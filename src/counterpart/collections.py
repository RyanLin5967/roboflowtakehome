"""Durable collection admission, human review, and reproducible releases.

Authorization belongs to the HTTP boundary. Invitations are bearer secrets and
only their hashes are stored. Review decisions do not rewrite verifier evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .execution_contract import (
    ExecutionContractError,
    build_execution_contract,
    validate_execution_contract,
)
from .recipes import RecipeContractError, build_recipe_snapshot
from .repository import Conflict, NotFound, Repository, _json
from .splits import split_for_group, validate_split_group


def _uuid(value: str, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise NotFound(f"{label} not found") from error


def _text(value: str, label: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum:
        raise Conflict("invalid_collection_input", f"{label} must contain at most {maximum} characters")
    return value.strip()


def _key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{8,128}", value):
        raise Conflict("invalid_request_key", "Use an idempotency key of 8–128 simple characters")
    return value


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _review(row: dict | None) -> dict:
    if row is None or row.get("version") is None:
        return {"status": "pending", "version": 0, "reviewer": None, "reason": "", "updated_at": None}
    return _json({k: row[k] for k in ("status", "version", "reviewer", "reason", "updated_at")})


class CollectionRepository:
    def __init__(self, repository: Repository | str):
        self.repository = Repository(repository) if isinstance(repository, str) else repository

    @staticmethod
    def _campaign(connection, campaign_id: str, *, lock: bool = False) -> dict:
        row = connection.execute(
            "SELECT * FROM counterpart_campaigns WHERE id=%s" + (" FOR UPDATE" if lock else ""),
            (_uuid(campaign_id, "Collection"),),
        ).fetchone()
        if row is None:
            raise NotFound("Collection not found")
        return row

    @staticmethod
    def _progress(connection, campaign_id: str, target: int) -> dict:
        counts = connection.execute(
            "SELECT count(*) AS sessions,count(p.id) AS published_pairs,"
            "count(*) FILTER (WHERE r.status='approved') AS approved_pairs,"
            "count(*) FILTER (WHERE r.status='rejected') AS rejected_pairs,"
            "count(*) FILTER (WHERE r.status='needs_revision') AS needs_revision_pairs,"
            "count(*) FILTER (WHERE p.id IS NOT NULL AND r.pair_id IS NULL) AS pending_review_pairs "
            "FROM counterpart_sessions s LEFT JOIN counterpart_pairs p ON p.session_id=s.id "
            "LEFT JOIN counterpart_pair_reviews r ON r.pair_id=p.id WHERE s.campaign_id=%s",
            (campaign_id,),
        ).fetchone()
        return {
            **counts,
            "target_pairs": target,
            "remaining_pairs": max(0, target - counts["approved_pairs"]),
        }

    def _view(self, connection, row: dict) -> dict:
        return _json({**row, "progress": self._progress(connection, str(row["id"]), row["target_pairs"])})

    def create_campaign(
        self,
        name: str,
        recipe: str,
        mode: str = "live",
        object_a: str = "bottle",
        object_b: str = "cup",
        target_pairs: int = 20,
        *,
        execution_contract: dict | None = None,
        protected_objects: list[str] | None = None,
    ) -> dict:
        name = _text(name, "Collection name", 120)
        if mode not in {"live", "replay"}:
            raise Conflict("invalid_mode", "Choose live capture or fixture replay")
        if mode == "replay" and (object_a, object_b) != ("bottle", "cup"):
            raise Conflict("replay_objects", "Fixture replay supports bottle and cup in their original roles")
        if (
            isinstance(target_pairs, bool)
            or not isinstance(target_pairs, int)
            or not 1 <= target_pairs <= 100000
        ):
            raise Conflict("invalid_target", "Target pairs must be between one and 100000")
        try:
            from .recipes import version_for_mode

            frozen = build_recipe_snapshot(
                recipe,
                object_a,
                object_b,
                recipe_version=version_for_mode(mode),
                protected_objects=protected_objects or [],
            )
        except RecipeContractError as error:
            raise Conflict("invalid_recipe", str(error)) from error
        try:
            frozen_execution = (
                build_execution_contract(mode=mode)
                if execution_contract is None
                else validate_execution_contract(execution_contract, mode=mode)
            )
        except ExecutionContractError as error:
            raise Conflict(error.code, str(error)) from error
        with self.repository._connection() as connection:
            row = connection.execute(
                "INSERT INTO counterpart_campaigns "
                "(id,name,recipe,mode,object_a,object_b,recipe_snapshot,target_pairs,execution_contract) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    str(uuid4()),
                    name,
                    recipe,
                    mode,
                    object_a,
                    object_b,
                    Jsonb(frozen),
                    target_pairs,
                    Jsonb(frozen_execution),
                ),
            ).fetchone()
            return self._view(connection, row)

    def list_campaigns(self) -> list[dict]:
        with self.repository._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return [
                self._view(connection, row)
                for row in connection.execute(
                    "SELECT * FROM counterpart_campaigns ORDER BY created_at DESC,id"
                ).fetchall()
            ]

    def get_campaign(self, campaign_id: str) -> dict:
        with self.repository._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return self._view(connection, self._campaign(connection, campaign_id))

    def set_campaign_active(self, campaign_id: str, active: bool) -> dict:
        if not isinstance(active, bool):
            raise Conflict("invalid_collection_state", "Collection activity must be true or false")
        with self.repository._connection() as connection:
            self._campaign(connection, campaign_id, lock=True)
            row = connection.execute(
                "UPDATE counterpart_campaigns SET active=%s WHERE id=%s RETURNING *",
                (active, campaign_id),
            ).fetchone()
            return self._view(connection, row)

    def assert_collection_open(self, campaign_id: str | None) -> None:
        """API preflight only; Repository._submit also checks under a DB lock."""
        if campaign_id is None:
            return
        with self.repository._connection() as connection:
            if not self._campaign(connection, campaign_id)["active"]:
                raise Conflict(
                    "collection_paused", "This collection is paused; saved evidence remains available"
                )

    def create_split_group(self, name: str, description: str = "") -> dict:
        """Create a workspace-wide scene identity; display names are not identities."""
        name = _text(name, "Scene group name", 120)
        description = _text(description, "Scene group description", 2000, required=False)
        with self.repository._connection() as connection:
            row = connection.execute(
                "INSERT INTO counterpart_split_groups (id,name,description) VALUES (%s,%s,%s) RETURNING *",
                (str(uuid4()), name, description),
            ).fetchone()
            return _json({**row, "split": split_for_group(str(row["id"]))})

    def list_split_groups(self) -> list[dict]:
        with self.repository._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM counterpart_split_groups ORDER BY created_at DESC,id"
            ).fetchall()
            return _json([{**row, "split": split_for_group(str(row["id"]))} for row in rows])

    @staticmethod
    def _split_group(connection, group_id: str) -> dict:
        row = connection.execute(
            "SELECT * FROM counterpart_split_groups WHERE id=%s", (_uuid(group_id, "Scene group"),)
        ).fetchone()
        if row is None:
            raise NotFound("Scene group not found")
        return row

    @staticmethod
    def _invite_group_view(connection, row: dict) -> dict:
        result = dict(row)
        group = (
            CollectionRepository._split_group(connection, str(row["split_group_id"]))
            if row.get("split_group_id") is not None
            else None
        )
        result["split_group_name"] = group["name"] if group else None
        return result

    def create_invite(
        self,
        campaign_id: str,
        *,
        expires_in_hours: int = 72,
        max_sessions: int = 20,
        label: str = "",
        split_group_id: str | None = None,
    ) -> dict:
        label = _text(label, "Invite label", 120, required=False)
        if (
            isinstance(expires_in_hours, bool)
            or not isinstance(expires_in_hours, int)
            or not 1 <= expires_in_hours <= 720
        ):
            raise Conflict("invalid_invite_expiry", "Invites must expire within one hour and 30 days")
        if (
            isinstance(max_sessions, bool)
            or not isinstance(max_sessions, int)
            or not 1 <= max_sessions <= 1000
        ):
            raise Conflict("invalid_invite_quota", "Invite session limit must be between one and 1000")
        token = secrets.token_urlsafe(32)
        with self.repository._connection() as connection:
            self._campaign(connection, campaign_id)
            if split_group_id is not None:
                split_group_id = str(self._split_group(connection, split_group_id)["id"])
            row = connection.execute(
                "INSERT INTO counterpart_collection_invites "
                "(id,campaign_id,token_hash,label,expires_at,max_sessions,split_group_id) "
                "VALUES (%s,%s,%s,%s,clock_timestamp()+(%s*interval '1 hour'),%s,%s) RETURNING "
                "id,campaign_id,label,expires_at,max_sessions,sessions_created,revoked_at,created_at,split_group_id",
                (
                    str(uuid4()),
                    campaign_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    label,
                    expires_in_hours,
                    max_sessions,
                    split_group_id,
                ),
            ).fetchone()
            return _json({**self._invite_group_view(connection, row), "token": token})

    def list_invites(self, campaign_id: str) -> list[dict]:
        with self.repository._connection() as connection:
            self._campaign(connection, campaign_id)
            return _json(
                [
                    self._invite_group_view(connection, row)
                    for row in connection.execute(
                        "SELECT id,campaign_id,label,expires_at,max_sessions,sessions_created,"
                        "revoked_at,created_at,split_group_id "
                        "FROM counterpart_collection_invites WHERE campaign_id=%s "
                        "ORDER BY created_at DESC,id",
                        (campaign_id,),
                    ).fetchall()
                ]
            )

    def revoke_invite(self, campaign_id: str, invite_id: str) -> dict:
        with self.repository._connection() as connection:
            row = connection.execute(
                "UPDATE counterpart_collection_invites SET revoked_at=coalesce(revoked_at,clock_timestamp()) "
                "WHERE id=%s AND campaign_id=%s RETURNING "
                "id,campaign_id,label,expires_at,max_sessions,sessions_created,revoked_at,created_at,split_group_id",
                (_uuid(invite_id, "Invitation"), _uuid(campaign_id, "Collection")),
            ).fetchone()
            if row is None:
                raise NotFound("Invitation not found")
            return _json(self._invite_group_view(connection, row))

    @staticmethod
    def _invite(connection, token: str, *, lock: bool = False) -> dict:
        if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
            raise NotFound("Invitation not found")
        row = connection.execute(
            "SELECT *,expires_at>clock_timestamp() AS unexpired FROM counterpart_collection_invites "
            "WHERE token_hash=%s" + (" FOR UPDATE" if lock else ""),
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
        if row is None:
            raise NotFound("Invitation not found")
        return row

    def invite_info(self, token: str) -> dict:
        with self.repository._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            invite = self._invite(connection, token)
            campaign = self._view(connection, self._campaign(connection, str(invite["campaign_id"])))
            return _json(
                {
                    "campaign": campaign,
                    "invite": {
                        "id": invite["id"],
                        "label": invite["label"],
                        "expires_at": invite["expires_at"],
                        "remaining_sessions": max(0, invite["max_sessions"] - invite["sessions_created"]),
                        "split_group_id": invite["split_group_id"],
                        "split_group_name": self._invite_group_view(connection, invite)["split_group_name"],
                    },
                    "accepting": bool(
                        invite["unexpired"]
                        and invite["revoked_at"] is None
                        and invite["sessions_created"] < invite["max_sessions"]
                        and campaign["progress"]["remaining_pairs"] > 0
                        and campaign["active"]
                    ),
                }
            )

    @staticmethod
    def _session_token(invite_token: str, session_id: str) -> str:
        # Secret input is never persisted. Retrying a lost response returns the
        # same capability without storing retrievable bearer tokens in the DB.
        return hmac.new(
            invite_token.encode(), f"counterpart-session:{session_id}".encode(), "sha256"
        ).hexdigest()

    def _assert_invite_admission(self, connection, campaign, invite, *, allow_new_live=True):
        """Shared checks for joining or continuing an invitation under its quota lock."""
        if invite["revoked_at"] is not None or not invite["unexpired"]:
            raise Conflict("invite_inactive", "This invitation has expired or was revoked")
        if campaign["mode"] == "live" and not allow_new_live:
            raise Conflict("live_disabled", "Cloud inference is not enabled for new collector sessions")
        if campaign["execution_contract"] is None:
            raise Conflict(
                "execution_contract_migration_required",
                "This historical collection has no frozen detector contract; create a new collection.",
            )
        if not campaign["active"]:
            raise Conflict("collection_paused", "This collection is paused; saved evidence remains available")
        if invite["sessions_created"] >= invite["max_sessions"]:
            raise Conflict("invite_quota_reached", "This invitation has reached its session limit")
        progress = self._progress(connection, str(campaign["id"]), campaign["target_pairs"])
        if progress["remaining_pairs"] == 0:
            raise Conflict("collection_complete", "This collection has reached its reviewed-pair target")

    def create_session_from_invite(
        self, token: str, idempotency_key: str, *, allow_new_live: bool = True
    ) -> dict:
        idempotency_key = _key(idempotency_key)
        with self.repository._connection() as connection:
            # Lock order is campaign then invite; quota checking and attachment
            # share the same transaction as capability/session creation.
            first = self._invite(connection, token)
            campaign = self._campaign(connection, str(first["campaign_id"]), lock=True)
            invite = self._invite(connection, token, lock=True)
            if invite["revoked_at"] is not None or not invite["unexpired"]:
                raise Conflict("invite_inactive", "This invitation has expired or was revoked")
            existing = connection.execute(
                "SELECT session_id FROM counterpart_invite_sessions WHERE invite_id=%s AND request_key=%s",
                (invite["id"], idempotency_key),
            ).fetchone()
            if existing:
                session_id = str(existing["session_id"])
                return {
                    "token": self._session_token(token, session_id),
                    "session": self.repository._snapshot(connection, session_id),
                }
            self._assert_invite_admission(connection, campaign, invite, allow_new_live=allow_new_live)
            session_id = str(uuid4())
            group = (
                self._split_group(connection, str(invite["split_group_id"]))
                if invite["split_group_id"] is not None
                else None
            )
            group_snapshot = (
                None
                if group is None
                else {
                    "schema_version": "counterpart.split-group.v1",
                    "id": str(group["id"]),
                    "kind": "operator_group",
                    "name": group["name"],
                }
            )
            created = self.repository._create_session(
                connection,
                campaign["recipe"],
                campaign["mode"],
                campaign["object_a"],
                campaign["object_b"],
                recipe_snapshot=campaign["recipe_snapshot"],
                execution_contract=campaign["execution_contract"],
                split_group_snapshot=group_snapshot,
                campaign_id=str(campaign["id"]),
                session_id=session_id,
                token=self._session_token(token, session_id),
            )
            connection.execute(
                "INSERT INTO counterpart_invite_sessions (invite_id,request_key,session_id) "
                "VALUES (%s,%s,%s)",
                (invite["id"], idempotency_key, session_id),
            )
            connection.execute(
                "UPDATE counterpart_collection_invites SET sessions_created=sessions_created+1 WHERE id=%s",
                (invite["id"],),
            )
            return created

    def list_sessions(self, campaign_id: str) -> list[dict]:
        with self.repository._connection() as connection:
            self._campaign(connection, campaign_id)
            return _json(
                connection.execute(
                    "SELECT s.id,s.campaign_id,s.recipe,s.mode,s.object_a,s.object_b,"
                    "s.state,s.revision,s.epoch,s.split_group_snapshot,"
                    "s.created_at,p.id AS pair_id,coalesce(r.status,'pending') AS review_status,"
                    "coalesce(r.version,0) AS review_version,"
                    "(SELECT count(*) FROM counterpart_captures c WHERE c.session_id=s.id "
                    "AND c.slot IN ('baseline','counterpart')) AS capture_count,"
                    "(SELECT count(*) FROM counterpart_captures c WHERE c.session_id=s.id "
                    "AND c.status='failed') AS failed_capture_count "
                    "FROM counterpart_sessions s LEFT JOIN counterpart_pairs p ON p.session_id=s.id "
                    "LEFT JOIN counterpart_pair_reviews r ON r.pair_id=p.id WHERE s.campaign_id=%s "
                    "ORDER BY s.created_at DESC,s.id",
                    (campaign_id,),
                ).fetchall()
            )

    def list_pairs(self, campaign_id: str, review_status: str | None = None) -> list[dict]:
        if review_status not in {None, "pending", "approved", "rejected", "needs_revision"}:
            raise Conflict("invalid_review_status", "Unsupported review filter")
        with self.repository._connection() as connection:
            self._campaign(connection, campaign_id)
            rows = connection.execute(
                "SELECT p.*,r.status,r.version,r.reviewer,r.reason,r.updated_at FROM counterpart_pairs p "
                "JOIN counterpart_sessions s ON s.id=p.session_id "
                "LEFT JOIN counterpart_pair_reviews r ON r.pair_id=p.id WHERE s.campaign_id=%s "
                "AND (%s::text IS NULL OR coalesce(r.status,'pending')=%s) ORDER BY p.created_at DESC,p.id",
                (campaign_id, review_status, review_status),
            ).fetchall()
            return _json(
                [
                    {
                        **{
                            k: row[k] for k in ("id", "session_id", "manifest", "export_status", "created_at")
                        },
                        "review": _review(row),
                    }
                    for row in rows
                ]
            )

    @staticmethod
    def _pair(connection, campaign_id: str, pair_id: str, *, lock: bool = False) -> dict:
        row = connection.execute(
            "SELECT p.* FROM counterpart_pairs p JOIN counterpart_sessions s ON s.id=p.session_id "
            "WHERE p.id=%s AND s.campaign_id=%s" + (" FOR UPDATE OF p" if lock else ""),
            (_uuid(pair_id, "Pair"), _uuid(campaign_id, "Collection")),
        ).fetchone()
        if row is None:
            raise NotFound("Pair not found in this collection")
        return row

    def review_pair(
        self,
        campaign_id: str,
        pair_id: str,
        *,
        decision: str,
        expected_version: int,
        reviewer: str,
        reason: str = "",
        idempotency_key: str,
    ) -> dict:
        if decision not in {"approved", "rejected", "needs_revision"}:
            raise Conflict("invalid_review_decision", "Approve, reject, or request a new pair")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise Conflict("invalid_review_version", "Use the current review version")
        reviewer = _text(reviewer, "Reviewer", 120)
        reason = _text(reason, "Review reason", 2000, required=decision != "approved")
        idempotency_key = _key(idempotency_key)
        fingerprint = _fingerprint(
            {
                "decision": decision,
                "expected_version": expected_version,
                "reviewer": reviewer,
                "reason": reason,
            }
        )
        with self.repository._connection() as connection:
            pair = self._pair(connection, campaign_id, pair_id, lock=True)
            previous = connection.execute(
                "SELECT * FROM counterpart_review_events WHERE pair_id=%s AND request_key=%s",
                (pair_id, idempotency_key),
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Conflict("idempotency_mismatch", "This key belongs to a different review")
                return self._review_result(connection, previous)
            current = connection.execute(
                "SELECT * FROM counterpart_pair_reviews WHERE pair_id=%s",
                (pair_id,),
            ).fetchone()
            if (current["version"] if current else 0) != expected_version:
                raise Conflict("stale_review", "Another review changed this pair; reload before deciding")
            event = connection.execute(
                "INSERT INTO counterpart_review_events "
                "(id,pair_id,request_key,fingerprint,decision,version,reviewer,reason) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    str(uuid4()),
                    pair_id,
                    idempotency_key,
                    fingerprint,
                    decision,
                    expected_version + 1,
                    reviewer,
                    reason,
                ),
            ).fetchone()
            connection.execute(
                "INSERT INTO counterpart_pair_reviews (pair_id,status,version,reviewer,reason,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (pair_id) DO UPDATE SET "
                "status=EXCLUDED.status,version=EXCLUDED.version,reviewer=EXCLUDED.reviewer,"
                "reason=EXCLUDED.reason,updated_at=EXCLUDED.updated_at",
                (pair_id, decision, expected_version + 1, reviewer, reason, event["created_at"]),
            )
            self.repository._event(
                connection,
                str(pair["session_id"]),
                "pair_reviewed",
                pair_id=pair_id,
                decision=decision,
                review_version=expected_version + 1,
                reviewer=reviewer,
            )
            return self._review_result(connection, event)

    @staticmethod
    def _review_result(connection, event: dict) -> dict:
        audit = connection.execute(
            "SELECT id,decision,version,reviewer,reason,created_at FROM counterpart_review_events "
            "WHERE pair_id=%s ORDER BY version",
            (event["pair_id"],),
        ).fetchall()
        return _json(
            {
                "status": event["decision"],
                "version": event["version"],
                "reviewer": event["reviewer"],
                "reason": event["reason"],
                "updated_at": event["created_at"],
                "audit": audit,
            }
        )

    def review_history(self, campaign_id: str, pair_id: str) -> list[dict]:
        with self.repository._connection() as connection:
            self._pair(connection, campaign_id, pair_id)
            return _json(
                connection.execute(
                    "SELECT id,decision,version,reviewer,reason,created_at FROM counterpart_review_events "
                    "WHERE pair_id=%s ORDER BY version",
                    (pair_id,),
                ).fetchall()
            )

    def export_manifests(
        self,
        campaign_id: str,
        *,
        pair_ids: list[str] | None = None,
        include_replay: bool = False,
        idempotency_key: str | None = None,
    ) -> dict:
        """Persist an immutable, reviewed selection with intact pair-level splits."""
        if pair_ids is not None:
            if not pair_ids or len(pair_ids) > 10000:
                raise Conflict("invalid_selection", "Select between one and 10000 reviewed pairs")
            pair_ids = sorted({_uuid(value, "Pair") for value in pair_ids})
        if idempotency_key is not None:
            idempotency_key = _key(idempotency_key)
        fingerprint = _fingerprint({"pair_ids": pair_ids, "include_replay": include_replay})
        with self.repository._connection() as connection:
            # Serialize release admission; the single selection statement below
            # gives one consistent membership/review snapshot under READ COMMITTED.
            campaign = self._campaign(connection, campaign_id, lock=True)
            if idempotency_key is not None:
                previous = connection.execute(
                    "SELECT manifest,request_fingerprint FROM counterpart_dataset_releases "
                    "WHERE campaign_id=%s AND request_key=%s",
                    (campaign_id, idempotency_key),
                ).fetchone()
                if previous:
                    if previous["request_fingerprint"] != fingerprint:
                        raise Conflict("idempotency_mismatch", "This retry key belongs to another release")
                    return previous["manifest"]
            if campaign["mode"] == "replay" and not include_replay:
                raise Conflict(
                    "replay_release", "Replay fixtures require an explicitly labeled fixture release"
                )
            rows = connection.execute(
                "SELECT p.*,r.status,r.version,r.reviewer,r.reason,r.updated_at FROM counterpart_pairs p "
                "JOIN counterpart_sessions s ON s.id=p.session_id "
                "JOIN counterpart_pair_reviews r ON r.pair_id=p.id "
                "WHERE s.campaign_id=%s AND r.status='approved' AND (%s::uuid[] IS NULL OR p.id=ANY(%s)) "
                "ORDER BY p.id",
                (campaign_id, pair_ids, pair_ids),
            ).fetchall()
            if not rows or (pair_ids is not None and len(rows) != len(pair_ids)):
                raise Conflict("unreviewed_selection", "Every selected pair must currently be approved")
            asset_splits: dict[str, str] = {}
            group_splits: dict[str, str] = {}
            for row in rows:
                manifest = row["manifest"]
                provenance = manifest.get("split_provenance")
                try:
                    if "split_provenance" in manifest:
                        group_id = validate_split_group(provenance, manifest["session_id"])["id"]
                    else:
                        group_id = manifest["session_id"]
                    if manifest["split_group"] != group_id or manifest["split"] != split_for_group(group_id):
                        raise ValueError("Recorded partition does not match its frozen group")
                except (ValueError, KeyError) as error:
                    raise Conflict(
                        "invalid_split_provenance", "Pair split provenance is inconsistent"
                    ) from error
                previous_split = group_splits.setdefault(group_id, manifest["split"])
                if previous_split != manifest["split"]:
                    raise Conflict("split_leakage", "Related scene sessions cannot cross dataset partitions")
                for slot in ("baseline", "counterpart"):
                    for frame in manifest[slot]["frames"]:
                        prior = asset_splits.setdefault(frame["asset_id"], manifest["split"])
                        if prior != manifest["split"]:
                            raise Conflict(
                                "split_leakage",
                                "The same saved image occurs in different dataset splits. "
                                "Choose disjoint pairs or pairs in the same partition, or recollect.",
                            )
            release_id = str(uuid4())
            created_at = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            manifests = [{"manifest": row["manifest"], "review": _review(row)} for row in rows]
            split_counts = {
                split: sum(row["manifest"]["split"] == split for row in rows)
                for split in ("train", "valid", "test")
            }
            release = _json(
                {
                    "id": release_id,
                    "campaign_id": campaign_id,
                    "schema_version": "counterpart.dataset.v1",
                    "created_at": created_at,
                    "recipe_snapshot": campaign["recipe_snapshot"],
                    "evidence_kind": "fixture_replay" if campaign["mode"] == "replay" else "roboflow_capture",
                    "pair_count": len(rows),
                    "pairs": manifests,
                    "split_counts": split_counts,
                }
            )
            release["content_sha256"] = _fingerprint(
                {key: value for key, value in release.items() if key not in {"id", "created_at"}}
            )
            connection.execute(
                "INSERT INTO counterpart_dataset_releases "
                "(id,campaign_id,manifest,created_at,request_key,request_fingerprint) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (release_id, campaign_id, Jsonb(release), created_at, idempotency_key, fingerprint),
            )
            return release

    def list_releases(self, campaign_id: str) -> list[dict]:
        with self.repository._connection() as connection:
            self._campaign(connection, campaign_id)
            return _json(
                connection.execute(
                    "SELECT id,campaign_id,created_at,(manifest->>'pair_count')::integer AS pair_count,"
                    "manifest->>'content_sha256' AS content_sha256,"
                    "manifest->'split_counts' AS split_counts,manifest->>'schema_version' AS schema_version,"
                    "manifest->>'evidence_kind' AS evidence_kind "
                    "FROM counterpart_dataset_releases WHERE campaign_id=%s ORDER BY created_at DESC,id",
                    (campaign_id,),
                ).fetchall()
            )

    def get_release(self, campaign_id: str, release_id: str) -> dict:
        with self.repository._connection() as connection:
            row = connection.execute(
                "SELECT manifest FROM counterpart_dataset_releases WHERE id=%s AND campaign_id=%s",
                (_uuid(release_id, "Release"), _uuid(campaign_id, "Collection")),
            ).fetchone()
            if row is None:
                raise NotFound("Release not found")
            return row["manifest"]

    def get_release_by_id(self, release_id: str) -> dict:
        with self.repository._connection() as connection:
            row = connection.execute(
                "SELECT manifest FROM counterpart_dataset_releases WHERE id=%s",
                (_uuid(release_id, "Release"),),
            ).fetchone()
            if row is None:
                raise NotFound("Release not found")
            return row["manifest"]

    def get_pair(self, pair_id: str) -> dict:
        with self.repository._connection() as connection:
            row = connection.execute(
                "SELECT p.*,s.campaign_id,r.status,r.version,r.reviewer,r.reason,r.updated_at "
                "FROM counterpart_pairs p JOIN counterpart_sessions s ON s.id=p.session_id "
                "LEFT JOIN counterpart_pair_reviews r ON r.pair_id=p.id WHERE p.id=%s",
                (_uuid(pair_id, "Pair"),),
            ).fetchone()
            if row is None:
                raise NotFound("Pair not found")
            return _json(
                {
                    **{
                        key: row[key]
                        for key in (
                            "id",
                            "session_id",
                            "campaign_id",
                            "manifest",
                            "export_status",
                            "created_at",
                        )
                    },
                    "review": _review(row),
                }
            )

    def request_export(self, pair_id: str, destination: dict, reviewer: str) -> dict:
        """Single-pair adapter for the atomic explicit export admission path."""
        return self.request_exports(None, [pair_id], destination, reviewer)[0]

    def request_exports(
        self, campaign_id: str | None, pair_ids: list[str], destination: dict, reviewer: str
    ) -> list[dict]:
        """Admit the whole selected batch atomically, with a frozen private destination.

        The HTTP boundary resolves privacy server-side. Multiple pairs are locked
        in deterministic order; a conflict rolls back every new intent in this batch.
        """
        reviewer = _text(reviewer, "Export requester", 120)
        if (
            not isinstance(destination, dict)
            or set(destination) != {"workspace", "project", "visibility"}
            or destination.get("visibility") != "private"
            or any(
                not isinstance(destination.get(key), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", destination[key])
                for key in ("workspace", "project")
            )
        ):
            raise Conflict("private_destination_required", "Choose a verified private Roboflow destination")
        if not pair_ids or len(pair_ids) > 1000:
            raise Conflict("invalid_selection", "Select one to 1000 approved pairs")
        selected = sorted({_uuid(pair_id, "Pair") for pair_id in pair_ids})
        with self.repository._connection() as connection:
            if campaign_id is not None:
                for pair_id in selected:
                    self._pair(connection, campaign_id, pair_id)
            return [self._request_export(connection, pair_id, destination, reviewer) for pair_id in selected]

    def _request_export(self, connection, pair_id: str, destination: dict, reviewer: str) -> dict:
        # Export workers lock the intent before updating the pair. Keep the
        # same order here to avoid a retry/finish deadlock.
        intent = connection.execute(
            "SELECT * FROM counterpart_export_intents WHERE pair_id=%s FOR UPDATE",
            (pair_id,),
        ).fetchone()
        pair = connection.execute(
            "SELECT * FROM counterpart_pairs WHERE id=%s FOR UPDATE",
            (pair_id,),
        ).fetchone()
        if pair is None or intent is None:
            raise NotFound("Pair not found")
        current_review = connection.execute(
            "SELECT status,version FROM counterpart_pair_reviews WHERE pair_id=%s",
            (pair_id,),
        ).fetchone()
        if (
            pair["manifest"]["mode"] != "live"
            or current_review is None
            or current_review["status"] != "approved"
        ):
            raise Conflict("review_required", "Only approved live pairs may be exported to Roboflow")
        if intent["destination"] is not None:
            if intent["destination"] != destination:
                raise Conflict("export_destination_frozen", "An export's destination cannot be changed")
            return _json({"pair_id": pair_id, "status": intent["status"], "destination": destination})
        if intent["status"] != "disabled":
            raise Conflict("legacy_export_intent", "Reconcile this older export intent before resubmitting")
        connection.execute(
            "UPDATE counterpart_export_intents SET destination=%s,requested_by=%s,"
            "requested_at=clock_timestamp(),status='pending',available_at=clock_timestamp(),"
            "updated_at=clock_timestamp() WHERE pair_id=%s",
            (Jsonb(destination), reviewer, pair_id),
        )
        connection.execute("UPDATE counterpart_pairs SET export_status='pending' WHERE id=%s", (pair_id,))
        self.repository._event(
            connection,
            str(pair["session_id"]),
            "export_requested",
            pair_id=pair_id,
            destination=destination,
            reviewer=reviewer,
        )
        return {"pair_id": pair_id, "status": "pending", "destination": destination}
