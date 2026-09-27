"""Operator inspection and conservative recovery of an explicit export request.

This module makes no provider calls. Upload uncertainty can start another bounded
read-only reconciliation cycle, preserving its write intent. Annotation ambiguity
cannot be resolved from the current provider API and is never automatically retried.
Export authorization is frozen at request time; a later review is not cancellation.
"""

from __future__ import annotations

import copy
import re
from uuid import UUID

from psycopg.types.json import Jsonb

from .exporting import ExportWorker, InvalidExport, _assets, export_split
from .repository import Conflict, NotFound, Repository, _json

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_STATES = {"uploading", "uploaded", "annotating", "confirmed", "uncertain"}
_RETRYABLE_CONFIGURATION = {
    "provider_auth_failed",
    "provider_budget_exhausted",
    "provider_rate_limited",
    "destination_auth_failed",
    "destination_not_found",
    "invalid_destination_configuration",
    "private_destination_required",
    "export_destination_mismatch",
}
_UPLOAD_UNCERTAINTY = {
    "upload_outcome_unknown",
    "upload_reconciliation_unconfirmed",
    "upload_not_yet_visible",
}
_SAFE_ERRORS = (
    _RETRYABLE_CONFIGURATION
    | _UPLOAD_UNCERTAINTY
    | {
        "annotation_outcome_unknown",
        "destination_identity_mismatch",
        "destination_redirect_rejected",
        "destination_unavailable",
        "invalid_destination_response",
        "export_disabled",
        "export_not_configured",
        "export_receipt_mismatch",
        "export_requires_reconciliation",
        "invalid_confirmed_receipt",
        "invalid_export_evidence",
        "invalid_export_manifest",
        "invalid_export_media",
        "invalid_export_receipt_state",
        "invalid_reconciliation_attempts",
        "media_unavailable",
        "unconfirmed_image_receipt",
        "conflicting_asset_observations",
        "export_outcome_unknown",
        "export_reconciliation_ambiguous",
        "invalid_export_parameters",
        "invalid_image_bytes",
        "invalid_image_dimensions",
        "invalid_provider_configuration",
        "invalid_provider_identifier",
        "invalid_provider_response",
        "provider_redirect_rejected",
        "provider_request_failed",
        "provider_transport_error",
        "provider_unavailable",
    }
)
_MAX_MANUAL_RECHECKS = 3


def _code(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value in _SAFE_ERRORS else "unrecognized_export_error"


def _destination(value) -> dict | None:
    if (
        not isinstance(value, dict)
        or set(value) != {"workspace", "project", "visibility"}
        or value.get("visibility") != "private"
        or any(
            not isinstance(value.get(k), str) or not _SLUG.fullmatch(value[k])
            for k in ("workspace", "project")
        )
    ):
        return None
    return dict(value)


def _pair_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise NotFound("Pair not found") from None


class ExportAdmin:
    def __init__(self, repo: Repository):
        self.repo = repo

    @staticmethod
    def _row(connection, pair_id: str, *, lock: bool = False) -> dict:
        # Match publication/request_export lock order: pair before export intent.
        pair = connection.execute(
            "SELECT id FROM counterpart_pairs WHERE id=%s" + (" FOR UPDATE" if lock else ""),
            (pair_id,),
        ).fetchone()
        if pair is None:
            raise NotFound("Pair not found")
        row = connection.execute(
            "SELECT e.*,p.session_id,p.manifest FROM counterpart_export_intents e "
            "JOIN counterpart_pairs p ON p.id=e.pair_id WHERE e.pair_id=%s"
            + (" FOR UPDATE OF e" if lock else ""),
            (pair_id,),
        ).fetchone()
        if row is None:
            raise NotFound("Export record not found")
        return row

    @staticmethod
    def _history(connection, row: dict) -> list[dict]:
        return connection.execute(
            "SELECT created_at,data FROM counterpart_events WHERE session_id=%s "
            "AND kind='export_recheck_requested' AND data->>'pair_id'=%s ORDER BY id",
            (row["session_id"], str(row["pair_id"])),
        ).fetchall()

    @staticmethod
    def _eligibility(row: dict, rechecks: int) -> tuple[bool, str]:
        if row["status"] in {"pending", "running"}:
            return False, "The export is active; its worker will continue or recover its lease."
        if row["status"] == "complete":
            return False, "All image and prediction receipts are already confirmed."
        if row["status"] == "disabled":
            return False, "Request export to a verified private destination first."
        if rechecks >= _MAX_MANUAL_RECHECKS:
            return False, "Three operator rechecks have been exhausted; inspect the destination manually."
        destination = _destination(row["destination"])
        if destination is None:
            return False, "The original private destination is missing or invalid."
        try:
            assets = _assets(row["manifest"], str(row["pair_id"]))
        except InvalidExport:
            return False, "The saved publication evidence needs manual inspection."
        receipts = row["receipts"]
        if not isinstance(receipts, dict):
            return False, "The saved receipts need manual inspection."
        uncertain_uploads = 0
        for asset_id, receipt in receipts.items():
            if (
                not isinstance(receipt, dict)
                or asset_id not in assets
                or receipt.get("asset_id") != asset_id
                or receipt.get("media_sha256") != asset_id
                or receipt.get("pair_id") != str(row["pair_id"])
                or receipt.get("session_id") != str(row["session_id"])
                or receipt.get("destination") != {"provider": "roboflow", **destination}
                or receipt.get("split") != export_split(str(row["session_id"]))
                or receipt.get("observation_sha256") != assets[asset_id]["signature"]
            ):
                return False, "The saved receipts do not match the original destination and evidence."
            state = receipt.get("state")
            if state == "confirmed":
                if not ExportWorker._confirmed(receipt):
                    return False, "A confirmed receipt is incomplete and needs manual inspection."
                continue
            if state == "annotating" or any(
                field in receipt
                for field in ("expected_annotation_sha256", "annotation_sha256", "annotation_status")
            ):
                return False, (
                    "An annotation write may have succeeded. Inspect it in Roboflow; "
                    "this API cannot prove the outcome or safely repeat it. "
                    "The local dataset remains available."
                )
            if state == "uncertain":
                if receipt.get("error") not in _UPLOAD_UNCERTAINTY:
                    return False, "The uncertain receipt is not a verifiable upload outcome."
                uncertain_uploads += 1
            elif state == "uploading":
                uncertain_uploads += 1
            elif state != "uploaded":
                return False, "The receipt phase is unsupported; inspect it manually."
        if row["status"] == "uncertain" and uncertain_uploads:
            return True, "Search Roboflow again for existing uploads; never repeat an uncertain upload."
        if row["status"] == "failed" and row["last_error"] in _RETRYABLE_CONFIGURATION:
            return True, (
                "Retry after fixing the reported configuration or access issue. "
                "Existing upload intents are reconciled by reads, never discarded."
            )
        return False, "This failure is not safe to retry automatically; inspect it manually."

    def _view(self, row: dict, history: list[dict]) -> dict:
        can_recheck, reason = self._eligibility(row, len(history))
        receipts = []
        for asset_id, receipt in row["receipts"].items() if isinstance(row["receipts"], dict) else []:
            if (
                not isinstance(asset_id, str)
                or not _DIGEST.fullmatch(asset_id)
                or not isinstance(receipt, dict)
            ):
                continue
            image_id = receipt.get("image_id")
            attempts = receipt.get("reconciliation_attempts", 0)
            receipts.append(
                {
                    "asset_id": asset_id,
                    "state": receipt.get("state")
                    if isinstance(receipt.get("state"), str) and receipt["state"] in _STATES
                    else "unknown",
                    "image_id": image_id
                    if isinstance(image_id, str) and _IDENTIFIER.fullmatch(image_id)
                    else None,
                    "error": _code(receipt.get("error")),
                    "reconciliation_attempts": attempts
                    if type(attempts) is int and 0 <= attempts <= 5
                    else None,
                    "metadata_confirmed": receipt.get("metadata_confirmed") is True,
                    "annotation_status": "confirmed"
                    if receipt.get("annotation_status") == "confirmed"
                    else None,
                }
            )
        return _json(
            {
                "pair_id": row["pair_id"],
                "status": row["status"],
                "destination": _destination(row["destination"]),
                "last_error": _code(row["last_error"]),
                "available_at": row["available_at"],
                "updated_at": row["updated_at"],
                "can_recheck": can_recheck,
                "safe_recheck": can_recheck,
                "recheck_reason": reason,
                "recheck_count": len(history),
                "receipts": sorted(receipts, key=lambda r: r["asset_id"]),
                "history": [
                    {
                        "at": event["created_at"],
                        "reviewer": event["data"].get("reviewer"),
                        "previous_status": event["data"].get("previous_status"),
                        "previous_error": _code(event["data"].get("previous_error")),
                        "recheck_count": index + 1,
                    }
                    for index, event in enumerate(history)
                ],
            }
        )

    def status(self, pair_id: str) -> dict:
        with self.repo._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = self._row(connection, _pair_id(pair_id))
            return self._view(row, self._history(connection, row))

    def recheck(self, pair_id: str, reviewer: str) -> dict:
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or len(reviewer) > 120
            or any(ord(c) < 32 for c in reviewer)
        ):
            raise Conflict("invalid_export_reviewer", "Provide the current operator identity")
        with self.repo._connection() as connection:
            row = self._row(connection, _pair_id(pair_id), lock=True)
            history = self._history(connection, row)
            permitted, reason = self._eligibility(row, len(history))
            if not permitted:
                raise Conflict("export_recheck_unavailable", reason)
            receipts = copy.deepcopy(row["receipts"])
            for receipt in receipts.values():
                if receipt["state"] in {"uploading", "uncertain"}:
                    receipt["state"] = "uploading"
                    receipt["reconciliation_attempts"] = 0
                    receipt["operator_rechecks"] = len(history) + 1
                    receipt.pop("error", None)
            # Preserve the entire previous receipt map in the append-only audit;
            # never remove an asset intent to trick the worker into another upload.
            self.repo._event(
                connection,
                str(row["session_id"]),
                "export_recheck_requested",
                pair_id=str(row["pair_id"]),
                reviewer=reviewer.strip(),
                previous_status=row["status"],
                previous_error=row["last_error"],
                previous_receipts=row["receipts"],
                recheck_count=len(history) + 1,
            )
            connection.execute(
                "UPDATE counterpart_export_intents SET receipts=%s,status='pending',last_error=NULL,"
                "lease_token=NULL,lease_expires_at=NULL,worker_id=NULL,available_at=clock_timestamp(),"
                "updated_at=clock_timestamp() WHERE pair_id=%s",
                (Jsonb(receipts), row["pair_id"]),
            )
            connection.execute(
                "UPDATE counterpart_pairs SET export_status='pending' WHERE id=%s", (row["pair_id"],)
            )
            updated = self._row(connection, str(row["pair_id"]))
            return self._view(updated, self._history(connection, updated))
