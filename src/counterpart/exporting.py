"""Incremental, fenced Roboflow publication from immutable local pair manifests.

An iteration performs at most one publication operation and one read-only privacy
check, so capture verification can preempt between every image/annotation. A persisted write
intent survives the dangerous crash window after an external effect but before
its receipt. Such an upload is reconciled, never blindly repeated. Annotation
ambiguity needs operator reconciliation: ground-truth readback does not prove
that the intended prediction was stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from uuid import uuid4

from .config import Settings
from .destination import DestinationError, DestinationResolver
from .object_storage import MediaUnavailable
from .providers import ProviderError, RoboflowExporter, voc_prediction
from .repository import Conflict, Repository
from .splits import split_for_group, validate_split_group
from .storage import MediaStore

log = logging.getLogger(__name__)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_RECONCILIATION_DELAYS = (2, 10, 30, 120)


class InvalidExport(ValueError):
    """An allowlisted code, never upstream response text or a credential."""


def export_split(split_group: str) -> str:
    """Stable 80/10/10 assignment keeps a frozen scene group together."""
    return split_for_group(split_group)


def _assets(manifest: dict, pair_id: str, *, media_reader=None) -> dict[str, dict]:
    """Fail before any external write if the publication is not canonical live evidence."""
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") not in {"counterpart.pair.v1", "counterpart.pair.v2"}
        or manifest.get("pair_id") != pair_id
        or manifest.get("mode") != "live"
        or not isinstance(manifest.get("provenance"), dict)
        or manifest["provenance"].get("evidence_kind") != "roboflow_capture"
        or not isinstance(manifest.get("session_id"), str)
        or not _IMAGE_ID.fullmatch(manifest["session_id"])
    ):
        raise InvalidExport("invalid_export_manifest")
    human_adjudicated = manifest["schema_version"] == "counterpart.pair.v2"
    if human_adjudicated:
        from .evidence_revisions import validate_adjudication

        try:
            validate_adjudication(
                manifest, media_reader=media_reader, replay_decisions=media_reader is not None
            )
        except (Conflict, KeyError, TypeError, ValueError):
            raise InvalidExport("invalid_export_adjudication") from None
    try:
        group = (
            validate_split_group(manifest["split_provenance"], manifest["session_id"])["id"]
            if "split_provenance" in manifest
            else manifest["session_id"]
        )
        if manifest.get("split_group") != group or (
            "split" in manifest and manifest["split"] != split_for_group(group)
        ):
            raise ValueError("Invalid frozen split")
    except (KeyError, ValueError):
        raise InvalidExport("invalid_export_manifest") from None
    assets = {}
    for slot in ("baseline", "counterpart"):
        capture = manifest.get(slot)
        if (
            not isinstance(capture, dict)
            or (not human_adjudicated and capture.get("slot") != slot)
            or (not human_adjudicated and capture.get("status") != "accepted")
            or not isinstance(capture.get("result"), dict)
            or (not human_adjudicated and capture["result"].get("accepted") is not True)
            or not isinstance(capture.get("frames"), list)
            or not 1 <= len(capture["frames"]) <= 48
        ):
            raise InvalidExport("invalid_export_manifest")
        for frame in capture["frames"]:
            if (
                not isinstance(frame, dict)
                or not isinstance(frame.get("asset_id"), str)
                or not _DIGEST.fullmatch(frame["asset_id"])
                or "fixture_detections" in frame
                or not isinstance(frame.get("model"), dict)
                or frame["model"].get("provider") != "roboflow"
                or not frame["model"].get("model_id")
                or frame.get("media_type") not in ("image/jpeg", "image/png")
            ):
                raise InvalidExport("invalid_export_evidence")
            try:
                # Uses the same validation and coordinate convention as the adapter.
                annotation = voc_prediction(
                    frame["detections"], frame["width"], frame["height"], filename="evidence.jpg"
                )
                signature = hashlib.sha256(
                    (annotation + json.dumps(frame["model"], sort_keys=True, allow_nan=False)).encode()
                ).hexdigest()
            except (ProviderError, KeyError, TypeError, ValueError):
                raise InvalidExport("invalid_export_evidence") from None
            asset_id = frame["asset_id"]
            if asset_id in assets and assets[asset_id]["signature"] != signature:
                raise InvalidExport("conflicting_asset_observations")
            assets.setdefault(asset_id, {"frame": frame, "signature": signature})
    return assets


class ExportWorker:
    def __init__(
        self,
        repo: Repository,
        media: MediaStore,
        config: Settings,
        exporter: RoboflowExporter | None = None,
        worker_id: str | None = None,
        *,
        destination_resolver: DestinationResolver | None = None,
    ):
        self.repo, self.media, self.config, self.exporter = repo, media, config, exporter
        self.destination_resolver = destination_resolver
        self.worker_id = worker_id or f"export-{uuid4().hex[:12]}"

    def once(self) -> bool:
        """Advance one phase; False means disabled or no eligible work."""
        if (
            not self.config.export_enabled
            or not self.config.export_project
            or self.exporter is None
            or self.destination_resolver is None
        ):
            return False
        if self.exporter.project != self.config.export_project:
            log.error("Export adapter destination does not match configured project")
            return False
        job = self.repo.export_claim(self.worker_id)
        if job is None:
            return False
        try:
            self._advance(job)
        except InvalidExport as exc:
            self.repo.export_finish(job["pair_id"], job["lease_token"], "failed", error=str(exc))
        except DestinationError as exc:
            self.repo.export_finish(
                job["pair_id"],
                job["lease_token"],
                "pending" if exc.retryable else "failed",
                error=exc.code,
                delay_seconds=30 if exc.retryable else 0,
            )
        except Exception as exc:
            # DB failures leave the intent leased for safe expiry/recovery. Never
            # overwrite an external-write intent with a guessed failure outcome.
            log.error("Export iteration interrupted (%s); receipt recovery required", type(exc).__name__)
        return True

    def _advance(self, job: dict) -> None:
        pair_id, lease = job["pair_id"], job["lease_token"]
        manifest, receipts = job["manifest"], job["receipts"]
        expected = {
            "workspace": self.exporter.workspace,
            "project": self.exporter.project,
            "visibility": "private",
        }
        if job.get("destination") != expected:
            raise InvalidExport("export_destination_mismatch")
        # A human pair decision does not promote detector boxes to ground truth.
        # Original uncertain observations remain prediction=true/addToDataset=false
        # in _annotate; independent Roboflow annotation review happens later.
        try:
            assets = _assets(manifest, pair_id, media_reader=self.media.read)
        except MediaUnavailable:
            self.repo.export_finish(pair_id, lease, "pending", error="media_unavailable", delay_seconds=10)
            return
        destination = {
            "provider": "roboflow",
            **expected,
        }
        split = export_split(manifest["split_group"])
        if not isinstance(receipts, dict):
            raise InvalidExport("export_receipt_mismatch")
        # Bind every previous external effect to this destination and evidence.
        for asset_id, receipt in receipts.items():
            if (
                not isinstance(receipt, dict)
                or asset_id not in assets
                or receipt.get("asset_id") != asset_id
                or receipt.get("destination") != destination
                or receipt.get("pair_id") != pair_id
                or receipt.get("session_id") != manifest["session_id"]
                or receipt.get("split") != split
                or receipt.get("observation_sha256") != assets[asset_id]["signature"]
                or receipt.get("media_sha256") != asset_id
            ):
                raise InvalidExport("export_receipt_mismatch")
        for asset_id, asset in assets.items():
            receipt = receipts.get(asset_id)
            if receipt is not None and receipt.get("state") == "confirmed":
                if not self._confirmed(receipt):
                    raise InvalidExport("invalid_confirmed_receipt")
                continue
            base = {
                "asset_id": asset_id,
                "media_sha256": asset_id,
                "destination": destination,
                "split": split,
                "pair_id": pair_id,
                "session_id": manifest["session_id"],
                "observation_sha256": asset["signature"],
                "receipt_version": "counterpart.export.v1",
            }
            if receipt is None:
                self._upload(job, asset["frame"], base)
            elif receipt.get("state") == "uploading":
                self._reconcile(job, receipt)
            elif receipt.get("state") == "uploaded":
                self._annotate(job, asset["frame"], receipt)
            elif receipt.get("state") == "annotating":
                self._uncertain(job, receipt, "annotation_outcome_unknown")
            elif receipt.get("state") == "uncertain":
                self.repo.export_finish(pair_id, lease, "uncertain", error="export_requires_reconciliation")
            else:
                raise InvalidExport("invalid_export_receipt_state")
            return
        self.repo.export_finish(pair_id, lease, "complete")

    @staticmethod
    def _confirmed(receipt: dict) -> bool:
        return (
            receipt.get("metadata_confirmed") is True
            and receipt.get("annotation_status") == "confirmed"
            and receipt.get("is_prediction") is True
            and isinstance(receipt.get("image_id"), str)
            and bool(_IMAGE_ID.fullmatch(receipt["image_id"]))
            and receipt.get("annotation_sha256") == receipt.get("expected_annotation_sha256")
            and isinstance(receipt.get("annotation_sha256"), str)
            and bool(_DIGEST.fullmatch(receipt["annotation_sha256"]))
        )

    def _record(self, job: dict, receipt: dict) -> bool:
        return self.repo.export_record_asset(job["pair_id"], job["lease_token"], receipt["asset_id"], receipt)

    def _release(self, job: dict, receipt: dict, *, delay_seconds: float = 0) -> None:
        if self._record(job, receipt):
            self.repo.export_finish(
                job["pair_id"], job["lease_token"], "pending", delay_seconds=delay_seconds
            )

    def _uncertain(self, job: dict, receipt: dict, code: str) -> None:
        if self._record(job, {**receipt, "state": "uncertain", "error": code}):
            self.repo.export_finish(job["pair_id"], job["lease_token"], "uncertain", error=code)

    def _ready_to_dispatch(self, job: dict, receipt: dict) -> bool:
        return self._record(job, receipt) and self.repo.export_heartbeat(job["pair_id"], job["lease_token"])

    def _upload(self, job: dict, frame: dict, base: dict) -> None:
        try:
            image = self.media.read(frame["asset_id"])
        except MediaUnavailable:
            self.repo.export_finish(
                job["pair_id"], job["lease_token"], "pending", error="media_unavailable", delay_seconds=10
            )
            return
        except (OSError, ValueError):
            raise InvalidExport("invalid_export_media") from None
        self._verify_destination(job, force=True)
        pending = {**base, "state": "uploading"}
        if not self._ready_to_dispatch(job, pending):
            return
        try:
            result = self.exporter.upload(
                image,
                asset_id=base["asset_id"],
                pair_id=base["pair_id"],
                session_id=base["session_id"],
                split=base["split"],
                media_type=frame["media_type"],
            )
        except ProviderError as exc:
            if exc.ambiguous:
                # A later claim performs a read-only reconciliation, never another upload.
                self._release(job, {**pending, "error": "upload_outcome_unknown"})
            else:
                self.repo.export_finish(job["pair_id"], job["lease_token"], "failed", error=exc.code)
            return
        if not self._valid_image_receipt(result, base):
            self._release(job, {**pending, "error": "upload_outcome_unknown"})
            return
        # A duplicate response alone does not prove this pair's metadata was applied.
        state = "uploaded" if result.get("metadata_confirmed") is True else "uploading"
        self._release(job, {**pending, **result, "state": state})

    @staticmethod
    def _valid_image_receipt(result: dict, expected: dict) -> bool:
        return (
            isinstance(result, dict)
            and result.get("asset_id") == expected["asset_id"]
            and result.get("media_sha256") == expected["media_sha256"]
            and isinstance(result.get("image_id"), str)
            and bool(_IMAGE_ID.fullmatch(result["image_id"]))
        )

    def _reconcile(self, job: dict, receipt: dict) -> None:
        self._verify_destination(job)
        attempts = receipt.get("reconciliation_attempts", 0)
        if type(attempts) is not int or not 0 <= attempts <= len(_RECONCILIATION_DELAYS) + 1:
            raise InvalidExport("invalid_reconciliation_attempts")
        if attempts == len(_RECONCILIATION_DELAYS) + 1:
            self._uncertain(job, receipt, "upload_reconciliation_unconfirmed")
            return
        # The provider's search index is eventually consistent. Persist the read
        # attempt first so a restart cannot reset this bounded reconciliation.
        receipt = {**receipt, "reconciliation_attempts": attempts + 1}
        if not self._ready_to_dispatch(job, receipt):
            return
        try:
            result = self.exporter.reconcile(
                asset_id=receipt["asset_id"],
                media_sha256=receipt["media_sha256"],
                expected_pair_id=receipt["pair_id"],
                expected_session_id=receipt["session_id"],
                expected_split=receipt["split"],
            )
        except ProviderError:
            self._uncertain(job, receipt, "upload_reconciliation_unconfirmed")
            return
        if result is None and attempts < len(_RECONCILIATION_DELAYS):
            self._release(
                job,
                {**receipt, "error": "upload_not_yet_visible"},
                delay_seconds=_RECONCILIATION_DELAYS[attempts],
            )
            return
        if (
            result is None
            or not self._valid_image_receipt(result, receipt)
            or result.get("metadata_confirmed") is not True
        ):
            self._uncertain(job, receipt, "upload_reconciliation_unconfirmed")
            return
        reconciled = {**receipt, **result, "state": "uploaded"}
        reconciled.pop("error", None)
        self._release(job, reconciled)

    def _annotate(self, job: dict, frame: dict, receipt: dict) -> None:
        self._verify_destination(job)
        if receipt.get("metadata_confirmed") is not True or not self._valid_image_receipt(receipt, receipt):
            raise InvalidExport("unconfirmed_image_receipt")
        expected_hash = hashlib.sha256(
            voc_prediction(
                frame["detections"], frame["width"], frame["height"], filename=f"{receipt['image_id']}.jpg"
            ).encode()
        ).hexdigest()
        pending = {**receipt, "state": "annotating", "expected_annotation_sha256": expected_hash}
        if not self._ready_to_dispatch(job, pending):
            return
        try:
            result = self.exporter.annotate(
                receipt["image_id"],
                detections=frame["detections"],
                width=frame["width"],
                height=frame["height"],
            )
        except ProviderError as exc:
            if exc.ambiguous:
                self._uncertain(job, pending, "annotation_outcome_unknown")
            else:
                self.repo.export_finish(job["pair_id"], job["lease_token"], "failed", error=exc.code)
            return
        confirmed = {**pending, **result, "state": "confirmed"}
        if result.get("image_id") != receipt["image_id"] or not self._confirmed(confirmed):
            self._uncertain(job, pending, "annotation_outcome_unknown")
            return
        self._release(job, confirmed)

    def _verify_destination(self, job: dict, *, force: bool = False) -> None:
        if self.destination_resolver.resolve(force=force) != job["destination"]:
            raise InvalidExport("export_destination_mismatch")
