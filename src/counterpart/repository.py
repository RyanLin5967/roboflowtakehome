"""Transactional capture state and fenced work dispatch.

Cloud calls never belong inside this module. A lease fences local acceptance;
it does not promise exactly-once remote execution. Disposable previews and
immutable committed evidence have deliberately different freshness rules.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .splits import split_for_session

MAX_ATTEMPTS = 3
MAX_PENDING_JOBS = 128
MAX_RUNNING_JOBS = 4
PREVIEW_MAX_AGE_SECONDS = 10
COMMITTED_SLOTS = {"baseline", "counterpart"}


class Conflict(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


class NotFound(Exception):
    def __init__(self, message: str = "Session not found"):
        self.code, self.message = "not_found", message
        super().__init__(message)


class CapacityExceeded(Exception):
    def __init__(self, message: str = "Collection is busy. Try again shortly."):
        self.code, self.message = "capacity_exceeded", message
        super().__init__(message)


def _json(value: Any) -> Any:
    """Return ordinary JSON types without leaking driver-specific values."""
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _code(value: str) -> str:
    # The caller supplies a code, never provider exception text/URLs/headers.
    return value if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else "worker_error"


class Repository:
    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def _connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            yield connection

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(780245190220)")
            connection.execute(Path(__file__).with_name("schema.sql").read_text())

    @staticmethod
    def _session(connection, session_id: str, *, lock: bool = False) -> dict:
        try:
            session_id = str(UUID(str(session_id)))
        except (ValueError, TypeError, AttributeError) as error:
            raise NotFound("Session not found") from error
        row = connection.execute(
            "SELECT * FROM counterpart_sessions WHERE id = %s" + (" FOR UPDATE" if lock else ""),
            (session_id,),
        ).fetchone()
        if row is None:
            raise NotFound("Session not found")
        return row

    @staticmethod
    def _event(connection, session_id: str, kind: str, **data) -> None:
        connection.execute(
            "INSERT INTO counterpart_events (session_id, kind, data) VALUES (%s, %s, %s)",
            (session_id, kind, Jsonb(_json(data))),
        )

    @staticmethod
    def _capture(connection, capture_id: str | UUID | None, *, internal: bool = False) -> dict | None:
        if capture_id is None:
            return None
        row = connection.execute("SELECT * FROM counterpart_captures WHERE id = %s", (capture_id,)).fetchone()
        if row is None:
            raise NotFound("Capture not found")
        frames = row["observed_frames"] if row["observed_frames"] is not None else row["frames"]
        if not internal:
            frames = [{k: v for k, v in frame.items() if k != "fixture_detections"} for frame in frames]
        return _json(
            {
                "id": row["id"],
                "slot": row["slot"],
                "status": row["status"],
                "revision": row["revision"],
                "epoch": row["epoch"],
                "client_seq": row["client_seq"],
                "created_at": row["created_at"],
                "frames": frames,
                "result": row["result"],
                "error": row["error"],
                "interrupted": row["interrupted"],
            }
        )

    def _snapshot(self, connection, session_id: str, *, internal: bool = False) -> dict:
        row = self._session(connection, session_id)
        result = {
            k: row[k]
            for k in (
                "id",
                "recipe",
                "mode",
                "object_a",
                "object_b",
                "revision",
                "epoch",
                "state",
                "recovery_required",
                "created_at",
            )
        }
        for field, key in (
            ("baseline", "baseline_id"),
            ("counterpart", "counterpart_id"),
            ("latest_capture", "latest_capture_id"),
            ("feedback", "feedback_capture_id"),
        ):
            result[field] = self._capture(connection, row[key], internal=internal)
        if result["feedback"] is not None:
            fresh = connection.execute(
                "SELECT created_at > clock_timestamp() - (%s * interval '1 second') AS fresh "
                "FROM counterpart_captures WHERE id = %s",
                (PREVIEW_MAX_AGE_SECONDS, row["feedback_capture_id"]),
            ).fetchone()["fresh"]
            if not fresh:
                result["feedback"] = None
        result["pair"] = connection.execute(
            "SELECT id, session_id, manifest, export_status, created_at FROM counterpart_pairs WHERE "
            "session_id = %s",
            (session_id,),
        ).fetchone()
        events = connection.execute(
            "SELECT id, kind, data, created_at FROM counterpart_events WHERE session_id = %s "
            "ORDER BY id DESC LIMIT 50",
            (session_id,),
        ).fetchall()
        result["events"] = list(reversed(events))
        return _json(result)

    def create_session(self, recipe: str, mode: str, object_a: str, object_b: str) -> dict:
        if recipe not in {"spatial", "temporal"} or mode not in {"live", "replay"}:
            raise Conflict("invalid_session", "Unsupported recipe or collection mode")
        if not object_a or not object_b or object_a == object_b:
            raise Conflict("invalid_objects", "Choose two distinguishable object classes")
        session_id, token = str(uuid4()), secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO counterpart_sessions (id, capability_hash, recipe, mode, object_a, object_b) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (session_id, digest, recipe, mode, object_a, object_b),
            )
            self._event(connection, session_id, "session_created", mode=mode)
            return {"token": token, "session": self._snapshot(connection, session_id)}

    def authenticate(self, session_id: str, token: str) -> bool:
        if not isinstance(token, str):
            return False
        with self._connection() as connection:
            try:
                session = self._session(connection, session_id)
            except NotFound:
                return False
            return hmac.compare_digest(session["capability_hash"], hashlib.sha256(token.encode()).hexdigest())

    def snapshot(self, session_id: str) -> dict:
        with self._connection() as connection:
            # A snapshot must not combine pre- and post-transition capture state.
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return self._snapshot(connection, session_id)

    def resume(self, session_id: str, expected_epoch: int) -> dict:
        with self._connection() as connection:
            session = self._session(connection, session_id, lock=True)
            if expected_epoch > session["epoch"] or expected_epoch < 0:
                raise Conflict("stale_epoch", "Reload the current session before resuming")
            if expected_epoch == session["epoch"]:
                connection.execute(
                    "UPDATE counterpart_sessions SET epoch = epoch + 1, feedback_capture_id = NULL, "
                    "feedback_seq = -1, latest_preview_seq = -1, recovery_required = %s WHERE id = %s",
                    (
                        session["baseline_id"] is not None and session["state"] not in {"ready", "published"},
                        session_id,
                    ),
                )
                self._supersede(connection, session_id, ("preview", "recovery"))
                self._event(connection, session_id, "session_resumed", epoch=session["epoch"] + 1)
            return self._snapshot(connection, session_id)

    @staticmethod
    def _supersede(connection, session_id: str, slots: tuple[str, ...], *, pending_only: bool = False):
        condition = (
            "AND j.status IN ('pending', 'retry_wait')"
            if pending_only
            else ("AND j.status IN ('pending', 'retry_wait', 'running')")
        )
        rows = connection.execute(
            "UPDATE counterpart_jobs j SET status = 'superseded', lease_token = NULL "
            "FROM counterpart_captures c WHERE j.capture_id = c.id AND j.session_id = %s "
            "AND c.slot = ANY(%s) " + condition + " RETURNING j.capture_id",
            (session_id, list(slots)),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE counterpart_captures SET status = 'superseded', completed_at = clock_timestamp() "
                "WHERE id = %s",
                (row["capture_id"],),
            )

    def submit(self, session_id: str, metadata: dict, frames: list[dict], fingerprint: str) -> dict:
        with self._connection() as connection:
            session = self._session(connection, session_id, lock=True)
            previous = connection.execute(
                "SELECT id, fingerprint FROM counterpart_captures WHERE session_id = %s AND "
                "idempotency_key = %s",
                (session_id, metadata["idempotency_key"]),
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise Conflict("idempotency_mismatch", "This retry key belongs to a different capture")
                return {
                    "capture": self._capture(connection, previous["id"]),
                    "session": self._snapshot(connection, session_id),
                }
            if session["state"] == "published":
                raise Conflict("already_published", "This pair is already published")
            if metadata["expected_revision"] != session["revision"]:
                raise Conflict("stale_revision", "A newer capture already changed this session")
            if metadata["epoch"] != session["epoch"]:
                raise Conflict("stale_epoch", "Reconnect before submitting another observation")
            slot = metadata["slot"]
            if slot not in {"baseline", "counterpart", "preview", "recovery"}:
                raise Conflict("invalid_slot", "Unknown capture slot")
            if slot in COMMITTED_SLOTS and session["recovery_required"]:
                raise Conflict("recovery_required", "Check the scene again before continuing")
            if slot in {"counterpart", "recovery"} and session["baseline_id"] is None:
                raise Conflict("baseline_required", "Capture an accepted first observation first")
            if slot == "preview" and metadata.get("client_seq", 0) <= session["latest_preview_seq"]:
                raise Conflict("stale_preview", "This preview has already been replaced")
            # Serialize only admission accounting, not processing, across sessions.
            connection.execute("SELECT pg_advisory_xact_lock(780245190221)")
            pending = connection.execute(
                "SELECT count(*) AS n FROM counterpart_jobs WHERE status IN "
                "('pending','running','retry_wait')"
            ).fetchone()["n"]
            if pending >= MAX_PENDING_JOBS:
                replaceable = connection.execute(
                    "SELECT count(*) AS n FROM counterpart_jobs j JOIN counterpart_captures c ON "
                    "c.id=j.capture_id "
                    "WHERE j.session_id=%s AND j.status IN ('pending','retry_wait') AND c.slot=%s",
                    (session_id, slot),
                ).fetchone()["n"]
                if not replaceable:
                    raise CapacityExceeded()
            revision = session["revision"] + (1 if slot in COMMITTED_SLOTS else 0)
            if slot in COMMITTED_SLOTS:
                self._supersede(connection, session_id, ("baseline", "counterpart", "recovery"))
            elif slot in {"preview", "recovery"}:
                self._supersede(connection, session_id, (slot,), pending_only=slot == "preview")
            capture_id, job_id = str(uuid4()), str(uuid4())
            connection.execute(
                "INSERT INTO counterpart_captures "
                "(id, session_id, idempotency_key, fingerprint, slot, revision, epoch, client_seq, "
                "frames, interrupted) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    capture_id,
                    session_id,
                    metadata["idempotency_key"],
                    fingerprint,
                    slot,
                    revision,
                    session["epoch"],
                    metadata.get("client_seq", 0),
                    Jsonb(frames),
                    metadata.get("interrupted", False),
                ),
            )
            connection.execute(
                "INSERT INTO counterpart_jobs (id,capture_id,session_id) VALUES (%s,%s,%s)",
                (job_id, capture_id, session_id),
            )
            if slot in COMMITTED_SLOTS:
                if slot == "baseline":
                    connection.execute(
                        "UPDATE counterpart_sessions SET revision=%s,latest_capture_id=%s,baseline_id=NULL, "
                        "counterpart_id=NULL,state='collecting' WHERE id=%s",
                        (revision, capture_id, session_id),
                    )
                else:
                    connection.execute(
                        "UPDATE counterpart_sessions SET revision=%s,latest_capture_id=%s, "
                        "counterpart_id=NULL,state='baseline_ready' WHERE id=%s",
                        (revision, capture_id, session_id),
                    )
                self._supersede(connection, session_id, ("preview",), pending_only=True)
            elif slot == "preview":
                connection.execute(
                    "UPDATE counterpart_sessions SET latest_preview_seq=%s WHERE id=%s",
                    (metadata.get("client_seq", 0), session_id),
                )
            else:
                connection.execute(
                    "UPDATE counterpart_sessions SET latest_capture_id=%s WHERE id=%s",
                    (capture_id, session_id),
                )
            self._event(connection, session_id, "capture_submitted", capture_id=capture_id, slot=slot)
            return {
                "capture": self._capture(connection, capture_id),
                "session": self._snapshot(connection, session_id),
            }

    def claim(self, worker_id: str, lease_seconds: float = 90) -> dict | None:
        self._validate_lease(lease_seconds)
        with self._connection() as connection:
            # Coordinate a bound on active leases across every worker process.
            # Expired remote calls may still be running; this is not provider cancellation.
            connection.execute("SELECT pg_advisory_xact_lock(780245190222)")
            running = connection.execute(
                "SELECT count(*) AS count FROM counterpart_jobs WHERE status='running' "
                "AND lease_expires_at>clock_timestamp()"
            ).fetchone()["count"]
            if running >= MAX_RUNNING_JOBS:
                return None
            # Lock the session before its job, matching submit/finish/resume order.
            sessions = connection.execute(
                "SELECT s.id FROM counterpart_sessions s WHERE EXISTS ("
                "SELECT 1 FROM counterpart_jobs j JOIN counterpart_captures c ON c.id=j.capture_id "
                "WHERE j.session_id=s.id AND "
                "((j.status IN ('pending','retry_wait') AND j.available_at<=clock_timestamp()) OR "
                "(j.status='running' AND j.lease_expires_at<=clock_timestamp())) "
                "AND (c.slot!='preview' OR NOT EXISTS (SELECT 1 FROM counterpart_jobs a "
                "JOIN counterpart_captures ac ON ac.id=a.capture_id WHERE a.session_id=s.id "
                "AND ac.slot='preview' AND a.status='running' AND a.lease_expires_at>clock_timestamp()))) "
                "ORDER BY CASE WHEN EXISTS (SELECT 1 FROM counterpart_jobs j JOIN counterpart_captures c "
                "ON c.id=j.capture_id WHERE j.session_id=s.id AND c.slot IN "
                "('baseline','counterpart','recovery') "
                "AND ((j.status IN ('pending','retry_wait') AND j.available_at<=clock_timestamp()) OR "
                "(j.status='running' AND j.lease_expires_at<=clock_timestamp()))) THEN 0 ELSE 1 END, "
                "s.preview_served_at, s.created_at, s.id FOR UPDATE OF s SKIP LOCKED LIMIT 32"
            ).fetchall()
            for session in sessions:
                # Obsolete preview work is discarded before spending inference units.
                obsolete = connection.execute(
                    "UPDATE counterpart_jobs j SET status='superseded',lease_token=NULL "
                    "FROM counterpart_captures c,counterpart_sessions s WHERE j.capture_id=c.id "
                    "AND j.session_id=s.id AND s.id=%s AND c.slot='preview' "
                    "AND (j.status IN ('pending','retry_wait') OR "
                    "(j.status='running' AND j.lease_expires_at<=clock_timestamp())) "
                    "AND (c.epoch!=s.epoch OR c.revision!=s.revision OR "
                    "c.created_at<=clock_timestamp()-(%s*interval '1 second') OR "
                    "c.client_seq<s.latest_preview_seq) RETURNING j.capture_id",
                    (session["id"], PREVIEW_MAX_AGE_SECONDS),
                ).fetchall()
                for row in obsolete:
                    connection.execute(
                        "UPDATE counterpart_captures SET "
                        "status='superseded',completed_at=clock_timestamp() WHERE id=%s",
                        (row["capture_id"],),
                    )
                expired = connection.execute(
                    "UPDATE counterpart_jobs SET "
                    "status='failed',lease_token=NULL,last_error='attempts_exhausted' "
                    "WHERE session_id=%s AND status='running' AND lease_expires_at<=clock_timestamp() "
                    "AND attempt>=%s RETURNING capture_id",
                    (session["id"], MAX_ATTEMPTS),
                ).fetchall()
                for row in expired:
                    connection.execute(
                        "UPDATE counterpart_captures SET status='failed',error='attempts_exhausted', "
                        "completed_at=clock_timestamp() WHERE id=%s",
                        (row["capture_id"],),
                    )
                    self._event(connection, session["id"], "capture_failed", code="attempts_exhausted")
                job = connection.execute(
                    "SELECT j.*,c.slot FROM counterpart_jobs j JOIN counterpart_captures c ON "
                    "c.id=j.capture_id "
                    "WHERE j.session_id=%s AND j.attempt<%s AND "
                    "((j.status IN ('pending','retry_wait') AND j.available_at<=clock_timestamp()) OR "
                    "(j.status='running' AND j.lease_expires_at<=clock_timestamp())) "
                    "AND (c.slot!='preview' OR NOT EXISTS (SELECT 1 FROM counterpart_jobs a "
                    "JOIN counterpart_captures ac ON ac.id=a.capture_id WHERE a.session_id=j.session_id "
                    "AND ac.slot='preview' AND a.status='running' AND a.lease_expires_at>clock_timestamp())) "
                    "ORDER BY CASE c.slot WHEN 'baseline' THEN 0 WHEN 'counterpart' THEN 0 "
                    "WHEN 'recovery' THEN 1 ELSE 2 END, j.created_at,j.id FOR UPDATE OF j SKIP "
                    "LOCKED LIMIT 1",
                    (session["id"], MAX_ATTEMPTS),
                ).fetchone()
                if job is None:
                    continue
                token = str(uuid4())
                connection.execute(
                    "UPDATE counterpart_jobs SET "
                    "status='running',attempt=attempt+1,lease_token=%s,worker_id=%s, "
                    "lease_expires_at=clock_timestamp()+(%s*interval '1 second') WHERE id=%s",
                    (token, worker_id, lease_seconds, job["id"]),
                )
                connection.execute(
                    "UPDATE counterpart_captures SET status='running' WHERE id=%s", (job["capture_id"],)
                )
                if job["slot"] == "preview":
                    connection.execute(
                        "UPDATE counterpart_sessions SET preview_served_at=clock_timestamp() WHERE id=%s",
                        (session["id"],),
                    )
                self._event(
                    connection,
                    session["id"],
                    "verification_started",
                    capture_id=job["capture_id"],
                    attempt=job["attempt"] + 1,
                )
                return _json(
                    {
                        "id": job["id"],
                        "lease_token": token,
                        "attempt": job["attempt"] + 1,
                        "capture": self._capture(connection, job["capture_id"], internal=True),
                        "session": self._snapshot(connection, str(session["id"]), internal=True),
                    }
                )
            return None

    @staticmethod
    def _validate_lease(lease_seconds):
        if (
            not isinstance(lease_seconds, (int, float))
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("Lease duration must be finite and positive")

    def heartbeat(self, job_id: str, lease_token: str, lease_seconds: float = 90) -> bool:
        self._validate_lease(lease_seconds)
        with self._connection() as connection:
            return (
                connection.execute(
                    "UPDATE counterpart_jobs SET lease_expires_at=clock_timestamp()+(%s*interval '1 second') "
                    "WHERE id=%s AND lease_token=%s AND status='running' AND "
                    "lease_expires_at>clock_timestamp() RETURNING id",
                    (lease_seconds, job_id, lease_token),
                ).fetchone()
                is not None
            )

    def _locked_job(self, connection, job_id: str, lease_token: str):
        identity = connection.execute(
            "SELECT session_id FROM counterpart_jobs WHERE id=%s", (job_id,)
        ).fetchone()
        if identity is None:
            return None, None
        session = self._session(connection, str(identity["session_id"]), lock=True)
        job = connection.execute(
            "SELECT *,lease_expires_at>clock_timestamp() AS lease_valid FROM counterpart_jobs WHERE "
            "id=%s FOR UPDATE",
            (job_id,),
        ).fetchone()
        if (
            job["status"] != "running"
            or str(job["lease_token"]) != str(lease_token)
            or not job["lease_valid"]
        ):
            self._event(connection, session["id"], "stale_result_ignored", job_id=job_id, reason="lease")
            return None, session
        return job, session

    def finish(self, job_id: str, lease_token: str, result: dict, frames: list[dict]) -> bool:
        if not isinstance(result.get("accepted"), bool):
            raise ValueError("A verification result must contain an explicit boolean decision")
        with self._connection() as connection:
            job, session = self._locked_job(connection, job_id, lease_token)
            if job is None:
                return False
            capture = connection.execute(
                "SELECT * FROM counterpart_captures WHERE id=%s", (job["capture_id"],)
            ).fetchone()
            # Worker evidence may enrich frames, but cannot replace identity/order/timing.
            identity_keys = ("asset_id", "t_ms", "width", "height")
            if len(frames) != len(capture["frames"]) or any(
                any(original.get(key) != observed.get(key) for key in identity_keys)
                for original, observed in zip(capture["frames"], frames, strict=True)
            ):
                raise Conflict("evidence_mismatch", "Verification evidence belongs to another capture")
            slot = capture["slot"]
            current = capture["revision"] == session["revision"] and session["state"] != "published"
            if slot in {"preview", "recovery"}:
                current = current and capture["epoch"] == session["epoch"]
            if slot == "preview":
                fresh = connection.execute(
                    "SELECT created_at>clock_timestamp()-(%s*interval '1 second') AS fresh "
                    "FROM counterpart_captures WHERE id=%s",
                    (PREVIEW_MAX_AGE_SECONDS, capture["id"]),
                ).fetchone()["fresh"]
                current = current and capture["client_seq"] > session["feedback_seq"] and fresh
            status = ("accepted" if result["accepted"] else "rejected") if current else "superseded"
            connection.execute(
                "UPDATE counterpart_captures SET "
                "status=%s,result=%s,observed_frames=%s,completed_at=clock_timestamp() "
                "WHERE id=%s",
                (status, Jsonb(result), Jsonb(frames), capture["id"]),
            )
            connection.execute(
                "UPDATE counterpart_jobs SET status=%s,lease_token=NULL WHERE id=%s",
                ("succeeded" if current else "superseded", job_id),
            )
            if not current:
                self._event(
                    connection, session["id"], "stale_result_ignored", job_id=job_id, reason="revision_or_age"
                )
                return False
            if slot == "preview":
                connection.execute(
                    "UPDATE counterpart_sessions SET feedback_capture_id=%s,feedback_seq=%s WHERE id=%s",
                    (capture["id"], capture["client_seq"], session["id"]),
                )
            elif result["accepted"]:
                if slot == "baseline":
                    connection.execute(
                        "UPDATE counterpart_sessions SET "
                        "baseline_id=%s,counterpart_id=NULL,state='baseline_ready', "
                        "recovery_required=(recovery_required OR %s) WHERE id=%s",
                        (capture["id"], capture["epoch"] != session["epoch"], session["id"]),
                    )
                elif slot == "counterpart":
                    connection.execute(
                        "UPDATE counterpart_sessions SET "
                        "counterpart_id=%s,state='ready',recovery_required=false WHERE id=%s",
                        (capture["id"], session["id"]),
                    )
                elif slot == "recovery":
                    connection.execute(
                        "UPDATE counterpart_sessions SET recovery_required=false WHERE id=%s",
                        (session["id"],),
                    )
            self._event(
                connection,
                session["id"],
                "capture_" + status,
                capture_id=capture["id"],
                slot=slot,
                code=result.get("code", "verified"),
            )
            return True

    def fail(self, job_id: str, lease_token: str, code: str, retryable: bool = False) -> bool:
        code = _code(code)
        with self._connection() as connection:
            job, session = self._locked_job(connection, job_id, lease_token)
            if job is None:
                return False
            retry = retryable and job["attempt"] < MAX_ATTEMPTS
            connection.execute(
                "UPDATE counterpart_jobs SET status=%s,lease_token=NULL,last_error=%s, "
                "available_at=clock_timestamp()+(%s*interval '1 second') WHERE id=%s",
                (
                    "retry_wait" if retry else "failed",
                    code,
                    min(2 ** job["attempt"], 30) if retry else 0,
                    job_id,
                ),
            )
            connection.execute(
                "UPDATE counterpart_captures SET status=%s,error=%s WHERE id=%s",
                ("queued" if retry else "failed", code, job["capture_id"]),
            )
            self._event(
                connection,
                session["id"],
                "verification_retry" if retry else "capture_failed",
                capture_id=job["capture_id"],
                code=code,
            )
            return True

    def publish(self, session_id: str, expected_revision: int) -> dict:
        with self._connection() as connection:
            session = self._session(connection, session_id, lock=True)
            existing = connection.execute(
                "SELECT * FROM counterpart_pairs WHERE session_id=%s", (session_id,)
            ).fetchone()
            if existing is not None:
                return _json(existing)
            if expected_revision != session["revision"]:
                raise Conflict("stale_revision", "Review the current pair before publishing")
            if (
                session["state"] != "ready"
                or session["baseline_id"] is None
                or session["counterpart_id"] is None
            ):
                raise Conflict("incomplete_pair", "Both observations must be accepted before publishing")
            latest = self._capture(connection, session["latest_capture_id"], internal=True)
            if latest is None or latest["status"] != "accepted":
                raise Conflict(
                    "unfinished_replacement", "Finish or correct the latest capture before publishing"
                )
            unfinished = connection.execute(
                "SELECT 1 FROM counterpart_jobs j JOIN counterpart_captures c ON c.id=j.capture_id "
                "WHERE j.session_id=%s AND c.slot IN ('baseline','counterpart') "
                "AND j.status IN ('pending','retry_wait','running')",
                (session_id,),
            ).fetchone()
            if unfinished:
                raise Conflict(
                    "unfinished_replacement", "Wait for the current verification before publishing"
                )
            counterpart = self._capture(connection, session["counterpart_id"], internal=True)
            if counterpart["revision"] != session["revision"]:
                raise Conflict("unfinished_replacement", "The current capture revision has not been accepted")
            pair_id = str(uuid4())
            manifest = {
                "schema_version": "counterpart.pair.v1",
                "pair_id": pair_id,
                "session_id": session_id,
                "recipe": session["recipe"],
                "mode": session["mode"],
                "revision": session["revision"],
                "objects": {"a": session["object_a"], "b": session["object_b"]},
                "split_group": session_id,
                "split": split_for_session(session_id),
                "baseline": self._capture(connection, session["baseline_id"], internal=True),
                "counterpart": self._capture(connection, session["counterpart_id"], internal=True),
                "provenance": {
                    "evidence_kind": "fixture_replay" if session["mode"] == "replay" else "roboflow_capture",
                    "human_reviewed": False,
                },
            }
            # Fixtures remain labeled, and may never enqueue real dataset uploads.
            export_status = "disabled_replay" if session["mode"] == "replay" else "pending"
            connection.execute(
                "INSERT INTO counterpart_pairs (id,session_id,manifest,export_status) VALUES (%s,%s,%s,%s)",
                (pair_id, session_id, Jsonb(manifest), export_status),
            )
            connection.execute(
                "INSERT INTO counterpart_export_intents (id,pair_id,status) VALUES (%s,%s,%s)",
                (str(uuid4()), pair_id, "disabled" if session["mode"] == "replay" else "pending"),
            )
            connection.execute("UPDATE counterpart_sessions SET state='published' WHERE id=%s", (session_id,))
            self._supersede(connection, session_id, ("preview", "recovery"))
            self._event(connection, session_id, "pair_published", pair_id=pair_id)
            snapshot = self._snapshot(connection, session_id)
            return snapshot["pair"]

    @staticmethod
    def _manifest_assets(manifest: dict) -> set[str]:
        return {
            frame["asset_id"] for slot in ("baseline", "counterpart") for frame in manifest[slot]["frames"]
        }

    def export_claim(self, worker_id: str, lease_seconds: float = 90) -> dict | None:
        """Claim one live export; saved phases survive an interrupted HTTP effect."""
        self._validate_lease(lease_seconds)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT e.*,p.manifest FROM counterpart_export_intents e JOIN counterpart_pairs p ON "
                "p.id=e.pair_id "
                "WHERE ((e.status='pending' AND e.available_at<=clock_timestamp()) OR "
                "(e.status='running' AND "
                "e.lease_expires_at<=clock_timestamp())) "
                "AND p.manifest->>'mode'='live' ORDER BY e.updated_at,e.id FOR UPDATE OF e SKIP "
                "LOCKED LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            token = str(uuid4())
            connection.execute(
                "UPDATE counterpart_export_intents SET status='running',lease_token=%s,worker_id=%s, "
                "lease_expires_at=clock_timestamp()+(%s*interval '1 second'),attempt=attempt+1, "
                "updated_at=clock_timestamp() WHERE id=%s",
                (token, worker_id, lease_seconds, row["id"]),
            )
            connection.execute(
                "UPDATE counterpart_pairs SET export_status='running' WHERE id=%s", (row["pair_id"],)
            )
            return _json(
                {
                    "pair_id": row["pair_id"],
                    "lease_token": token,
                    "manifest": row["manifest"],
                    "receipts": row["receipts"],
                    "attempt": row["attempt"] + 1,
                }
            )

    def export_heartbeat(self, pair_id: str, lease_token: str, lease_seconds: float = 90) -> bool:
        self._validate_lease(lease_seconds)
        with self._connection() as connection:
            return (
                connection.execute(
                    "UPDATE counterpart_export_intents SET "
                    "lease_expires_at=clock_timestamp()+(%s*interval '1 second') "
                    "WHERE pair_id=%s AND lease_token=%s AND status='running' "
                    "AND lease_expires_at>clock_timestamp() RETURNING id",
                    (lease_seconds, pair_id, lease_token),
                ).fetchone()
                is not None
            )

    @staticmethod
    def _export_locked(connection, pair_id: str, lease_token: str):
        row = connection.execute(
            "SELECT e.*,p.manifest,e.lease_expires_at>clock_timestamp() AS lease_valid "
            "FROM counterpart_export_intents e JOIN counterpart_pairs p ON p.id=e.pair_id "
            "WHERE e.pair_id=%s FOR UPDATE OF e",
            (pair_id,),
        ).fetchone()
        if (
            row is None
            or row["status"] != "running"
            or str(row["lease_token"]) != str(lease_token)
            or not row["lease_valid"]
        ):
            return None
        return row

    def export_record_asset(self, pair_id: str, lease_token: str, asset_id: str, receipt: dict) -> bool:
        """Durably save a phase before external effects; confirmed receipts cannot change."""
        states = {"uploading", "uploaded", "annotating", "confirmed", "uncertain"}
        if not isinstance(receipt, dict) or receipt.get("state") not in states:
            raise Conflict("invalid_export_receipt", "An export receipt must include a recognized state")
        with self._connection() as connection:
            row = self._export_locked(connection, pair_id, lease_token)
            if row is None:
                return False
            if asset_id not in self._manifest_assets(row["manifest"]):
                raise Conflict("unknown_export_asset", "This media asset is not part of the published pair")
            prior = row["receipts"].get(asset_id)
            if prior is not None and prior.get("state") == "confirmed":
                if prior != receipt:
                    raise Conflict(
                        "immutable_export_receipt", "A confirmed export receipt cannot be replaced"
                    )
                return True
            transitions = {
                None: {"uploading"},
                "uploading": {"uploading", "uploaded", "uncertain"},
                "uploaded": {"uploaded", "annotating", "confirmed", "uncertain"},
                "annotating": {"annotating", "confirmed", "uncertain"},
                "uncertain": {"uncertain"},
            }
            if receipt["state"] not in transitions.get(prior.get("state") if prior else None, set()):
                raise Conflict(
                    "invalid_export_transition", "This receipt would skip or reverse an export phase"
                )
            receipts = {**row["receipts"], asset_id: receipt}
            connection.execute(
                "UPDATE counterpart_export_intents SET receipts=%s,updated_at=clock_timestamp() "
                "WHERE pair_id=%s",
                (Jsonb(receipts), pair_id),
            )
            return True

    def export_finish(
        self,
        pair_id: str,
        lease_token: str,
        status: str,
        error: str | None = None,
        *,
        delay_seconds: float = 0,
    ) -> bool:
        """Release a lease; delay pending reads without holding a worker or transaction.

        An absent provider search result immediately after upload is not proof of
        failure. Persisting the next read time lets reconciliation survive restarts
        while preserving the original upload intent and avoiding duplicate writes.
        """
        if (
            not isinstance(delay_seconds, (int, float))
            or isinstance(delay_seconds, bool)
            or not math.isfinite(delay_seconds)
            or not 0 <= delay_seconds <= 300
        ):
            raise ValueError("Export delay must be finite and between zero and 300 seconds")
        if status not in {"pending", "complete", "uncertain", "failed"}:
            raise Conflict("invalid_export_status", "Unsupported export completion status")
        with self._connection() as connection:
            row = self._export_locked(connection, pair_id, lease_token)
            if row is None:
                return False
            if status == "complete" and any(
                row["receipts"].get(asset_id, {}).get("state") != "confirmed"
                for asset_id in self._manifest_assets(row["manifest"])
            ):
                raise Conflict(
                    "incomplete_export", "Every pair asset needs a confirmed receipt before completion"
                )
            connection.execute(
                "UPDATE counterpart_export_intents SET status=%s,lease_token=NULL,last_error=%s, "
                "updated_at=clock_timestamp(),available_at=clock_timestamp()+(%s*interval '1 second') "
                "WHERE pair_id=%s",
                (status, _code(error) if error else None, delay_seconds, pair_id),
            )
            connection.execute("UPDATE counterpart_pairs SET export_status=%s WHERE id=%s", (status, pair_id))
            return True

    def reserve_budget(self, provider: str, request_key: str, units: int, limit: int) -> bool:
        if (
            not isinstance(units, int)
            or isinstance(units, bool)
            or units <= 0
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 0
        ):
            raise ValueError("Budget units and limit must be nonnegative integers with positive units")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO counterpart_budget_accounts (provider) VALUES (%s) ON CONFLICT DO NOTHING",
                (provider,),
            )
            account = connection.execute(
                "SELECT reserved_units FROM counterpart_budget_accounts WHERE provider=%s FOR UPDATE",
                (provider,),
            ).fetchone()
            existing = connection.execute(
                "SELECT units FROM counterpart_budget_reservations WHERE provider=%s AND request_key=%s",
                (provider, request_key),
            ).fetchone()
            if existing is not None:
                if existing["units"] != units:
                    raise Conflict(
                        "budget_request_mismatch", "This request already reserved a different allowance"
                    )
                return True
            if account["reserved_units"] + units > limit:
                return False
            connection.execute(
                "INSERT INTO counterpart_budget_reservations (provider,request_key,units) VALUES (%s,%s,%s)",
                (provider, request_key, units),
            )
            connection.execute(
                "UPDATE counterpart_budget_accounts SET reserved_units=reserved_units+%s WHERE provider=%s",
                (units, provider),
            )
            return True

    def metrics(self) -> dict:
        with self._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            jobs = connection.execute(
                "SELECT status,count(*) AS count FROM counterpart_jobs GROUP BY status"
            ).fetchall()
            stale = connection.execute(
                "SELECT count(*) AS count FROM counterpart_events WHERE kind='stale_result_ignored'"
            ).fetchone()["count"]
            reservations = connection.execute(
                "SELECT provider,reserved_units FROM counterpart_budget_accounts ORDER BY provider"
            ).fetchall()
            pairs = connection.execute("SELECT count(*) AS count FROM counterpart_pairs").fetchone()["count"]
            return {
                "jobs": {row["status"]: row["count"] for row in jobs},
                "stale_results": stale,
                "budget_reservations": reservations,
                "published_pairs": pairs,
            }
