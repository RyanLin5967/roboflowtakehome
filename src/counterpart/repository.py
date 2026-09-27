"""Transactional capture state and fenced work dispatch.

Cloud calls never belong inside this module. A lease fences local acceptance;
it does not promise exactly-once remote execution. Disposable previews and
immutable committed evidence have deliberately different freshness rules.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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

from .execution_contract import (
    ExecutionContractError,
    assert_execution_matches,
    assert_observation_matches,
    build_execution_contract,
    canonical,
    normalize_legacy_configuration,
    validate_execution_contract,
)
from .splits import manifest_split, session_split_group, validate_split_group

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
            connection.execute(Path(__file__).with_name("collection_schema.sql").read_text())
            # Independent feature schemas follow their session/collection dependencies.
            for filename in ("evidence_schema.sql", "delivery_schema.sql"):
                connection.execute(Path(__file__).with_name(filename).read_text())

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
                "completed_at": row["completed_at"],
                "reference_capture_id": row["reference_capture_id"],
                "retry_of_capture_id": row["retry_of_capture_id"],
                "frames": frames,
                "result": row["result"],
                "error": row["error"],
                "interrupted": row["interrupted"],
                **(
                    {"baseline_authorization": row["baseline_authorization"], "human_review_required": True}
                    if row["baseline_authorization"] is not None
                    else {}
                ),
                **({"recovery_unresolved": True} if row["recovery_unresolved"] else {}),
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
                "recipe_snapshot",
                "campaign_id",
                "execution_contract",
                "split_group_snapshot",
                "baseline_authorization",
            )
        }
        result["human_review_required"] = row["baseline_authorization"] is not None
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
        result["review"] = None
        if result["pair"] is not None:
            result["review"] = connection.execute(
                "SELECT status,version,reason,updated_at FROM counterpart_pair_reviews WHERE pair_id=%s",
                (result["pair"]["id"],),
            ).fetchone() or {"status": "pending", "version": 0, "reason": "", "updated_at": None}
        events = connection.execute(
            "SELECT id, kind, data, created_at FROM counterpart_events WHERE session_id = %s "
            "ORDER BY id DESC LIMIT 50",
            (session_id,),
        ).fetchall()
        result["events"] = list(reversed(events))
        return _json(result)

    def _create_session(
        self,
        connection,
        recipe: str,
        mode: str,
        object_a: str,
        object_b: str,
        *,
        recipe_snapshot: dict | None = None,
        execution_contract: dict | None = None,
        split_group_snapshot: dict | None = None,
        protected_objects: list[str] | None = None,
        campaign_id: str | None = None,
        session_id: str | None = None,
        token: str | None = None,
    ) -> dict:
        """Create on a caller-owned transaction so quota admission is atomic."""
        from .recipes import (
            RecipeContractError,
            build_recipe_snapshot,
            validate_recipe_snapshot,
            version_for_mode,
        )

        if recipe not in {"spatial", "temporal"} or mode not in {"live", "replay"}:
            raise Conflict("invalid_session", "Unsupported recipe or collection mode")
        try:
            frozen = (
                build_recipe_snapshot(
                    recipe,
                    object_a,
                    object_b,
                    recipe_version=version_for_mode(mode),
                    protected_objects=protected_objects,
                )
                if recipe_snapshot is None
                else validate_recipe_snapshot(recipe_snapshot)
            )
        except RecipeContractError as error:
            raise Conflict("invalid_objects", str(error)) from error
        if (frozen["recipe"], frozen["object_a"], frozen["object_b"]) != (recipe, object_a, object_b):
            raise Conflict("recipe_mismatch", "Frozen recipe does not match this session")
        session_id, token = session_id or str(uuid4()), token or secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        try:
            split_group = validate_split_group(
                split_group_snapshot or session_split_group(session_id), session_id
            )
        except ValueError as error:
            raise Conflict("invalid_split_group", str(error)) from error
        try:
            execution = (
                build_execution_contract(mode=mode)
                if execution_contract is None
                else validate_execution_contract(execution_contract, mode=mode)
            )
        except ExecutionContractError as error:
            raise Conflict(error.code, str(error)) from error
        connection.execute(
            "INSERT INTO counterpart_sessions "
            "(id, capability_hash, recipe, mode, object_a, object_b,recipe_snapshot,"
            "campaign_id,execution_contract,"
            "split_group_snapshot) VALUES (%s, %s, %s, %s, %s, %s,%s,%s,%s,%s)",
            (
                session_id,
                digest,
                recipe,
                mode,
                object_a,
                object_b,
                Jsonb(frozen),
                campaign_id,
                Jsonb(execution),
                Jsonb(split_group),
            ),
        )
        self._event(connection, session_id, "session_created", mode=mode, campaign_id=campaign_id)
        return {"token": token, "session": self._snapshot(connection, session_id)}

    def create_session(
        self,
        recipe: str,
        mode: str,
        object_a: str,
        object_b: str,
        *,
        execution_contract: dict | None = None,
        protected_objects: list[str] | None = None,
    ) -> dict:
        with self._connection() as connection:
            return self._create_session(
                connection,
                recipe,
                mode,
                object_a,
                object_b,
                execution_contract=execution_contract,
                protected_objects=protected_objects,
            )

    def assert_execution_contract(self, session_id: str, configuration: dict) -> dict:
        """Refuse a changed worker before it enters inference or spends a reservation."""
        with self._connection() as connection:
            session = self._session(connection, session_id)
            if session["execution_contract"] is None:
                raise ExecutionContractError(
                    "execution_contract_migration_required",
                    "Historical collection requires explicit, evidence-backed model migration",
                )
            frozen = assert_execution_matches(session["execution_contract"], configuration)
            for capture_id in (session["baseline_id"], session["counterpart_id"]):
                if capture_id is None:
                    continue
                capture = self._capture(connection, capture_id, internal=True)
                for frame in capture["frames"]:
                    self._assert_observation_contract(frame, frozen, session["mode"])
            return frozen

    @staticmethod
    def _assert_observation_contract(frame: dict, contract: dict, mode: str) -> None:
        assert_observation_matches(frame, contract, mode)

    def migrate_execution_contract(
        self,
        session_id: str,
        *,
        expected_revision: int,
        reviewer: str,
        reason: str,
        execution_contract: dict | None = None,
    ) -> dict:
        """Explicit one-time legacy adoption, never reinterpret saved observations.

        Existing observations must establish one complete canonical configuration.
        An operator-supplied contract is permitted for untouched sessions only, or
        must exactly match all recorded evidence. Every adoption is audited.
        """
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 120:
            raise Conflict("invalid_migration", "Migration needs a named operator")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            raise Conflict("invalid_migration", "Migration needs a recorded reason")
        with self._connection() as connection:
            session = self._session(connection, session_id, lock=True)
            if session["revision"] != expected_revision:
                raise Conflict("stale_revision", "Reload the session before model migration")
            if session["execution_contract"] is not None:
                if execution_contract is None or canonical(session["execution_contract"]) == canonical(
                    execution_contract
                ):
                    return self._snapshot(connection, session_id)
                raise Conflict("execution_contract_frozen", "A saved model contract cannot be changed")
            busy = connection.execute(
                "SELECT 1 FROM counterpart_jobs WHERE session_id=%s "
                "AND status='running' AND lease_expires_at>clock_timestamp() LIMIT 1",
                (session_id,),
            ).fetchone()
            if busy:
                raise Conflict("migration_busy", "Wait for running capture work before model migration")
            rows = connection.execute(
                "SELECT observed_frames FROM counterpart_captures WHERE session_id=%s "
                "AND observed_frames IS NOT NULL",
                (session_id,),
            ).fetchall()
            observed = [frame for row in rows for frame in row["observed_frames"]]
            configurations = []
            cache_exists = connection.execute(
                "SELECT to_regclass('counterpart_inference_cache') IS NOT NULL AS exists"
            ).fetchone()["exists"]
            for frame in observed:
                if session["mode"] == "replay":
                    configurations.append(build_execution_contract(mode="replay")["configuration"])
                    continue
                provenance = frame.get("inference", {})
                configuration = provenance.get("configuration")
                if configuration is None and cache_exists:
                    cache = connection.execute(
                        "SELECT model_config FROM counterpart_inference_cache "
                        "WHERE cache_key=%s AND asset_id=%s AND status='succeeded'",
                        (provenance.get("cache_key"), frame.get("asset_id")),
                    ).fetchone()
                    configuration = cache["model_config"] if cache else None
                if configuration is None:
                    raise Conflict(
                        "legacy_model_unknown",
                        "Saved evidence cannot establish the original detector configuration",
                    )
                configurations.append(configuration)
            # Include completed frame calls not yet committed by a crashed worker.
            if cache_exists:
                configurations.extend(
                    row["model_config"]
                    for row in connection.execute(
                        "SELECT DISTINCT c.model_config FROM counterpart_inference_attempts a "
                        "JOIN counterpart_inference_cache c ON c.cache_key=a.cache_key "
                        "WHERE a.session_id=%s AND a.status='succeeded'",
                        (session_id,),
                    ).fetchall()
                )
            try:
                candidate = (
                    validate_execution_contract(execution_contract, mode=session["mode"])
                    if execution_contract is not None
                    else None
                )
                for configuration in configurations:
                    evidence_contract = build_execution_contract(
                        mode=session["mode"],
                        detector_configuration=normalize_legacy_configuration(configuration),
                    )
                    if candidate is None:
                        candidate = evidence_contract
                    elif candidate != evidence_contract:
                        raise ExecutionContractError(
                            "legacy_model_conflict",
                            "Historical observations use different detector configurations",
                        )
                if candidate is None:
                    raise ExecutionContractError(
                        "legacy_model_unknown", "An untouched legacy session needs an explicit model contract"
                    )
                for frame in observed:
                    self._assert_observation_contract(frame, candidate, session["mode"])
            except ExecutionContractError as error:
                raise Conflict(error.code, str(error)) from error
            connection.execute(
                "UPDATE counterpart_sessions SET execution_contract=%s WHERE id=%s",
                (Jsonb(candidate), session_id),
            )
            self._event(
                connection,
                session_id,
                "execution_contract_migrated",
                execution_contract=candidate,
                reviewer=reviewer.strip(),
                reason=reason.strip(),
                observations_checked=len(observed),
                source="recorded_observations" if configurations else "explicit_operator_declaration",
                legacy_endpoint_mapping=any("endpoint" not in item for item in configurations),
            )
            return self._snapshot(connection, session_id)

    def evaluation_snapshot(self, session_id: str) -> dict:
        """Complete historical evidence; never substitute only the latest attempt."""
        with self._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            session = self._session(connection, session_id)
            rows = connection.execute(
                "SELECT id FROM counterpart_captures WHERE session_id=%s ORDER BY created_at,id",
                (session_id,),
            ).fetchall()
            return _json(
                {
                    "schema_version": "counterpart.evaluation.session.v1",
                    "session_id": str(session["id"]),
                    "recipe": session["recipe"],
                    "mode": session["mode"],
                    "recipe_snapshot": session["recipe_snapshot"],
                    "execution_contract": session["execution_contract"],
                    "split_group_snapshot": session["split_group_snapshot"],
                    "baseline_authorization": session["baseline_authorization"],
                    "human_review_required": session["baseline_authorization"] is not None,
                    "captures_complete": True,
                    "provenance": {
                        "evidence_kind": "fixture_replay"
                        if session["mode"] == "replay"
                        else "roboflow_capture",
                        "legacy_recipe_snapshot_missing": session["recipe_snapshot"] is None,
                    },
                    "captures": [self._capture(connection, row["id"]) for row in rows],
                    "pairs": [
                        row["manifest"]
                        for row in connection.execute(
                            "SELECT manifest FROM counterpart_pairs WHERE session_id=%s", (session_id,)
                        ).fetchall()
                    ],
                    "events": connection.execute(
                        "SELECT id,kind,data,created_at FROM counterpart_events "
                        "WHERE session_id=%s ORDER BY id",
                        (session_id,),
                    ).fetchall(),
                }
            )

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
            return self._submit(connection, session_id, metadata, frames, fingerprint)

    def check_new_capture_admission(self, session_id: str, idempotency_key: str) -> None:
        """Avoid new orphan media for paused collections; commit rechecks authoritatively.

        Already recorded keys continue to the fingerprint check so paused callers
        can still recover a lost acknowledgement without inventing a new capture.
        """
        with self._connection() as connection:
            session = self._session(connection, session_id)
            previous = connection.execute(
                "SELECT id FROM counterpart_captures WHERE session_id=%s AND idempotency_key=%s",
                (session_id, idempotency_key),
            ).fetchone()
            if previous or not session.get("campaign_id"):
                return
            campaign = connection.execute(
                "SELECT active FROM counterpart_campaigns WHERE id=%s", (session["campaign_id"],)
            ).fetchone()
            if campaign is None or not campaign["active"]:
                raise Conflict("collection_paused", "This collection is paused. Saved evidence is retained.")

    def _submit(
        self,
        connection,
        session_id: str,
        metadata: dict,
        frames: list[dict],
        fingerprint: str,
        *,
        saved_epoch: int | None = None,
        saved_baseline_authorization: dict | None = None,
        saved_recovery_unresolved: bool = False,
    ) -> dict:
        session = self._session(connection, session_id, lock=True)
        previous = connection.execute(
            "SELECT id, fingerprint FROM counterpart_captures WHERE session_id = %s AND idempotency_key = %s",
            (session_id, metadata["idempotency_key"]),
        ).fetchone()
        if previous is not None:
            if previous["fingerprint"] != fingerprint:
                raise Conflict("idempotency_mismatch", "This retry key belongs to a different capture")
            return {
                "capture": self._capture(connection, previous["id"]),
                "session": self._snapshot(connection, session_id),
            }
        if session["campaign_id"] is not None:
            # A pause and a submission serialize on the collection row. A
            # previously admitted attempt may finish; no new work sneaks past
            # an API-only preflight, and duplicate receipts remain readable.
            campaign = connection.execute(
                "SELECT active FROM counterpart_campaigns WHERE id=%s FOR SHARE",
                (session["campaign_id"],),
            ).fetchone()
            if campaign is None or not campaign["active"]:
                raise Conflict(
                    "collection_paused", "This collection is paused; saved evidence remains available"
                )
        if session["state"] == "published":
            raise Conflict("already_published", "This pair is already published")
        if metadata["expected_revision"] != session["revision"]:
            raise Conflict("stale_revision", "A newer capture already changed this session")
        if metadata["epoch"] != session["epoch"]:
            raise Conflict("stale_epoch", "Reconnect before submitting another observation")
        slot = metadata["slot"]
        if slot not in {"baseline", "counterpart", "preview", "recovery"}:
            raise Conflict("invalid_slot", "Unknown capture slot")
        reviewed_collection = slot == "counterpart" and session["baseline_authorization"] is not None
        if (
            slot in COMMITTED_SLOTS
            and session["recovery_required"]
            and saved_epoch is None
            and not reviewed_collection
        ):
            raise Conflict("recovery_required", "Check the scene again before continuing")
        if slot in {"counterpart", "recovery"} and session["baseline_id"] is None:
            raise Conflict("baseline_required", "Capture an accepted first observation first")
        if slot == "preview" and metadata.get("client_seq", 0) <= session["latest_preview_seq"]:
            raise Conflict("stale_preview", "This preview has already been replaced")
        # Serialize only admission accounting, not processing, across sessions.
        connection.execute("SELECT pg_advisory_xact_lock(780245190221)")
        pending = connection.execute(
            "SELECT count(*) AS n FROM counterpart_jobs WHERE status IN ('pending','running','retry_wait')"
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
        baseline_authorization = (
            (saved_baseline_authorization if saved_epoch is not None else session["baseline_authorization"])
            if slot != "baseline"
            else None
        )
        recovery_unresolved = (
            (saved_recovery_unresolved if saved_epoch is not None else session["recovery_required"])
            if baseline_authorization is not None and slot == "counterpart"
            else False
        )
        connection.execute(
            "INSERT INTO counterpart_captures "
            "(id, session_id, idempotency_key, fingerprint, slot, revision, epoch, client_seq, "
            "frames, interrupted,reference_capture_id,baseline_authorization,recovery_unresolved) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                capture_id,
                session_id,
                metadata["idempotency_key"],
                fingerprint,
                slot,
                revision,
                session["epoch"] if saved_epoch is None else saved_epoch,
                metadata.get("client_seq", 0),
                Jsonb(frames),
                metadata.get("interrupted", False),
                session["baseline_id"] if slot != "baseline" else None,
                Jsonb(baseline_authorization) if baseline_authorization is not None else None,
                recovery_unresolved,
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
                    "counterpart_id=NULL,baseline_authorization=NULL,state='collecting' WHERE id=%s",
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

    def retry_capture(
        self,
        session_id: str,
        capture_id: str,
        expected_revision: int,
        epoch: int,
        idempotency_key: str,
    ) -> dict:
        """Reverify immutable failed evidence, never a newly sampled camera frame.

        The original attempt remains failed. A new fenced attempt links back to
        it, and retains its physical capture epoch so recovery is not invented.
        """
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{8,128}", idempotency_key):
            raise Conflict("invalid_retry_key", "Use a new valid retry key")
        fingerprint = hashlib.sha256(
            json.dumps({"retry_of": capture_id, "session_id": session_id}, sort_keys=True).encode()
        ).hexdigest()
        with self._connection() as connection:
            session = self._session(connection, session_id, lock=True)
            prior = connection.execute(
                "SELECT id,fingerprint FROM counterpart_captures WHERE session_id=%s AND idempotency_key=%s",
                (session_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise Conflict("idempotency_mismatch", "This retry key belongs to another capture")
                return {
                    "capture": self._capture(connection, prior["id"]),
                    "session": self._snapshot(connection, session_id),
                }
            try:
                capture_id = str(UUID(str(capture_id)))
            except (ValueError, TypeError, AttributeError) as error:
                raise NotFound("Capture not found") from error
            original = connection.execute(
                "SELECT * FROM counterpart_captures WHERE id=%s AND session_id=%s",
                (capture_id, session_id),
            ).fetchone()
            if original is None:
                raise NotFound("Capture not found")
            if original["status"] != "failed" or original["slot"] not in COMMITTED_SLOTS:
                raise Conflict("capture_not_retryable", "Only failed saved observations can be retried")
            if str(session["latest_capture_id"]) != capture_id:
                raise Conflict("stale_capture", "A newer observation has replaced this failed attempt")
            result = self._submit(
                connection,
                session_id,
                {
                    "slot": original["slot"],
                    "expected_revision": expected_revision,
                    "epoch": epoch,
                    "idempotency_key": idempotency_key,
                    "client_seq": original["client_seq"],
                    "interrupted": original["interrupted"],
                },
                original["frames"],
                fingerprint,
                saved_epoch=original["epoch"],
                saved_baseline_authorization=original["baseline_authorization"],
                saved_recovery_unresolved=original["recovery_unresolved"],
            )
            connection.execute(
                "UPDATE counterpart_captures SET retry_of_capture_id=%s WHERE id=%s",
                (capture_id, result["capture"]["id"]),
            )
            self._event(
                connection,
                session_id,
                "saved_capture_retried",
                retry_of=capture_id,
                capture_id=result["capture"]["id"],
            )
            result["session"] = self._snapshot(connection, session_id)
            result["capture"] = self._capture(connection, result["capture"]["id"])
            return result

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
                        "baseline_id=%s,counterpart_id=NULL,baseline_authorization=NULL,"
                        "state='baseline_ready', "
                        "recovery_required=(recovery_required OR %s) WHERE id=%s",
                        (capture["id"], capture["epoch"] != session["epoch"], session["id"]),
                    )
                elif slot == "counterpart":
                    connection.execute(
                        "UPDATE counterpart_sessions SET "
                        "counterpart_id=%s,state='ready',recovery_required=CASE WHEN %s "
                        "THEN recovery_required ELSE false END WHERE id=%s",
                        (capture["id"], capture["baseline_authorization"] is not None, session["id"]),
                    )
                elif slot == "recovery" and capture["baseline_authorization"] is None:
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
                "UPDATE counterpart_captures SET status=%s,error=%s,"
                "completed_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END WHERE id=%s",
                ("queued" if retry else "failed", code, retry, job["capture_id"]),
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
            if session["baseline_authorization"] is not None:
                raise Conflict(
                    "human_review_required",
                    "This baseline was authorized for further collection, not machine acceptance; "
                    "an operator must review and publish both observations as human-reviewed evidence",
                )
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
                "recipe_snapshot": session["recipe_snapshot"],
                "execution_contract": session["execution_contract"],
                "campaign_id": str(session["campaign_id"]) if session["campaign_id"] else None,
                **manifest_split(session),
                "baseline": self._capture(connection, session["baseline_id"], internal=True),
                "counterpart": self._capture(connection, session["counterpart_id"], internal=True),
                "provenance": {
                    "evidence_kind": "fixture_replay" if session["mode"] == "replay" else "roboflow_capture",
                    "human_reviewed": False,
                    "legacy_recipe_snapshot_missing": session["recipe_snapshot"] is None,
                },
            }
            # Fixtures remain labeled, and may never enqueue real dataset uploads.
            export_status = "disabled_replay" if session["mode"] == "replay" else "local_only"
            connection.execute(
                "INSERT INTO counterpart_pairs (id,session_id,manifest,export_status) VALUES (%s,%s,%s,%s)",
                (pair_id, session_id, Jsonb(manifest), export_status),
            )
            connection.execute(
                "INSERT INTO counterpart_export_intents (id,pair_id,status) VALUES (%s,%s,%s)",
                (str(uuid4()), pair_id, "disabled"),
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
                "AND p.manifest->>'mode'='live' AND e.destination IS NOT NULL "
                "ORDER BY e.updated_at,e.id FOR UPDATE OF e SKIP "
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
                    "destination": row["destination"],
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
