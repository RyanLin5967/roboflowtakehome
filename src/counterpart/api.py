"""Capability-protected capture API. Model calls run in a separate worker."""

import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .archives import archive_response
from .config import Settings, settings
from .lifecycle import MaintenanceBusy
from .models import CaptureMetadata, CreateSession, Publish, Replay, Resume, RetryCapture
from .object_storage import MediaUnavailable, create_media_store
from .operator_api import mount_operator
from .recipes import recipe_catalogue
from .repository import CapacityExceeded, Conflict, NotFound, Repository
from .security import OperatorAuth, OriginGuard
from .storage import InvalidImage


def fingerprint(metadata: dict, frames: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps({"metadata": metadata, "frames": frames}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class BoundedRequest:
    """Bound the actual streamed body, including chunked requests, before parsing multipart."""

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body = message.get("body", b"")
                size += len(body)
                if size > self.limit:
                    return await JSONResponse(
                        {
                            "detail": {
                                "code": "body_too_large",
                                "message": "This capture is too large. Use a shorter clip or smaller frames.",
                            }
                        },
                        status_code=413,
                    )(scope, receive, send)
                spool.write(body)
                if not message.get("more_body", False):
                    break
            spool.seek(0)
            sent = False

            async def bounded_receive():
                nonlocal sent
                if not sent:
                    body = spool.read(64 * 1024)
                    sent = spool.tell() >= size
                    return {"type": "http.request", "body": body, "more_body": not sent}
                return await receive()

            await self.app(scope, bounded_receive, send)


def create_app(config: Settings | None = None, repository: Repository | None = None) -> FastAPI:
    config = config or settings()
    repo = repository or Repository(config.database_url)
    media = create_media_store(config)
    auth = OperatorAuth(config)
    from .destination import DestinationResolver
    from .inference_runtime import InferenceRuntime

    inference = InferenceRuntime(repo, media, config, detector=None)
    destination = DestinationResolver(config)

    @asynccontextmanager
    async def lifespan(app):
        repo.initialize()
        inference.initialize()
        try:
            yield
        finally:
            destination.close()
            if hasattr(media, "close"):
                media.close()
            if hasattr(repo, "close"):
                repo.close()

    app = FastAPI(title="Counterpart", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BoundedRequest, limit=config.max_upload_bytes)
    app.add_middleware(OriginGuard, allowed_origins=config.allowed_origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.trusted_hosts)
    app.state.destination = destination
    app.state.operator_auth = auth
    app.state.inference = inference
    app.state.repository = repo
    app.state.media = media

    @app.exception_handler(Conflict)
    async def conflict_handler(_request, exc):
        return JSONResponse(
            {
                "detail": {
                    "code": getattr(exc, "code", "conflict"),
                    "message": getattr(exc, "message", str(exc)),
                }
            },
            status_code=409,
        )

    @app.exception_handler(NotFound)
    async def not_found_handler(_request, _exc):
        return JSONResponse(
            {"detail": {"code": "not_found", "message": "Session or capture not found."}}, status_code=404
        )

    @app.exception_handler(CapacityExceeded)
    async def capacity_handler(_request, _exc):
        return JSONResponse(
            {"detail": {"code": "capacity", "message": "The collection queue is full. Retry shortly."}},
            status_code=429,
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(InvalidImage)
    async def image_handler(_request, exc):
        return JSONResponse({"detail": {"code": "invalid_image", "message": str(exc)}}, status_code=422)

    @app.exception_handler(MediaUnavailable)
    async def media_unavailable_handler(_request, _exc):
        return JSONResponse(
            {
                "detail": {
                    "code": "storage_unavailable",
                    "message": "Evidence storage is temporarily unavailable. Retry shortly.",
                }
            },
            status_code=503,
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(MaintenanceBusy)
    async def maintenance_handler(_request, _exc):
        return JSONResponse(
            {
                "detail": {
                    "code": "maintenance_busy",
                    "message": "Storage maintenance is running. Retry this saved upload shortly.",
                }
            },
            status_code=503,
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(FileNotFoundError)
    async def missing_media_handler(_request, _exc):
        return JSONResponse(
            {"detail": {"code": "missing_evidence", "message": "The recorded image is unavailable."}},
            status_code=404,
        )

    def authorized(session_id: str, authorization: Annotated[str | None, Header()] = None):
        token = authorization.removeprefix("Bearer ") if authorization else ""
        if not token or not repo.authenticate(session_id, token):
            raise HTTPException(404, detail={"code": "not_found", "message": "Session not found."})
        return repo.snapshot(session_id)

    Session = Annotated[dict, Depends(authorized)]

    def live_ready() -> bool:
        return config.live_enabled and bool(config.credentials()["ROBOFLOW_API_KEY"].get_secret_value())

    @app.get("/api/health")
    def health():
        counters = repo.metrics()
        return {
            "status": "ok",
            "live_enabled": live_ready(),
            "provider": "roboflow",
            "model_id": config.roboflow_model,
            "replay_available": True,
            "inference_limit": config.inference_limit,
            "export_enabled": config.export_enabled and bool(config.export_project),
            **({"metrics": counters} if not config.require_operator else {}),
        }

    @app.get("/api/recipes")
    def recipes():
        return {
            "recipes": recipe_catalogue(),
            "limits": {"max_frames": 48, "max_duration_ms": 12000, "fps": 4, "max_gap_ms": 600},
        }

    mount_operator(app, config, repo, media, auth, live_ready)

    @app.post("/api/sessions", status_code=201)
    def create(body: CreateSession, request: Request):
        if config.require_operator:
            auth.require(request)
        if body.mode == "live" and not live_ready():
            raise HTTPException(
                503,
                detail={
                    "code": "live_disabled",
                    "message": "Cloud inference is not enabled. Replay is available without model calls.",
                },
            )
        return repo.create_session(body.recipe, body.mode, body.object_a, body.object_b)

    @app.get("/api/sessions/{session_id}")
    def snapshot(session_id: str, session: Session):
        capture = session.get("latest_capture")
        if capture and capture["status"] in {"queued", "running", "pending", "processing"}:
            progress = inference.progress(capture["frames"])
            session["progress"] = {**progress, "completed_frames": progress["observed_frames"]}
        return session

    @app.post("/api/sessions/{session_id}/retry", status_code=202)
    def retry(session_id: str, body: RetryCapture, session: Session):
        return repo.retry_capture(session_id, **body.model_dump())

    @app.post("/api/sessions/{session_id}/resume")
    def resume(session_id: str, body: Resume, session: Session):
        return repo.resume(session_id, body.expected_epoch)

    @app.post("/api/sessions/{session_id}/captures", status_code=202)
    def capture(
        session_id: str,
        session: Session,
        metadata: Annotated[str, Form()],
        frames: Annotated[list[UploadFile], File()],
    ):
        if session["mode"] != "live":
            raise HTTPException(
                409, detail={"code": "replay_only", "message": "Use the replay controls for this session."}
            )
        if not live_ready():
            raise HTTPException(
                503, detail={"code": "live_disabled", "message": "Cloud inference is disabled."}
            )
        try:
            parsed = CaptureMetadata.model_validate_json(metadata)
        except ValidationError:
            raise HTTPException(
                422,
                detail={"code": "invalid_metadata", "message": "Invalid capture metadata or frame times."},
            ) from None
        if len(frames) != len(parsed.timestamps_ms):
            raise HTTPException(
                422, detail={"code": "frame_count", "message": "Every frame needs a matching timestamp."}
            )
        count = len(frames)
        is_sequence = session["recipe"] == "temporal" and parsed.slot in ("baseline", "counterpart")
        if (is_sequence and not 8 <= count <= 48) or (not is_sequence and count != 1):
            raise HTTPException(
                422, detail={"code": "frame_count", "message": "Use one photo or 8–48 temporal frames."}
            )
        from .lifecycle import media_reference_guard

        repo.check_new_capture_admission(session_id, parsed.idempotency_key)
        with media_reference_guard(repo.dsn):
            stored = []
            for upload, t_ms in zip(frames, parsed.timestamps_ms, strict=True):
                data = upload.file.read(config.max_asset_bytes + 1)
                stored.append({**media.put(data), "t_ms": t_ms})
            if len({(f["width"], f["height"]) for f in stored}) != 1:
                raise HTTPException(
                    422,
                    detail={
                        "code": "frame_dimensions",
                        "message": "Keep the camera resolution fixed throughout a clip.",
                    },
                )
            meta = parsed.model_dump()
            return repo.submit(session_id, meta, stored, fingerprint(meta, stored))

    @app.post("/api/sessions/{session_id}/replay", status_code=202)
    def replay(session_id: str, body: Replay, session: Session):
        if session["mode"] != "replay":
            raise HTTPException(
                409,
                detail={"code": "live_only", "message": "Synthetic replay is separate from live captures."},
            )
        from .fixtures import generate
        from .lifecycle import media_reference_guard

        repo.check_new_capture_admission(session_id, body.idempotency_key)
        with media_reference_guard(repo.dsn):
            stored = []
            for frame in generate(session["recipe"], body.scenario):
                stored.append(
                    {
                        **media.put(frame["bytes"]),
                        "t_ms": frame["t_ms"],
                        "fixture_detections": frame["detections"],
                    }
                )
            slot = body.scenario if body.scenario in ("baseline", "recovery") else "counterpart"
            meta = {
                "slot": slot,
                "expected_revision": body.expected_revision,
                "epoch": body.epoch,
                "client_seq": body.expected_revision,
                "idempotency_key": body.idempotency_key,
                "timestamps_ms": [f["t_ms"] for f in stored],
                "interrupted": False,
            }
            return repo.submit(session_id, meta, stored, fingerprint(meta, stored))

    @app.post("/api/sessions/{session_id}/publish")
    def publish(session_id: str, body: Publish, session: Session):
        pair = repo.publish(session_id, body.expected_revision)
        return {"pair": pair, "session": repo.snapshot(session_id)}

    def published(session: dict) -> dict:
        if not session.get("pair"):
            raise HTTPException(
                409, detail={"code": "not_published", "message": "Finish and save the pair first."}
            )
        return session["pair"]["manifest"]

    @app.get("/api/sessions/{session_id}/manifest")
    def manifest(session_id: str, session: Session):
        return JSONResponse(
            published(session), headers={"Content-Disposition": 'attachment; filename="counterpart.json"'}
        )

    @app.get("/api/sessions/{session_id}/archive")
    def archive(session_id: str, session: Session):
        return archive_response(
            published(session), media, f"counterpart-{session_id[:8]}.zip", max_bytes=config.max_archive_bytes
        )

    @app.get("/api/sessions/{session_id}/assets/{asset_id}")
    def asset(session_id: str, asset_id: str, session: Session):
        allowed = {
            f["asset_id"]
            for name in ("baseline", "counterpart", "latest_capture")
            for f in (session.get(name) or {}).get("frames", [])
        }
        # Published manifests may outlive the current UI's last-capture pointer.
        if session.get("pair"):
            allowed |= {
                f["asset_id"]
                for name in ("baseline", "counterpart")
                for f in session["pair"]["manifest"][name]["frames"]
            }
        if asset_id not in allowed:
            raise HTTPException(404, detail={"code": "not_found", "message": "Image not found."})
        data = media.read(asset_id)
        return Response(
            data,
            media_type="image/png" if data.startswith(b"\x89PNG") else "image/jpeg",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    if config.web_dir.is_dir() and (config.web_dir / "index.html").exists():
        if (config.web_dir / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=config.web_dir / "assets"), name="static-assets")

        @app.get("/{path:path}")
        def index(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(Path(config.web_dir) / "index.html", headers={"Cache-Control": "no-cache"})

    return app
