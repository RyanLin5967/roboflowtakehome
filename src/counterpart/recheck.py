"""Recheck saved observations under a new contract without rewriting their history.

This is a derived collection, not a new detector run or human approval. Original
pixels, model observations and decisions stay in their source session. The source
lock fences the final commit; bounded pixel verification runs outside that lock.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from .execution_contract import (
    ExecutionContractError,
    assert_observation_matches,
    canonical,
    validate_execution_contract,
)
from .recipes import RecipeContractError, build_recipe_snapshot, validate_recipe_snapshot
from .repository import Conflict, Repository, _json
from .splits import session_split_group, validate_split_group
from .verification import verify

TARGET_VERSION = "2.4.0"
SOURCE_VERSIONS = frozenset({"2.0.0", "2.1.0", "2.2.0", "2.3.0"})
RECEIPT_VERSIONS = frozenset({"2.1.0", "2.2.0", "2.3.0", "2.4.0"})


def derived_token(parent_token: str, session_id: str, idempotency_key: str) -> str:
    """Retries recover one capability without persisting its plaintext anywhere."""
    message = canonical(["counterpart.saved-pair-recheck.v1", session_id, idempotency_key])
    return hmac.new(parent_token.encode(), message.encode(), hashlib.sha256).hexdigest()


class PairRecheck:
    def __init__(self, repo: Repository, media):
        self.repo, self.media = repo, media

    def _receipt(self, connection, session_id, key, request, token):
        row = connection.execute(
            "SELECT data FROM counterpart_events WHERE session_id=%s "
            "AND kind='saved_pair_rechecked' AND data->>'idempotency_key'=%s",
            (session_id, key),
        ).fetchone()
        if row is None:
            return None
        recorded = row["data"]["request"]
        # The caller supplies revision/epoch, not a target verifier version.
        # A response lost before a server upgrade must recover its original
        # child rather than silently executing a different version or forking
        # another collection. New request keys use today's target version.
        if recorded.get("recipe_version") not in RECEIPT_VERSIONS or {
            key: value for key, value in recorded.items() if key != "recipe_version"
        } != {key: value for key, value in request.items() if key != "recipe_version"}:
            raise Conflict("idempotency_mismatch", "This recheck key belongs to another request")
        child = self.repo._session(connection, row["data"]["derived_session_id"])
        if (child["recipe_snapshot"] or {}).get("recipe_version") != recorded["recipe_version"]:
            raise Conflict(
                "recheck_receipt_invalid", "The saved recheck no longer matches its recorded rules."
            )
        if not hmac.compare_digest(child["capability_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise Conflict("idempotency_mismatch", "This recheck belongs to another capability")
        return {"token": token, "session": self.repo._snapshot(connection, str(child["id"]))}

    def _source(self, connection, session, request):
        session_id = str(session["id"])
        if session["revision"] != request["expected_revision"]:
            raise Conflict("stale_revision", "The photos changed. Reload before checking them again.")
        if session["epoch"] != request["epoch"]:
            raise Conflict("stale_epoch", "The camera session changed. Reload before checking again.")
        if (
            session["state"] == "published"
            or connection.execute(
                "SELECT 1 FROM counterpart_pairs WHERE session_id=%s", (session_id,)
            ).fetchone()
        ):
            raise Conflict("already_published", "Published pairs retain their original verification.")
        if session["campaign_id"] is not None:
            raise Conflict("campaign_contract_frozen", "Campaign collections keep their assigned rules.")
        if session["mode"] != "live" or session["baseline_authorization"] is not None:
            raise Conflict("recheck_ineligible", "Only standalone automatic live pairs can be rechecked.")
        try:
            snapshot = validate_recipe_snapshot(session["recipe_snapshot"])
            execution = validate_execution_contract(session["execution_contract"], mode="live")
        except (RecipeContractError, ExecutionContractError) as error:
            raise Conflict("recheck_contract_invalid", "The saved collection contract is invalid.") from error
        if snapshot["recipe_version"] not in SOURCE_VERSIONS:
            raise Conflict("recheck_ineligible", "This pair already uses current verification rules.")
        if connection.execute(
            "SELECT 1 FROM counterpart_jobs WHERE session_id=%s "
            "AND status IN ('pending','running','retry_wait')",
            (session_id,),
        ).fetchone():
            raise Conflict("verification_in_progress", "Wait for the current photo check to finish.")
        # An old initial observation may have been rejected solely because a
        # featureless background could not establish camera registration. A
        # derived current-version session can re-evaluate those exact pixels;
        # the old result is never waived or rewritten. Accepted initial-only
        # sessions can also upgrade before collecting their second observation.
        latest = connection.execute(
            "SELECT slot FROM counterpart_captures WHERE id=%s AND session_id=%s",
            (session["latest_capture_id"], session_id),
        ).fetchone()
        baseline_only = latest is not None and latest["slot"] == "baseline"
        if baseline_only and session["counterpart_id"] is not None:
            raise Conflict("recheck_ineligible", "The saved pair no longer matches its first observation.")
        selected = (
            ((session["latest_capture_id"], "baseline"),)
            if baseline_only
            else (
                (session["baseline_id"], "baseline"),
                (session["latest_capture_id"], "counterpart"),
            )
        )
        captures = []
        for capture_id, slot in selected:
            row = connection.execute(
                "SELECT * FROM counterpart_captures WHERE id=%s AND session_id=%s",
                (capture_id, session_id),
            ).fetchone()
            if (
                row is None
                or row["slot"] != slot
                or row["status"] not in {"accepted", "rejected"}
                or row["completed_at"] is None
                or not row["observed_frames"]
                or not isinstance(row["result"], dict)
                or row["interrupted"]
                or row["baseline_authorization"] is not None
                or row["recovery_unresolved"]
                or row["error"] is not None
            ):
                raise Conflict("recheck_ineligible", "Complete saved observations are required.")
            if slot == "baseline" and (
                row["reference_capture_id"] is not None
                or (
                    not baseline_only
                    and (row["status"] != "accepted" or row["result"].get("accepted") is not True)
                )
                or (baseline_only and row["revision"] != session["revision"])
            ):
                raise Conflict("recheck_ineligible", "The saved first observation must match this session.")
            if slot == "counterpart" and (
                row["reference_capture_id"] != session["baseline_id"]
                or row["revision"] != session["revision"]
            ):
                raise Conflict(
                    "recheck_reference_changed", "The second photo belongs to another first photo."
                )
            observed = row["observed_frames"]
            if len(observed) != len(row["frames"]) or any(
                any(original.get(k) != frame.get(k) for k in ("asset_id", "t_ms", "width", "height"))
                for original, frame in zip(row["frames"], observed, strict=True)
            ):
                raise Conflict(
                    "recheck_evidence_invalid", "Saved observations differ from the original media."
                )
            try:
                for frame in observed:
                    assert_observation_matches(frame, execution, "live")
            except ExecutionContractError as error:
                raise Conflict(
                    "recheck_evidence_invalid", "Saved model provenance does not match."
                ) from error
            captures.append(_json(row))
        source = {
            "session_id": session_id,
            "recipe_snapshot": snapshot,
            "execution_contract": execution,
            "split_group_snapshot": validate_split_group(
                session["split_group_snapshot"] or session_split_group(session_id), session_id
            ),
            "captures": captures,
        }
        return source

    def run(self, session_id, *, expected_revision, epoch, idempotency_key, token):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{8,128}", idempotency_key):
            raise Conflict("invalid_recheck_key", "Use a new valid recheck key.")
        request = {"expected_revision": expected_revision, "epoch": epoch, "recipe_version": TARGET_VERSION}
        with self.repo._connection() as connection:
            session = self.repo._session(connection, session_id, lock=True)
            prior = self._receipt(connection, session_id, idempotency_key, request, token)
            if prior is not None:
                return prior
            source = self._source(connection, session, request)

        old = source["recipe_snapshot"]
        snapshot = build_recipe_snapshot(
            old["recipe"],
            old["object_a"],
            old["object_b"],
            recipe_version=TARGET_VERSION,
            protected_objects=old.get("protected_objects", []),
        )
        results = []
        baseline = None
        for capture in source["captures"]:
            result = verify(
                old["recipe"],
                capture["slot"],
                capture["observed_frames"],
                baseline,
                object_a=old["object_a"],
                object_b=old["object_b"],
                recipe_snapshot=snapshot,
                media_reader=self.media.read,
            )
            results.append(result)
            if capture["slot"] == "baseline":
                baseline = {**result, "frames": capture["observed_frames"]}

        with self.repo._connection() as connection:
            session = self.repo._session(connection, session_id, lock=True)
            prior = self._receipt(connection, session_id, idempotency_key, request, token)
            if prior is not None:
                return prior
            current = self._source(connection, session, request)
            if canonical(current) != canonical(source):
                raise Conflict("recheck_evidence_changed", "Saved evidence changed during verification.")
            group = dict(source["split_group_snapshot"])
            if group["kind"] == "session":
                group["kind"] = "derived_session"
            child_id = str(uuid4())
            self.repo._create_session(
                connection,
                old["recipe"],
                "live",
                old["object_a"],
                old["object_b"],
                recipe_snapshot=snapshot,
                execution_contract=source["execution_contract"],
                split_group_snapshot=group,
                session_id=child_id,
                token=token,
            )
            capture_ids = [str(uuid4()) for _ in source["captures"]]
            for index, (capture, result) in enumerate(zip(source["captures"], results, strict=True)):
                connection.execute(
                    "INSERT INTO counterpart_captures (id,session_id,idempotency_key,fingerprint,"
                    "slot,status,revision,epoch,client_seq,frames,observed_frames,result,created_at,"
                    "completed_at,reference_capture_id,retry_of_capture_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,clock_timestamp(),%s,%s)",
                    (
                        capture_ids[index],
                        child_id,
                        f"recheck-{capture['id']}",
                        hashlib.sha256(
                            canonical([capture, snapshot["snapshot_sha256"]]).encode()
                        ).hexdigest(),
                        capture["slot"],
                        "accepted" if result["accepted"] else "rejected",
                        index + 1,
                        capture["client_seq"],
                        Jsonb(capture["frames"]),
                        Jsonb(capture["observed_frames"]),
                        Jsonb(result),
                        capture["created_at"],
                        capture_ids[0] if index else None,
                        capture["id"],
                    ),
                )
            first_ok = results[0]["accepted"]
            second_ok = len(results) == 2 and results[1]["accepted"]
            state = "ready" if first_ok and second_ok else "baseline_ready" if first_ok else "collecting"
            connection.execute(
                "UPDATE counterpart_sessions SET revision=%s,state=%s,baseline_id=%s,counterpart_id=%s,"
                "latest_capture_id=%s,recovery_required=%s WHERE id=%s",
                (
                    len(results),
                    state,
                    capture_ids[0] if first_ok else None,
                    capture_ids[1] if first_ok and second_ok else None,
                    capture_ids[-1],
                    first_ok and not second_ok,
                    child_id,
                ),
            )
            provenance = {
                "source_session_id": session_id,
                "source_capture_ids": [row["id"] for row in source["captures"]],
                "derived_capture_ids": capture_ids,
                "source_recipe_snapshot": old,
                "recipe_snapshot": snapshot,
                "source_evidence_sha256": hashlib.sha256(canonical(source).encode()).hexdigest(),
                "provider_calls": 0,
            }
            self.repo._event(connection, child_id, "saved_pair_derived", **provenance)
            self.repo._event(
                connection,
                session_id,
                "saved_pair_rechecked",
                idempotency_key=idempotency_key,
                request=request,
                derived_session_id=child_id,
                **provenance,
            )
            return {"token": token, "session": self.repo._snapshot(connection, child_id)}
