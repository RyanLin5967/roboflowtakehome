"""Reviewed, immutable Roboflow annotation round trips and version delivery.

Wire contracts follow roboflow-python c39f99e6645b9039188f16665a5e17b2d83655d1.
Search returns annotation summaries; image details return actual pixel-center
boxes (also checked against a generated-card hosted readback). Never infer a
human review from a Roboflow annotation or overwrite the original local pair.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .exporting import ExportWorker
from .providers import ProviderError, RoboflowExporter, _safe_id
from .repository import Conflict, NotFound, Repository, _json

MAX_IMAGES = 1000
MAX_JSON_BYTES = 2 * 1024 * 1024
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_KEY = re.compile(r"^[A-Za-z0-9_.-]{8,128}$")
_GENERATION_SETTINGS = {"preprocessing": {}, "augmentation": {}}


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def identifier(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise NotFound("Record not found") from None


def request_key(value: str) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise Conflict("invalid_delivery_key", "Use an 8–128 character retry key")
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ProviderError("invalid_annotation_geometry")
    return float(value)


def normalize_annotation(value: dict, width: int, height: int) -> list[dict]:
    """Fail closed on masks/polygons/unknown box formats instead of discarding them."""
    if (
        not isinstance(value, dict)
        or set(value) - {"key", "width", "height", "boxes"}
        or value.get("width") != width
        or value.get("height") != height
        or not isinstance(value.get("boxes"), list)
        or len(value["boxes"]) > 1000
    ):
        raise ProviderError("invalid_annotation_geometry")
    if "key" in value and (
        not isinstance(value["key"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}\.(?:jpg|png)", value["key"])
    ):
        raise ProviderError("invalid_annotation_geometry")
    result = []
    for box in value["boxes"]:
        if not isinstance(box, dict) or set(box) - {"label", "x", "y", "width", "height", "id"}:
            raise ProviderError("unsupported_annotation_geometry")
        label = box.get("label")
        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label) > 100
            or any(ord(c) < 32 for c in label)
        ):
            raise ProviderError("invalid_annotation_geometry")
        x, y, w, h = [_number(box.get(k)) for k in ("x", "y", "width", "height")]
        if w <= 0 or h <= 0 or x - w / 2 < 0 or y - h / 2 < 0 or x + w / 2 > width or y + h / 2 > height:
            raise ProviderError("invalid_annotation_geometry")
        result.append({"label": label, "bbox": [x - w / 2, y - h / 2, w, h]})
    return sorted(result, key=lambda row: (row["label"], row["bbox"]))


class RoboflowDeliveryProvider(RoboflowExporter):
    def project_state(self, *, require_private=True) -> dict:
        payload = self._request(
            f"https://api.roboflow.com/{self.workspace}/{self.project}",
            http_method="GET",
            params={"api_key": self._api_key},
        )
        project, versions = payload.get("project"), payload.get("versions")
        if not isinstance(project, dict) or project.get("id") != f"{self.workspace}/{self.project}":
            raise ProviderError("destination_identity_mismatch")
        if require_private and project.get("public") is not False:
            raise ProviderError("private_destination_required")
        if not isinstance(versions, list) or len(versions) > 1000:
            raise ProviderError("invalid_provider_response")
        version_ids = []
        summaries = []
        for version in versions:
            if not isinstance(version, dict) or not isinstance(version.get("id"), str):
                raise ProviderError("invalid_provider_response")
            tail = version["id"].rsplit("/", 1)[-1]
            if not tail.isdigit() or not 0 < int(tail) < 100000000:
                raise ProviderError("invalid_provider_response")
            if version["id"] != f"{self.workspace}/{self.project}/{tail}" or int(tail) in version_ids:
                raise ProviderError("invalid_provider_response")
            version_ids.append(int(tail))
            count = version.get("images")
            splits = version.get("splits")
            valid_count = type(count) is int and count >= 0
            # The hosted API omits empty partitions (verified on the one-image
            # generated-card version); absence is zero, not an unknown count.
            valid_splits = (
                isinstance(splits, dict)
                and not set(splits) - {"train", "valid", "test"}
                and all(type(value) is int and value >= 0 for value in splits.values())
            )
            summaries.append(
                {
                    "version": int(tail),
                    "ready": valid_count and count > 0 and not version.get("generating", False),
                    "images": count if valid_count else None,
                    "splits": {k: splits.get(k, 0) for k in ("train", "valid", "test")}
                    if valid_splits
                    else None,
                }
            )
        return {
            "versions": sorted(set(version_ids)),
            "public": project.get("public"),
            "version_summaries": summaries,
        }

    def inventory(self, *, in_dataset: bool | None = None) -> list[dict]:
        result = []
        for offset in range(0, MAX_IMAGES + 100, 100):
            payload = {"offset": offset, "limit": 100, "fields": ["id", "name", "split", "user_metadata"]}
            if in_dataset is not None:
                payload["in_dataset"] = in_dataset
            page = self._request(
                f"https://api.roboflow.com/{self.workspace}/{self.project}/search",
                params={"api_key": self._api_key},
                json=payload,
            ).get("results")
            if not isinstance(page, list) or len(page) > 100 or any(not isinstance(x, dict) for x in page):
                raise ProviderError("invalid_provider_response")
            result.extend(page)
            if len(result) > MAX_IMAGES:
                raise ProviderError("delivery_inventory_limit")
            if len(page) < 100:
                ids = [row.get("id") for row in result]
                if any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
                    raise ProviderError("delivery_inventory_changed")
                return result
        raise ProviderError("delivery_inventory_limit")

    def annotation(self, image_id: str) -> dict:
        image_id = _safe_id(image_id)
        payload = self._request(
            f"https://api.roboflow.com/{self.workspace}/{self.project}/images/{image_id}",
            http_method="GET",
            params={"api_key": self._api_key},
        ).get("image")
        if not isinstance(payload, dict) or payload.get("id") != image_id:
            raise ProviderError("annotation_identity_mismatch")
        return {key: payload.get(key) for key in ("id", "name", "split", "annotation")}

    def generate(self) -> int:
        payload = self._request(
            f"https://api.roboflow.com/{self.workspace}/{self.project}/generate",
            mutating=True,
            params={"api_key": self._api_key},
            json=_GENERATION_SETTINGS,
        )
        version = payload.get("version")
        # Official SDK uses int(response["version"]); support its numeric-string
        # wire form while still refusing booleans, floats and malformed IDs.
        if isinstance(version, str) and re.fullmatch(r"[1-9][0-9]{0,7}", version):
            version = int(version)
        if type(version) is not int or not 0 < version < 100000000:
            raise ProviderError("generation_outcome_unknown", ambiguous=True)
        return version


class RoboflowDelivery:
    def __init__(self, repository: Repository, provider, *, generation_limit=0, budget_evidence=""):
        self.repo, self.provider = repository, provider
        self.generation_limit = generation_limit
        self.budget_evidence = budget_evidence.strip() if isinstance(budget_evidence, str) else ""

    @staticmethod
    def _snapshot(connection, snapshot_id):
        row = connection.execute(
            "SELECT * FROM counterpart_annotation_snapshots WHERE id=%s", (identifier(snapshot_id),)
        ).fetchone()
        if row is None:
            raise NotFound("Annotation snapshot not found")
        return row

    @staticmethod
    def _delivery(connection, delivery_id, *, lock=False):
        row = connection.execute(
            "SELECT * FROM counterpart_roboflow_deliveries WHERE id=%s" + (" FOR UPDATE" if lock else ""),
            (identifier(delivery_id),),
        ).fetchone()
        if row is None:
            raise NotFound("Delivery not found")
        return row

    def _expected(self, release_id):
        with self.repo._connection() as connection:
            row = connection.execute(
                "SELECT manifest FROM counterpart_dataset_releases WHERE id=%s", (identifier(release_id),)
            ).fetchone()
            if row is None:
                raise NotFound("Release not found")
            release = row["manifest"]
            if release.get("evidence_kind") != "roboflow_capture":
                raise Conflict(
                    "fixture_delivery_forbidden", "Replay fixtures are not captured camera evidence"
                )
            images = {}
            for entry in release["pairs"]:
                manifest = entry["manifest"]
                if entry["review"]["status"] != "approved":
                    raise Conflict("unreviewed_selection", "Every release pair must be approved")
                intent = connection.execute(
                    "SELECT status,destination,receipts FROM counterpart_export_intents WHERE pair_id=%s",
                    (manifest["pair_id"],),
                ).fetchone()
                if intent is None or intent["status"] != "complete":
                    raise Conflict(
                        "export_incomplete", "Finish the selected pair exports before reading annotations"
                    )
                destination = {
                    "workspace": self.provider.workspace,
                    "project": self.provider.project,
                    "visibility": "private",
                }
                if intent["destination"] != destination:
                    raise Conflict(
                        "export_destination_mismatch", "Every pair must use the same private project"
                    )
                for slot in ("baseline", "counterpart"):
                    for frame in manifest[slot]["frames"]:
                        asset = frame["asset_id"]
                        receipt = intent["receipts"].get(asset, {})
                        if (
                            receipt.get("state") != "confirmed"
                            or not ExportWorker._confirmed(receipt)
                            or receipt.get("metadata_confirmed") is not True
                            or receipt.get("media_sha256") != asset
                            or receipt.get("asset_id") != asset
                            or receipt.get("pair_id") != manifest["pair_id"]
                            or receipt.get("session_id") != manifest["session_id"]
                            or receipt.get("split") != manifest["split"]
                            or receipt.get("destination") != {"provider": "roboflow", **destination}
                            or not receipt.get("image_id")
                        ):
                            raise Conflict(
                                "export_incomplete", "Every source image requires a confirmed export receipt"
                            )
                        image = {
                            "asset_id": asset,
                            "image_id": receipt["image_id"],
                            "pair_id": manifest["pair_id"],
                            "session_id": manifest["session_id"],
                            "split": manifest["split"],
                            "width": frame["width"],
                            "height": frame["height"],
                            "machine_detections": frame.get("detections", []),
                        }
                        if asset in images and images[asset] != image:
                            raise Conflict(
                                "ambiguous_shared_asset", "Shared assets have conflicting source receipts"
                            )
                        images[asset] = image
            if not images or len(images) > MAX_IMAGES:
                raise Conflict("delivery_inventory_limit", "Delivery requires one to 1000 source images")
            return release, images

    def _read_images(self, expected, *, exact=False, require_dataset=False):
        self.provider.project_state()
        inventory = self.provider.inventory()
        by_id = {row["id"]: row for row in inventory}
        selected = {item["image_id"] for item in expected.values()}
        if len(selected) != len(expected) or (exact and set(by_id) != selected):
            raise Conflict(
                "project_inventory_mismatch", "The project must contain exactly the released source images"
            )
        if require_dataset and {row["id"] for row in self.provider.inventory(in_dataset=True)} != selected:
            raise Conflict(
                "annotations_not_in_dataset", "Approve and add every selected image to the Roboflow dataset"
            )
        images = []
        for asset, source in sorted(expected.items()):
            row = by_id.get(source["image_id"], {})
            metadata = row.get("user_metadata", {})
            if (
                not isinstance(metadata, dict)
                or metadata.get("counterpart_asset_id") != asset
                or metadata.get("counterpart_media_sha256") != asset
                or metadata.get("counterpart_pair_id") != source["pair_id"]
                or metadata.get("counterpart_session_id") != source["session_id"]
                or row.get("split") != source["split"]
                or row.get("name") not in {f"counterpart-{asset}.png", f"counterpart-{asset}.jpg"}
            ):
                raise Conflict("remote_source_mismatch", "Remote identity, source hash or split changed")
            remote = self.provider.annotation(source["image_id"])
            if remote.get("split") != source["split"] or remote.get("name") != row.get("name"):
                raise Conflict("remote_source_mismatch", "Remote source metadata changed during readback")
            annotation = remote["annotation"]
            boxes = normalize_annotation(annotation, source["width"], source["height"])
            images.append(
                {
                    **source,
                    "remote_name": row["name"],
                    "annotation": annotation,
                    "boxes": boxes,
                    "annotation_sha256": digest(annotation),
                }
            )
        # Detect changed membership/splits/metadata while traversing a paged, nontransactional API.
        after = self.provider.inventory()
        if digest(sorted(inventory, key=lambda x: x["id"])) != digest(sorted(after, key=lambda x: x["id"])):
            raise Conflict("project_inventory_changed", "Roboflow inventory changed; take a fresh snapshot")
        return images

    def sync_annotations(self, release_id: str, key: str) -> dict:
        release_id, key = identifier(release_id), request_key(key)
        with self.repo._connection() as connection:
            prior = connection.execute(
                "SELECT id FROM counterpart_annotation_snapshots WHERE release_id=%s AND request_key=%s",
                (release_id, key),
            ).fetchone()
        if prior:
            return self.get_snapshot(str(prior["id"]))
        release, expected = self._expected(release_id)
        images = self._read_images(expected)
        manifest = {
            "schema_version": "counterpart.roboflow-annotations.v1",
            "release_id": release_id,
            "release_sha256": release["content_sha256"],
            "destination": {
                "workspace": self.provider.workspace,
                "project": self.provider.project,
                "visibility": "private",
            },
            "images": images,
            "provider_review_attested": False,
            "source": "roboflow-image-api",
            "machine_manifest_unchanged": True,
        }
        if len(json.dumps(manifest).encode()) > MAX_JSON_BYTES:
            raise Conflict("annotation_snapshot_too_large", "Annotation snapshot exceeds the two MiB limit")
        snapshot_id = str(uuid4())
        with self.repo._connection() as connection:
            row = connection.execute(
                "INSERT INTO counterpart_annotation_snapshots "
                "(id,release_id,request_key,content_sha256,manifest) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT(release_id,request_key) DO NOTHING RETURNING id",
                (snapshot_id, release_id, key, digest(manifest), Jsonb(manifest)),
            ).fetchone()
            if row is None:
                snapshot_id = str(
                    connection.execute(
                        "SELECT id FROM counterpart_annotation_snapshots "
                        "WHERE release_id=%s AND request_key=%s",
                        (release_id, key),
                    ).fetchone()["id"]
                )
        return self.get_snapshot(snapshot_id)

    def get_snapshot(self, snapshot_id):
        with self.repo._connection() as connection:
            row = self._snapshot(connection, snapshot_id)
            approval = connection.execute(
                "SELECT reviewer,reason,created_at FROM counterpart_annotation_approvals "
                "WHERE snapshot_id=%s",
                (row["id"],),
            ).fetchone()
            return _json(
                {
                    **{k: row[k] for k in ("id", "release_id", "content_sha256", "manifest", "created_at")},
                    "approval": approval,
                }
            )

    def approve(self, snapshot_id, key, reason, reviewer="workspace-operator"):
        key = request_key(key)
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
            raise Conflict("review_reason_required", "Record why these returned annotations are approved")
        with self.repo._connection() as connection:
            row = self._snapshot(connection, snapshot_id)
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,92407))", (str(row["id"]),))
            prior = connection.execute(
                "SELECT * FROM counterpart_annotation_approvals WHERE snapshot_id=%s", (row["id"],)
            ).fetchone()
            if prior:
                if (prior["request_key"], prior["reason"], prior["reviewer"]) != (
                    key,
                    reason.strip(),
                    reviewer,
                ):
                    raise Conflict(
                        "annotation_review_frozen", "Approved snapshots are immutable; pull a new snapshot"
                    )
            else:
                connection.execute(
                    "INSERT INTO counterpart_annotation_approvals "
                    "(snapshot_id,request_key,reviewer,reason) VALUES (%s,%s,%s,%s)",
                    (row["id"], key, reviewer, reason.strip()),
                )
        return self.get_snapshot(snapshot_id)

    def request_delivery(self, snapshot_id, key):
        key = request_key(key)
        with self.repo._connection() as connection:
            row = self._snapshot(connection, snapshot_id)
            if not connection.execute(
                "SELECT 1 FROM counterpart_annotation_approvals WHERE snapshot_id=%s", (row["id"],)
            ).fetchone():
                raise Conflict("annotation_review_required", "Review the returned annotation snapshot first")
            delivery_id = str(uuid4())
            created = connection.execute(
                "INSERT INTO counterpart_roboflow_deliveries "
                "(id,snapshot_id,request_key,status) VALUES (%s,%s,%s,'planned') "
                "ON CONFLICT(snapshot_id) DO NOTHING RETURNING id",
                (delivery_id, row["id"], key),
            ).fetchone()
            if not created:
                previous = connection.execute(
                    "SELECT id,request_key FROM counterpart_roboflow_deliveries WHERE snapshot_id=%s",
                    (row["id"],),
                ).fetchone()
                if previous["request_key"] != key:
                    raise Conflict("delivery_exists", "This snapshot already has one delivery intent")
                delivery_id = str(previous["id"])
        return self.get_delivery(delivery_id)

    def get_delivery(self, delivery_id):
        with self.repo._connection() as connection:
            row = self._delivery(connection, delivery_id)
            events = connection.execute(
                "SELECT kind,data,created_at FROM counterpart_roboflow_delivery_events "
                "WHERE delivery_id=%s ORDER BY id",
                (row["id"],),
            ).fetchall()
            return _json(
                {
                    **row,
                    "events": events,
                    "generation_enabled": self.generation_limit > 0 and bool(self.budget_evidence),
                    "contents_verified": row["status"] == "verified",
                }
            )

    def _event(self, connection, delivery_id, kind, data):
        connection.execute(
            "INSERT INTO counterpart_roboflow_delivery_events(delivery_id,kind,data) VALUES (%s,%s,%s)",
            (delivery_id, kind, Jsonb(data)),
        )

    def advance(self, delivery_id):
        current = self.get_delivery(delivery_id)
        snapshot = self.get_snapshot(str(current["snapshot_id"]))["manifest"]
        if snapshot["destination"] != {
            "workspace": self.provider.workspace,
            "project": self.provider.project,
            "visibility": "private",
        }:
            raise Conflict("export_destination_mismatch", "Restore the frozen private destination")
        if current["status"] != "planned":
            # This read NEVER resubmits generate or adopts a newly observed version as its own.
            state = self.provider.project_state()
            with self.repo._connection() as connection:
                self._event(connection, delivery_id, "provider_versions_observed", state)
                if current["status"] in {"issuing", "uncertain"}:
                    candidates = [
                        item
                        for item in state.get("version_summaries", [])
                        if item["version"] not in current["before_versions"]
                    ]
                    connection.execute(
                        "UPDATE counterpart_roboflow_deliveries SET receipt=receipt || %s,"
                        "updated_at=clock_timestamp() WHERE id=%s AND status IN ('issuing','uncertain')",
                        (Jsonb({"reconciliation_candidates": candidates}), delivery_id),
                    )
                if current["status"] == "generated":
                    summary = next(
                        (
                            item
                            for item in state.get("version_summaries", [])
                            if item["version"] == current["provider_version"]
                        ),
                        None,
                    )
                    if summary is not None:
                        connection.execute(
                            "UPDATE counterpart_roboflow_deliveries SET receipt=receipt || %s,"
                            "updated_at=clock_timestamp() WHERE id=%s AND status='generated'",
                            (Jsonb({"provider_version_state": summary}), delivery_id),
                        )
            return self.get_delivery(delivery_id)
        if type(self.generation_limit) is not int or self.generation_limit <= 0 or not self.budget_evidence:
            raise Conflict(
                "generation_budget_unverified",
                "Configure a current included-credit allowance before generation",
            )
        expected = {
            item["asset_id"]: {
                k: item[k]
                for k in (
                    "asset_id",
                    "image_id",
                    "pair_id",
                    "session_id",
                    "split",
                    "width",
                    "height",
                    "machine_detections",
                )
            }
            for item in snapshot["images"]
        }
        observed = self._read_images(expected, exact=True, require_dataset=True)
        if digest(observed) != digest(snapshot["images"]):
            raise Conflict(
                "annotations_changed", "Annotations changed after approval; pull and review a new snapshot"
            )
        state = self.provider.project_state()
        # Commit the reservation and uncertainty boundary before sending any external write.
        with self.repo._connection() as connection:
            row = self._delivery(connection, delivery_id, lock=True)
            if row["status"] != "planned":
                return _json(row)
            connection.execute("SELECT pg_advisory_xact_lock(780245190230)")
            count = connection.execute(
                "SELECT count(*) AS n FROM counterpart_roboflow_deliveries WHERE generation_reserved"
            ).fetchone()["n"]
            if count >= self.generation_limit:
                raise Conflict(
                    "generation_budget_exhausted", "The reserved generation allowance is exhausted"
                )
            connection.execute(
                "UPDATE counterpart_roboflow_deliveries SET status='issuing',generation_reserved=true,"
                "budget_evidence_sha256=%s,before_versions=%s,updated_at=clock_timestamp() WHERE id=%s",
                (digest(self.budget_evidence), Jsonb(state["versions"]), delivery_id),
            )
            self._event(
                connection,
                delivery_id,
                "generation_reserved",
                {
                    "settings": _GENERATION_SETTINGS,
                    "snapshot_sha256": self.get_snapshot(str(current["snapshot_id"]))["content_sha256"],
                },
            )
        try:
            version = self.provider.generate()
            if version in state["versions"]:
                raise ProviderError("generation_outcome_unknown", ambiguous=True)
        except ProviderError as error:
            with self.repo._connection() as connection:
                connection.execute(
                    "UPDATE counterpart_roboflow_deliveries SET status=%s,last_error=%s,"
                    "updated_at=clock_timestamp() WHERE id=%s AND status='issuing'",
                    ("uncertain" if error.ambiguous else "failed", error.code, delivery_id),
                )
                self._event(
                    connection,
                    delivery_id,
                    "generation_response_failed",
                    {"code": error.code, "ambiguous": error.ambiguous},
                )
            return self.get_delivery(delivery_id)
        with self.repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_roboflow_deliveries SET status='generated',provider_version=%s,"
                "receipt=%s,updated_at=clock_timestamp() WHERE id=%s AND status='issuing'",
                (
                    version,
                    Jsonb(
                        {
                            "provider_ack": True,
                            "contents_verified": False,
                            "verification_required": "Import the original-size COCO export for this version",
                        }
                    ),
                    delivery_id,
                ),
            )
            self._event(connection, delivery_id, "generation_acknowledged", {"version": version})
        return self.get_delivery(delivery_id)

    def verify_coco(self, delivery_id, version, documents, *, reviewer="workspace-operator"):
        """Explicit official COCO-export import, NOT authenticated provider attestation.

        The operator imports train/valid/test _annotations.coco.json unchanged.
        Bytes, dimensions, every selected image, partition and corrected box set
        are bound to the approved snapshot. Source pixel bytes remain the original
        local archive; an imported JSON file cannot prove remote pixel identity.
        """
        if type(version) is not int or not 0 < version < 100000000:
            raise Conflict("invalid_provider_version", "Select a positive numeric provider version")
        current = self.get_delivery(delivery_id)
        reconcile = current["status"] in {"issuing", "uncertain"}
        if not reconcile and (
            current["status"] not in {"generated", "verified"} or current["provider_version"] != version
        ):
            raise Conflict("version_receipt_required", "A matching acknowledged hosted version is required")
        snapshot = self.get_snapshot(str(current["snapshot_id"]))["manifest"]
        proof = validate_coco(documents, snapshot["images"])
        state = current["receipt"].get("provider_version_state", {})
        if reconcile:
            state = next(
                (
                    item
                    for item in current["receipt"].get("reconciliation_candidates", [])
                    if item["version"] == version and version not in current["before_versions"]
                ),
                {},
            )
        if (
            state.get("ready") is not True
            or state.get("images") != proof["images"]
            or state.get("splits") != proof["split_counts"]
        ):
            raise Conflict(
                "version_not_ready", "Refresh hosted version status until its image and split counts match"
            )
        proof.update(
            {
                "version": version,
                "reviewer": reviewer,
                "source": "operator-imported-official-coco",
                "remote_pixel_identity_verified": False,
                "provider_authenticated_artifact": False,
                "write_receipt_recovered": current["receipt"].get("provider_ack") is True,
            }
        )
        with self.repo._connection() as connection:
            row = self._delivery(connection, delivery_id, lock=True)
            if row["status"] == "verified":
                if row["receipt"].get("artifact") != proof:
                    raise Conflict(
                        "delivery_verification_frozen", "This version already has an immutable artifact proof"
                    )
            else:
                receipt = {
                    **row["receipt"],
                    "contents_verified": True,
                    "artifact": proof,
                    "provider_version_state": state,
                }
                connection.execute(
                    "UPDATE counterpart_roboflow_deliveries SET status='verified',receipt=%s,"
                    "provider_version=%s,"
                    "updated_at=clock_timestamp() WHERE id=%s",
                    (Jsonb(receipt), version, delivery_id),
                )
                self._event(connection, delivery_id, "coco_membership_verified", proof)
        return self.get_delivery(delivery_id)

    def list_release(self, release_id):
        with self.repo._connection() as connection:
            if not connection.execute(
                "SELECT 1 FROM counterpart_dataset_releases WHERE id=%s", (identifier(release_id),)
            ).fetchone():
                raise NotFound("Release not found")
            snapshots = connection.execute(
                "SELECT s.id,s.content_sha256,s.created_at,a.reviewer,a.reason,"
                "a.created_at AS approved_at FROM counterpart_annotation_snapshots s "
                "LEFT JOIN counterpart_annotation_approvals a ON a.snapshot_id=s.id "
                "WHERE s.release_id=%s ORDER BY s.created_at DESC",
                (release_id,),
            ).fetchall()
            deliveries = connection.execute(
                "SELECT d.* FROM counterpart_roboflow_deliveries d JOIN "
                "counterpart_annotation_snapshots s ON s.id=d.snapshot_id "
                "WHERE s.release_id=%s ORDER BY d.created_at DESC",
                (release_id,),
            ).fetchall()
            return _json(
                {
                    "release_id": release_id,
                    "snapshots": snapshots,
                    "deliveries": deliveries,
                    "generation_enabled": self.generation_limit > 0 and bool(self.budget_evidence),
                }
            )


def validate_coco(documents, expected):
    """Validate exact source membership, splits and corrected boxes; reject extras."""

    def fail():
        raise Conflict(
            "coco_release_mismatch", "COCO files must match every approved source, split and annotation"
        )

    try:
        raw = json.dumps(documents, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        fail()
    if (
        len(raw) > MAX_JSON_BYTES
        or not isinstance(documents, dict)
        or not documents
        or set(documents) - {"train", "valid", "test"}
    ):
        fail()
    sources = {item["asset_id"]: item for item in expected}
    seen = set()
    for split, doc in documents.items():
        if not isinstance(doc, dict) or any(
            not isinstance(doc.get(k), list) for k in ("images", "annotations", "categories")
        ):
            fail()
        categories = {}
        for cat in doc["categories"]:
            if (
                not isinstance(cat, dict)
                or type(cat.get("id")) is not int
                or not isinstance(cat.get("name"), str)
                or cat["id"] in categories
            ):
                fail()
            categories[cat["id"]] = cat["name"]
        images = {}
        for image in doc["images"]:
            if not isinstance(image, dict) or type(image.get("id")) is not int or image["id"] in images:
                fail()
            name = image.get("file_name")
            if not isinstance(name, str):
                fail()
            # Official RF COCO exports keep upload stem, add extension + .rf.hash.
            match = re.fullmatch(
                r"counterpart-([a-f0-9]{64})(?:[._](?:png|jpg))(?:\.rf\.[a-f0-9]+)?\.(?:jpg|png)", name
            )
            if match is None:
                match = re.fullmatch(r"counterpart-([a-f0-9]{64})\.(?:png|jpg)", name)
            asset = match.group(1) if match else None
            if asset not in sources or asset in seen:
                fail()
            source = sources[asset]
            if (
                source["split"] != split
                or source["width"] != image.get("width")
                or source["height"] != image.get("height")
            ):
                fail()
            seen.add(asset)
            images[image["id"]] = {"source": source, "boxes": []}
        annotation_ids = set()
        for annotation in doc["annotations"]:
            if (
                not isinstance(annotation, dict)
                or type(annotation.get("id")) is not int
                or annotation["id"] in annotation_ids
                or type(annotation.get("image_id")) is not int
                or type(annotation.get("category_id")) is not int
                or annotation.get("image_id") not in images
                or annotation.get("category_id") not in categories
                or annotation.get("iscrowd", 0) != 0
                or annotation.get("segmentation") not in (None, [])
            ):
                fail()
            annotation_ids.add(annotation["id"])
            box = annotation.get("bbox")
            if not isinstance(box, list) or len(box) != 4:
                fail()
            try:
                box = [_number(value) for value in box]
            except ProviderError:
                fail()
            images[annotation["image_id"]]["boxes"].append(
                {"label": categories[annotation["category_id"]], "bbox": box}
            )
        for image in images.values():
            actual = sorted(image["boxes"], key=lambda row: (row["label"], row["bbox"]))
            wanted = image["source"]["boxes"]
            if len(actual) != len(wanted):
                fail()
            for a, b in zip(actual, wanted, strict=True):
                if a["label"] != b["label"] or any(
                    abs(x - y) > 0.01 for x, y in zip(a["bbox"], b["bbox"], strict=True)
                ):
                    fail()
    if seen != set(sources):
        fail()
    return {
        "sha256": digest(documents),
        "images": len(seen),
        "split_counts": {
            split: sum(item["split"] == split for item in expected) for split in ("train", "valid", "test")
        },
        "membership_splits_annotations_verified": True,
    }
