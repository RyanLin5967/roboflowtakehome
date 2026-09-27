"""Bounded evidence retention and consistent backup, coordinated with ingestion.

Content-addressing alone does not make deletion safe: a collector can reuse an
old orphan just before inserting its DB reference. Every ingestion path must
hold ``media_reference_guard`` across storage.put and its DB transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .storage import ASSET_ID

_MEDIA_LOCK = 1129334605
DEFAULT_RETENTION_SECONDS = 86400
MINIMUM_GRACE_SECONDS = 3600
MAX_BATCH = 1000


class MaintenanceBusy(RuntimeError):
    """A collector/maintenance operation currently owns the media barrier."""


class LifecycleError(RuntimeError):
    """Maintenance could not finish safely; exception text contains no DSN."""


@contextmanager
def media_reference_guard(dsn: str):
    """Shared cross-process guard for the complete media-write/reference interval.

    Do not hold this guard over a browser's upload transmission: enter it when
    the server has validated bytes and is about to persist them. A process crash
    closes its connection and PostgreSQL releases the lock automatically.
    """
    with psycopg.connect(dsn, autocommit=True) as connection:
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock_shared(hashtext(current_schema()),%s)", (_MEDIA_LOCK,)
        ).fetchone()[0]
        if not acquired:
            raise MaintenanceBusy("Evidence maintenance is active; retry this saved upload shortly.")
        try:
            yield
        finally:
            connection.execute(
                "SELECT pg_advisory_unlock_shared(hashtext(current_schema()),%s)", (_MEDIA_LOCK,)
            )


@contextmanager
def _maintenance_guard(dsn: str):
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock(hashtext(current_schema()),%s) AS acquired", (_MEDIA_LOCK,)
        ).fetchone()["acquired"]
        if not acquired:
            raise MaintenanceBusy("Media writes or another maintenance operation are active; retry later.")
        try:
            yield connection
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext(current_schema()),%s)", (_MEDIA_LOCK,))


def _limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_BATCH:
        raise ValueError(f"Use a maintenance batch between 1 and {MAX_BATCH} records.")


def _cursor(after_asset_id: str | None) -> None:
    if after_asset_id is not None and (
        not isinstance(after_asset_id, str) or not ASSET_ID.fullmatch(after_asset_id)
    ):
        raise ValueError("Use a valid asset cursor.")


def _cutoff(seconds: int, now: datetime | None) -> datetime:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not MINIMUM_GRACE_SECONDS <= seconds:
        raise ValueError("Retention/grace must be an integer of at least one hour.")
    now = datetime.now(UTC) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Use a timezone-aware maintenance time.")
    try:
        return now.astimezone(UTC) - timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("Retention/grace is out of range.") from exc


def _inventory(store, after_asset_id: str | None, limit: int) -> tuple[list[dict], bool]:
    _cursor(after_asset_id)
    rows: list[dict] = []
    previous = ""
    for row in store.iter_assets():
        asset_id, modified_at, size = row.get("asset_id"), row.get("modified_at"), row.get("bytes")
        if (
            not isinstance(asset_id, str)
            or not ASSET_ID.fullmatch(asset_id)
            or asset_id <= previous
            or not isinstance(modified_at, datetime)
            or modified_at.tzinfo is None
            or modified_at.utcoffset() is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise LifecycleError("Media inventory is invalid or not strictly ordered; nothing was deleted.")
        previous = asset_id
        if after_asset_id is not None and asset_id <= after_asset_id:
            continue
        if len(rows) == limit:
            return rows, True
        rows.append(row)
    return rows, False


def _referenced_assets(connection, asset_ids: list[str] | None = None) -> set[str]:
    if asset_ids == []:
        return set()
    # JSON paths include original/observed frames and nested result evidence.
    # Release manifests retain full pair copies and remain independent roots.
    rows = connection.execute(
        "WITH documents AS ("
        "SELECT frames AS doc FROM counterpart_captures UNION ALL "
        "SELECT observed_frames FROM counterpart_captures WHERE observed_frames IS NOT NULL UNION ALL "
        "SELECT result FROM counterpart_captures WHERE result IS NOT NULL UNION ALL "
        "SELECT manifest FROM counterpart_pairs UNION ALL "
        "SELECT manifest FROM counterpart_dataset_releases) "
        "SELECT DISTINCT asset #>> '{}' AS asset_id FROM documents "
        "CROSS JOIN LATERAL jsonb_path_query(doc,'$.**.asset_id') asset "
        + ("WHERE asset #>> '{}' = ANY(%s)" if asset_ids is not None else ""),
        (asset_ids,) if asset_ids is not None else (),
    ).fetchall()
    return {row["asset_id"] for row in rows}


def collect_orphan_media(
    dsn: str,
    store,
    *,
    dry_run: bool = True,
    grace_seconds: int = DEFAULT_RETENTION_SECONDS,
    limit: int = 500,
    after_asset_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Inspect one inventory page and optionally delete old unreferenced assets.

    The cursor is the last inspected digest, not the last deleted object. Follow
    it while ``has_more`` to avoid repeatedly scanning only a referenced prefix.
    Reset the cursor on the next complete sweep. Requires coordinated ingestion;
    direct object-store writers and independent deletion processes are excluded.
    """
    _limit(limit)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be an explicit boolean.")
    cutoff = _cutoff(grace_seconds, now)
    with _maintenance_guard(dsn) as connection:
        inventory, has_more = _inventory(store, after_asset_id, limit)
        old = [row for row in inventory if row["modified_at"] < cutoff]
        referenced = _referenced_assets(connection, [row["asset_id"] for row in old])
        candidates = [row for row in old if row["asset_id"] not in referenced]
        report = {
            "dry_run": dry_run,
            "cutoff": cutoff.isoformat(),
            "scanned": len(inventory),
            "protected_referenced": len(referenced),
            "protected_recent": len(inventory) - len(old),
            "candidate_assets": [row["asset_id"] for row in candidates],
            "candidate_bytes": sum(row["bytes"] for row in candidates),
            "deleted_assets": [],
            "delete_failed_assets": [],
            "next_cursor": inventory[-1]["asset_id"] if has_more and inventory else None,
            "has_more": has_more,
            "storage_semantics": "Current objects only; versioned storage may retain older billed versions.",
        }
        if dry_run:
            return report
        # One fresh batch recheck avoids a full JSON reference scan per object.
        # During the exclusive write barrier, publication/release can only add
        # roots for bytes already referenced by immutable capture records.
        rechecked = _referenced_assets(connection, [row["asset_id"] for row in candidates])
        for row in candidates:
            asset_id = row["asset_id"]
            if asset_id in rechecked:
                report["protected_referenced"] += 1
                continue
            try:
                removed = store.delete(asset_id)
            except (OSError, ValueError):
                report["delete_failed_assets"].append(asset_id)
            else:
                if removed:
                    report["deleted_assets"].append(asset_id)
        return report


_PREVIEW_ELIGIBLE = (
    "c.slot='preview' AND c.status IN ('accepted','rejected','failed','superseded') "
    "AND c.created_at<%s AND coalesce(c.completed_at,c.created_at)<%s "
    "AND c.id IS DISTINCT FROM s.baseline_id AND c.id IS DISTINCT FROM s.counterpart_id "
    "AND c.id IS DISTINCT FROM s.latest_capture_id AND c.id IS DISTINCT FROM s.feedback_capture_id "
    "AND NOT EXISTS (SELECT 1 FROM counterpart_jobs j WHERE j.capture_id=c.id "
    "AND j.status IN ('pending','running','retry_wait'))"
)


def prune_previews(
    dsn: str,
    *,
    dry_run: bool = True,
    retention_seconds: int = DEFAULT_RETENTION_SECONDS,
    limit: int = 500,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Remove only old terminal, unpointed preview rows and their terminal jobs.

    Committed attempts, rejected counterexamples and recovery captures survive.
    Preserve budget reservations and append an audit event for each session.
    Deleting rows does not itself delete blobs shared with other evidence.
    """
    _limit(limit)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be an explicit boolean.")
    cutoff = _cutoff(retention_seconds, now)
    selected: list[dict] = []
    with _maintenance_guard(dsn) as connection, connection.transaction():
        # Match repository lock order: session before captures/jobs. Avoid
        # blocking live worker transitions with SKIP LOCKED.
        sessions = connection.execute(
            "SELECT s.id FROM counterpart_sessions s WHERE EXISTS "
            "(SELECT 1 FROM counterpart_captures c WHERE c.session_id=s.id AND "
            + _PREVIEW_ELIGIBLE
            + ") ORDER BY s.created_at,s.id FOR UPDATE OF s SKIP LOCKED LIMIT %s",
            (cutoff, cutoff, limit),
        ).fetchall()
        for session in sessions:
            selected.extend(
                connection.execute(
                    "SELECT c.id,c.session_id FROM counterpart_captures c JOIN counterpart_sessions s "
                    "ON s.id=c.session_id WHERE s.id=%s AND "
                    + _PREVIEW_ELIGIBLE
                    + " ORDER BY c.created_at,c.id FOR UPDATE OF c SKIP LOCKED LIMIT %s",
                    (session["id"], cutoff, cutoff, limit - len(selected)),
                ).fetchall()
            )
            if len(selected) == limit:
                break
        ids = [str(row["id"]) for row in selected]
        if not dry_run and ids:
            connection.execute("DELETE FROM counterpart_jobs WHERE capture_id=ANY(%s::uuid[])", (ids,))
            connection.execute("DELETE FROM counterpart_captures WHERE id=ANY(%s::uuid[])", (ids,))
            per_session = defaultdict(list)
            for row in selected:
                per_session[row["session_id"]].append(str(row["id"]))
            for session_id, capture_ids in per_session.items():
                connection.execute(
                    "INSERT INTO counterpart_events(session_id,kind,data) VALUES (%s,%s,%s)",
                    (
                        session_id,
                        "previews_pruned",
                        Jsonb({"capture_ids": capture_ids, "cutoff": cutoff.isoformat()}),
                    ),
                )
    return {
        "dry_run": dry_run,
        "cutoff": cutoff.isoformat(),
        "candidate_capture_ids": ids,
        "deleted_capture_ids": [] if dry_run else ids,
        "limit_reached": len(ids) == limit,
    }


def run_maintenance(dsn: str, store, *, dry_run: bool = True, **options) -> dict[str, Any]:
    """CLI helper: prune disposable metadata, then collect unreferenced media."""
    preview_options = {key: options[key] for key in ("retention_seconds", "limit", "now") if key in options}
    media_options = {
        key: options[key] for key in ("grace_seconds", "limit", "after_asset_id", "now") if key in options
    }
    unknown = options.keys() - preview_options.keys() - media_options.keys()
    if unknown:
        raise ValueError("Unknown maintenance option.")
    # Validate the entire invocation before the first mutating phase.
    _limit(options.get("limit", 500))
    _cutoff(options.get("retention_seconds", DEFAULT_RETENTION_SECONDS), options.get("now"))
    _cutoff(options.get("grace_seconds", DEFAULT_RETENTION_SECONDS), options.get("now"))
    _cursor(options.get("after_asset_id"))
    return {
        "previews": prune_previews(dsn, dry_run=dry_run, **preview_options),
        "media": collect_orphan_media(dsn, store, dry_run=dry_run, **media_options),
    }


def _file_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def _write_private(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def postgres_tool_environment(dsn: str):
    """Private temporary libpq service, so command arguments never carry secrets."""
    try:
        parameters = conninfo_to_dict(dsn)
    except psycopg.Error:
        raise LifecycleError("Invalid database connection parameters for backup tools.") from None
    if any("\n" in value or "\r" in value for value in parameters.values()):
        raise LifecycleError("Multiline database connection parameters are unsupported for backup tools.")
    with tempfile.TemporaryDirectory(prefix="counterpart-pg-") as directory:
        service = Path(directory) / "pg_service.conf"
        contents = "[counterpart_maintenance]\n" + "".join(
            f"{key}={value}\n" for key, value in parameters.items()
        )
        _write_private(service, contents.encode())
        environment = os.environ.copy()
        environment["PGSERVICEFILE"] = str(service)
        environment["PGSERVICE"] = "counterpart_maintenance"
        # Explicit service selection overrides inherited database names/options.
        yield environment


def create_backup(dsn: str, store, destination: str | Path, *, pg_dump: str = "pg_dump") -> dict[str, Any]:
    """Create a real schema archive plus immutable bytes, never credentials.

    No automatic restore is offered: restoration is a deliberate offline
    operation, especially because rolling back budget ledgers needs independent
    reconciliation before cloud compute may resume.
    """
    destination = Path(destination).expanduser().resolve()
    if any((parent / ".git").exists() for parent in (destination, *destination.parents)):
        raise LifecycleError("Keep database and camera backups outside every source checkout.")
    with _maintenance_guard(dsn) as connection:
        schema = connection.execute("SELECT current_schema() AS name").fetchone()["name"]
        if not schema:
            raise LifecycleError("Select an application schema before creating a backup.")
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        incomplete = destination / ".incomplete"
        _write_private(incomplete, b"Backup not complete. Do not restore as a complete evidence bundle.\n")
        archive = destination / "database.dump"
        _write_private(archive, b"")
        # pg_dump interprets schema arguments as patterns, even without shell
        # expansion. Double quoting makes this one exact schema identifier.
        schema_pattern = '"' + schema.replace('"', '""') + '"'
        try:
            with postgres_tool_environment(dsn) as environment:
                process = subprocess.run(
                    [
                        pg_dump,
                        "--dbname=service=counterpart_maintenance",
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                        f"--schema={schema_pattern}",
                        f"--file={archive}",
                    ],
                    env=environment,
                    capture_output=True,
                    timeout=300,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            raise LifecycleError("Database backup did not finish; the bundle remains incomplete.") from None
        if process.returncode != 0:
            raise LifecycleError("Database backup failed; the bundle remains incomplete.")
        os.chmod(archive, 0o600)
        with archive.open("rb") as stream:
            os.fsync(stream.fileno())
        assets_dir = destination / "assets"
        assets_dir.mkdir(mode=0o700)
        assets = []
        previous = ""
        for record in store.iter_assets():
            asset_id = record.get("asset_id")
            if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id) or asset_id <= previous:
                raise LifecycleError("Media inventory changed or is invalid; the bundle remains incomplete.")
            previous = asset_id
            data = store.read(asset_id)
            if hashlib.sha256(data).hexdigest() != asset_id:
                raise LifecycleError("Evidence failed its integrity check; the bundle remains incomplete.")
            _write_private(assets_dir / asset_id, data)
            assets.append({"asset_id": asset_id, "bytes": len(data), "sha256": asset_id})
        stored = {row["asset_id"] for row in assets}
        if not _referenced_assets(connection).issubset(stored):
            raise LifecycleError("Referenced media is missing; the bundle remains incomplete.")
        assets_directory = os.open(assets_dir, os.O_RDONLY)
        try:
            os.fsync(assets_directory)
        finally:
            os.close(assets_directory)
        manifest = {
            "schema_version": "counterpart.backup/1",
            "created_at": datetime.now(UTC).isoformat(),
            "source_schema": schema,
            "database": {"path": "database.dump", "sha256": _file_sha256(archive)},
            "assets": assets,
            "restore_policy": "Offline restore; reconcile provider usage independently before live compute.",
        }
        _write_private(
            destination / "backup.json",
            json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n",
        )
        incomplete.unlink()
        directory = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return manifest
