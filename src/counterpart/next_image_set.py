"""Start another image set without changing a saved pair or waiting for review."""

from __future__ import annotations

import hashlib
import hmac
from uuid import uuid4

from .collections import CollectionRepository, _key
from .execution_contract import canonical
from .repository import Conflict, Repository
from .splits import session_split_group, validate_split_group


def continuation_token(parent_token: str, session_id: str, key: str) -> str:
    message = canonical(["counterpart.next-image-set.v1", session_id, key])
    return hmac.new(parent_token.encode(), message.encode(), hashlib.sha256).hexdigest()


class NextCollection:
    def __init__(self, repository: Repository):
        self.repo = repository
        self.collections = CollectionRepository(repository)

    def run(
        self,
        session_id: str,
        *,
        idempotency_key: str,
        token: str,
        execution_contract: dict | None = None,
        allow_new_live: bool = True,
    ) -> dict:
        key = _key(idempotency_key)
        with self.repo._connection() as connection:
            original = self.repo._session(connection, session_id)
            campaign = invite = None
            if original["campaign_id"] is not None:
                # Match invitation admission's campaign -> invitation -> session
                # lock order. Admission and the durable receipt commit together.
                campaign = self.collections._campaign(connection, str(original["campaign_id"]), lock=True)
                invite = connection.execute(
                    "SELECT i.*,i.expires_at>clock_timestamp() AS unexpired "
                    "FROM counterpart_collection_invites i "
                    "JOIN counterpart_invite_sessions s ON s.invite_id=i.id "
                    "WHERE s.session_id=%s FOR UPDATE OF i",
                    (session_id,),
                ).fetchone()
                if invite is None:
                    raise Conflict(
                        "invitation_required", "This collection has no original collector invitation."
                    )
            source = self.repo._session(connection, session_id, lock=True)
            receipt = connection.execute(
                "SELECT data FROM counterpart_events WHERE session_id=%s "
                "AND kind='next_image_set_created' AND data->>'idempotency_key'=%s",
                (session_id, key),
            ).fetchone()
            if receipt:
                child_id = receipt["data"]["next_session_id"]
                child = self.repo._session(connection, child_id)
                if not hmac.compare_digest(
                    child["capability_hash"], hashlib.sha256(token.encode()).hexdigest()
                ):
                    raise Conflict("idempotency_mismatch", "This request belongs to another capability.")
                return {"token": token, "session": self.repo._snapshot(connection, child_id)}
            pair = connection.execute(
                "SELECT id FROM counterpart_pairs WHERE session_id=%s", (session_id,)
            ).fetchone()
            if source["state"] != "published" or pair is None:
                raise Conflict("pair_not_saved", "Save this image set before starting another one.")
            if campaign is not None:
                self.collections._assert_invite_admission(
                    connection, campaign, invite, allow_new_live=allow_new_live
                )
            elif source["mode"] == "live" and not allow_new_live:
                raise Conflict("live_disabled", "Cloud inference is not enabled for new image sets.")

            group = validate_split_group(
                source["split_group_snapshot"] or session_split_group(session_id), session_id
            )
            if group["kind"] in {"session", "derived_session"}:
                group["kind"] = "continued_session"
            child_id = str(uuid4())
            created = self.repo._create_session(
                connection,
                source["recipe"],
                source["mode"],
                source["object_a"],
                source["object_b"],
                recipe_snapshot=campaign["recipe_snapshot"] if campaign else None,
                execution_contract=campaign["execution_contract"] if campaign else execution_contract,
                split_group_snapshot=group,
                protected_objects=(source["recipe_snapshot"] or {}).get("protected_objects", []),
                campaign_id=str(campaign["id"]) if campaign else None,
                session_id=child_id,
                token=token,
            )
            if campaign is not None:
                # Namespace the original source and request so a key reused by
                # another collector cannot collide with invitation join receipts.
                admission_key = "next-" + hashlib.sha256(canonical([session_id, key]).encode()).hexdigest()
                connection.execute(
                    "INSERT INTO counterpart_invite_sessions (invite_id,request_key,session_id) "
                    "VALUES (%s,%s,%s)",
                    (invite["id"], admission_key, child_id),
                )
                connection.execute(
                    "UPDATE counterpart_collection_invites "
                    "SET sessions_created=sessions_created+1 WHERE id=%s",
                    (invite["id"],),
                )
            self.repo._event(
                connection,
                session_id,
                "next_image_set_created",
                idempotency_key=key,
                next_session_id=child_id,
                saved_pair_id=str(pair["id"]),
            )
            self.repo._event(
                connection,
                child_id,
                "continued_from_saved_pair",
                source_session_id=session_id,
                source_pair_id=str(pair["id"]),
                split_group_snapshot=group,
            )
            created["session"] = self.repo._snapshot(connection, child_id)
            return created
