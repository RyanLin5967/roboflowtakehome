"""Disk-spooled portable releases; media is never accumulated into one RAM buffer."""

import json
import tempfile
import zipfile
from collections.abc import Iterator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .dataset import examples
from .evidence_revisions import HUMAN_BASIS


def archive_response(
    manifest: dict, media, filename: str, *, max_bytes: int = 512 * 1024 * 1024
) -> StreamingResponse:
    release = manifest.get("schema_version") == "counterpart.dataset.v1"
    members = manifest["pairs"] if release else [{"manifest": manifest}]
    declared_assets = {
        frame["asset_id"]: frame["bytes"]
        for member in members
        for slot in ("baseline", "counterpart")
        for frame in member["manifest"][slot]["frames"]
    }
    if sum(declared_assets.values()) > max_bytes:
        raise HTTPException(
            413,
            detail={
                "code": "archive_too_large",
                "message": "Download limit exceeded. Release smaller groups of complete pairs.",
            },
        )
    # Bound memory at one decoded storage read plus a small spool; larger clips spill to disk.
    buffer = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, allow_nan=False))
            assets = set()
            with archive.open("examples.jsonl", "w") as rows:
                for member in members:
                    pair = member["manifest"]
                    for row in examples(pair):
                        if release:
                            row.update(
                                human_reviewed=True,
                                review=member["review"],
                                release_id=manifest["id"],
                                label_basis=(
                                    HUMAN_BASIS
                                    if pair.get("schema_version") == "counterpart.pair.v2"
                                    else "Machine evidence accepted in an explicit human review; "
                                    "review is not independent ground truth."
                                ),
                            )
                        rows.write((json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode())
                    assets.update(
                        frame["asset_id"]
                        for slot in ("baseline", "counterpart")
                        for frame in pair[slot]["frames"]
                    )
            for asset_id in sorted(assets):
                data = media.read(asset_id)
                if len(data) != declared_assets[asset_id]:
                    raise ValueError("Evidence size differs from its immutable capture record.")
                archive.writestr(f"assets/{asset_id}", data)
                if buffer.tell() > max_bytes:
                    raise HTTPException(
                        413,
                        detail={
                            "code": "archive_too_large",
                            "message": "Release smaller groups of complete pairs.",
                        },
                    )
        length = buffer.tell()
        if length > max_bytes:
            raise HTTPException(
                413,
                detail={"code": "archive_too_large", "message": "Release smaller groups of complete pairs."},
            )
        buffer.seek(0)
    except BaseException:
        buffer.close()
        raise

    def chunks() -> Iterator[bytes]:
        try:
            while data := buffer.read(64 * 1024):
                yield data
        finally:
            buffer.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Length": str(length)},
        background=BackgroundTask(buffer.close),
    )
