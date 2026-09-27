"""Single-workspace operator routes and separately scoped collector invitations."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from .archives import archive_response
from .collections import CollectionRepository
from .models import CreateSession, StrictModel
from .repository import Conflict, NotFound
from .security import COOKIE


class Login(StrictModel):
    token: str = Field(min_length=1, max_length=512)


class Campaign(CreateSession):
    mode: Literal["live", "replay"] = "live"
    name: str = Field(min_length=1, max_length=120)
    target_pairs: int = Field(default=20, ge=1, le=100000)


class Invite(StrictModel):
    label: str = Field(default="", max_length=120)
    expires_in_hours: int = Field(default=72, ge=1, le=720)
    max_sessions: int = Field(default=20, ge=1, le=1000)


class InviteToken(StrictModel):
    token: str = Field(min_length=16, max_length=128)


class Join(InviteToken):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")


class Review(StrictModel):
    decision: Literal["approved", "rejected", "needs_revision"]
    expected_version: int = Field(ge=0)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")


class Release(StrictModel):
    pair_ids: list[str] = Field(min_length=1, max_length=10000)
    include_replay: bool = False
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")


class Export(StrictModel):
    pair_ids: list[str] = Field(min_length=1, max_length=1000)
    destination_id: Literal["configured"] = "configured"


class CampaignActivity(StrictModel):
    active: bool


def mount_operator(app, config, repo, media, auth, live_ready):
    collections = CollectionRepository(repo)
    app.state.collections = collections
    public = APIRouter(prefix="/api")
    operator = APIRouter(prefix="/api/operator", dependencies=[Depends(auth.require)])

    @public.get("/operator/session")
    def operator_session(request: Request):
        return {"authenticated": auth.valid(request.cookies.get(COOKIE)), "local_mode": auth.local(request)}

    @public.post("/operator/bootstrap")
    def bootstrap(request: Request):
        if not auth.local(request):
            raise HTTPException(
                403, detail={"code": "local_only", "message": "Sign in with the workspace token."}
            )
        return auth.response()

    @public.post("/operator/login")
    def login(body: Login, request: Request):
        auth.verify_login(body.token, request)
        return auth.response()

    @public.post("/operator/logout")
    def logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, path="/", secure=config.operator_cookie_secure, samesite="strict")
        return response

    @public.post("/invites/info")
    def invite_info(body: InviteToken):
        return collections.invite_info(body.token)

    @public.post("/invites/sessions", status_code=201)
    def join(body: Join):
        try:
            return collections.create_session_from_invite(
                body.token, body.idempotency_key, allow_new_live=live_ready()
            )
        except Conflict as error:
            if error.code == "live_disabled":
                raise HTTPException(503, detail={"code": error.code, "message": error.message}) from None
            raise

    def require_live(mode):
        if mode == "live" and not live_ready():
            raise HTTPException(
                503, detail={"code": "live_disabled", "message": "Cloud inference is not enabled."}
            )

    @operator.get("/campaigns")
    def list_campaigns():
        return {"campaigns": collections.list_campaigns()}

    @operator.get("/runtime")
    def runtime():
        return {
            "metrics": repo.metrics(),
            "inference": app.state.inference.cache_stats(),
            "workers": app.state.inference.worker_status(),
            "live_enabled": live_ready(),
            "inference_limit": config.inference_limit,
        }

    @operator.post("/campaigns", status_code=201)
    def create_campaign(body: Campaign):
        require_live(body.mode)
        return collections.create_campaign(**body.model_dump())

    @operator.get("/campaigns/{campaign_id}")
    def campaign(campaign_id: str):
        return collections.get_campaign(campaign_id)

    @operator.post("/campaigns/{campaign_id}/activity")
    def campaign_activity(campaign_id: str, body: CampaignActivity):
        return collections.set_campaign_active(campaign_id, body.active)

    @operator.get("/campaigns/{campaign_id}/sessions")
    def sessions(campaign_id: str):
        return {"sessions": collections.list_sessions(campaign_id)}

    @operator.get("/campaigns/{campaign_id}/pairs")
    def pairs(campaign_id: str, review_status: str | None = None):
        return {"pairs": collections.list_pairs(campaign_id, review_status)}

    @operator.get("/campaigns/{campaign_id}/invites")
    def invites(campaign_id: str):
        return {"invites": collections.list_invites(campaign_id)}

    @operator.post("/campaigns/{campaign_id}/invites", status_code=201)
    def create_invite(campaign_id: str, body: Invite):
        return collections.create_invite(campaign_id, **body.model_dump())

    @operator.post("/campaigns/{campaign_id}/invites/{invite_id}/revoke")
    def revoke_invite(campaign_id: str, invite_id: str):
        return collections.revoke_invite(campaign_id, invite_id)

    @operator.post("/campaigns/{campaign_id}/pairs/{pair_id}/review")
    def review(campaign_id: str, pair_id: str, body: Review):
        return collections.review_pair(
            campaign_id, pair_id, reviewer="workspace-operator", **body.model_dump()
        )

    @operator.get("/campaigns/{campaign_id}/pairs/{pair_id}/reviews")
    def reviews(campaign_id: str, pair_id: str):
        return {"reviews": collections.review_history(campaign_id, pair_id)}

    @operator.get("/campaigns/{campaign_id}/releases")
    def releases(campaign_id: str):
        return {"releases": collections.list_releases(campaign_id)}

    @operator.post("/campaigns/{campaign_id}/releases", status_code=201)
    def release(campaign_id: str, body: Release):
        return collections.export_manifests(campaign_id, **body.model_dump())

    @operator.get("/releases/{release_id}/manifest")
    def release_manifest(release_id: str):
        return JSONResponse(
            collections.get_release_by_id(release_id),
            headers={"Content-Disposition": 'attachment; filename="counterpart-dataset.json"'},
        )

    @operator.get("/releases/{release_id}/archive")
    def release_archive(release_id: str):
        return archive_response(
            collections.get_release_by_id(release_id),
            media,
            f"counterpart-dataset-{release_id[:8]}.zip",
            max_bytes=config.max_archive_bytes,
        )

    def image_response(asset_id):
        data = media.read(asset_id)
        return Response(
            data,
            media_type="image/png" if data.startswith(b"\x89PNG") else "image/jpeg",
            headers={"Cache-Control": "private, max-age=86400, immutable"},
        )

    @operator.get("/pairs/{pair_id}/assets/{asset_id}")
    def pair_asset(pair_id: str, asset_id: str):
        pair = collections.get_pair(pair_id)
        allowed = {
            f["asset_id"] for slot in ("baseline", "counterpart") for f in pair["manifest"][slot]["frames"]
        }
        if asset_id not in allowed:
            raise NotFound("Image not found in this pair")
        return image_response(asset_id)

    @operator.get("/sessions/{session_id}")
    def session(session_id: str):
        # No collector secret is stored or returned; operator cannot impersonate the collector.
        return {**repo.snapshot(session_id), "history": repo.evaluation_snapshot(session_id)["captures"]}

    @operator.get("/sessions/{session_id}/evaluation")
    def evaluation(session_id: str):
        return repo.evaluation_snapshot(session_id)

    @operator.get("/sessions/{session_id}/assets/{asset_id}")
    def session_asset(session_id: str, asset_id: str):
        captures = repo.evaluation_snapshot(session_id)["captures"]
        if asset_id not in {f["asset_id"] for capture in captures for f in capture["frames"]}:
            raise NotFound("Image not found in this session")
        return image_response(asset_id)

    @operator.get("/destination")
    def destination():
        from .destination import DestinationError

        try:
            result = app.state.destination.resolve()
            return {"enabled": True, **result, "privacy": result["visibility"]}
        except DestinationError as error:
            return {"enabled": False, "reason": error.code}

    @operator.get("/pairs/{pair_id}/export")
    def export_status(pair_id: str):
        from .export_admin import ExportAdmin

        return ExportAdmin(repo).status(pair_id)

    @operator.post("/pairs/{pair_id}/export/recheck")
    def export_recheck(pair_id: str):
        from .destination import DestinationError
        from .export_admin import ExportAdmin

        try:
            destination = app.state.destination.resolve(force=True)
        except DestinationError as error:
            raise HTTPException(
                409,
                detail={
                    "code": error.code,
                    "message": "The private destination must be available before rechecking.",
                },
            ) from None
        admin = ExportAdmin(repo)
        if admin.status(pair_id)["destination"] != destination:
            raise HTTPException(
                409,
                detail={
                    "code": "export_destination_mismatch",
                    "message": "Restore the original export destination before rechecking.",
                },
            )
        return admin.recheck(pair_id, "workspace-operator")

    @operator.post("/campaigns/{campaign_id}/exports", status_code=202)
    def export(campaign_id: str, body: Export):
        from .destination import DestinationError

        try:
            destination = app.state.destination.resolve(force=True)
        except DestinationError as error:
            raise HTTPException(
                409,
                detail={
                    "code": error.code,
                    "message": "A verified private Roboflow destination is required.",
                },
            ) from None
        return {
            "exports": collections.request_exports(
                campaign_id, body.pair_ids, destination, "workspace-operator"
            )
        }

    app.include_router(public)
    app.include_router(operator)
