#!/usr/bin/env python3
"""One-time, bounded hosted version probe using generated cards only.

The durable ledger refuses a second generate write even if the first process
lost its response. Production settings and camera export remain untouched.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageDraw

from counterpart.providers import ProviderError, voc_prediction
from counterpart.roboflow_delivery import (
    RoboflowDeliveryProvider,
    digest,
    normalize_annotation,
    validate_coco,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = Path.home() / ".config/counterpart/provider-probes/hosted-version-20260927.json"
REPORT = ROOT / "research/roboflow_version_probe.json"
PROJECT = "counterpart-integration-test"
PRIVATE_PROJECT = "counterpart-delivery-private-test"
BASE = "https://api.roboflow.com"
MAX_BYTES = 32 * 1024 * 1024


def saved(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def request(client, key, method, path, *, payload=None):
    try:
        response = client.request(
            method, BASE + path, params={"api_key": key}, json=payload, timeout=20, follow_redirects=False
        )
        if len(response.content) > 2 * 1024 * 1024:
            raise RuntimeError("provider_response_too_large")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("invalid_provider_response")
        return response.status_code, data
    except httpx.HTTPError:
        raise RuntimeError("provider_transport_outcome_unknown") from None
    except ValueError:
        raise RuntimeError("invalid_provider_response") from None


def card():
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((160, 96, 479, 287), fill="navy")
    draw.text((20, 350), "Generated annotation transport test. Not camera or model evidence.", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def download(client, url):
    for _ in range(5):
        parts = urlparse(url)
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or parts.hostname not in {"app.roboflow.com", "storage.googleapis.com"}
        ):
            raise RuntimeError("unexpected_artifact_download_host")
        with client.stream("GET", url, timeout=30, follow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code != 200:
                raise RuntimeError("artifact_download_failed")
            data = bytearray()
            for chunk in response.iter_bytes(16384):
                if len(data) + len(chunk) > MAX_BYTES:
                    raise RuntimeError("artifact_download_too_large")
                data.extend(chunk)
            return bytes(data)
    raise RuntimeError("artifact_redirect_limit")


def main():
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = (
            json.loads(LEDGER.read_text())
            if LEDGER.exists()
            else {
                "schema_version": "counterpart.generated-provider-probe.v1",
                "created_at": datetime.now(UTC).isoformat(),
                "generation_allowance": 1,
                "generation_attempts_reserved": 0,
                "camera_images": 0,
                "training_calls": 0,
                "production_configuration_changed": False,
                "user_credit_authorization": "User reaffirmed plentiful Roboflow credits on 2026-09-27 UTC",
            }
        )

        def record(**fields):
            state.update(fields)
            saved(LEDGER, state)
            saved(REPORT, state)

        credentials = json.loads((Path.home() / ".config/coveragecv/credentials.json").read_text())
        key, workspace = credentials["ROBOFLOW_API_KEY"], credentials["ROBOFLOW_WORKSPACE"]
        if workspace != "ryan-lin-khj4s":
            raise RuntimeError("unexpected_workspace")
        with httpx.Client(trust_env=False, headers={"Accept-Encoding": "identity"}) as client:
            status, plan = request(client, key, "GET", "/usage/plan")
            if status != 200 or plan != {
                "is_enterprise": False,
                "is_pro": False,
                "is_trial": False,
                "over_quota": False,
            }:
                raise RuntimeError("verified_free_plan_required")
            record(
                account_plan=plan,
                billing_evidence={
                    "plan_endpoint": "/usage/plan",
                    "rate_source": "https://roboflow.com/credits",
                    "version_images_per_credit": 20000,
                    "cash_control": (
                        "Official credit FAQ: free plans stop when included/prepaid credits are exhausted"
                    ),
                },
            )
            # A one-time empty private-project entitlement attempt; never buy or upgrade.
            if "private_project_attempt" not in state:
                record(private_project_attempt="issuing")
                status, data = request(
                    client,
                    key,
                    "POST",
                    "/" + workspace + "/projects",
                    payload={
                        "name": "Counterpart Delivery Private Test",
                        "type": "object-detection",
                        "license": "Private",
                        "annotation": "generated fixture boxes",
                    },
                )
                if (
                    status == 200
                    and data.get("public") is False
                    and data.get("id") == workspace + "/" + PRIVATE_PROJECT
                ):
                    record(private_project_attempt="confirmed", project=PRIVATE_PROJECT, project_public=False)
                elif status in (400, 403, 422):
                    record(
                        private_project_attempt="unavailable",
                        private_project_http_status=status,
                        project=PROJECT,
                        project_public=True,
                    )
                else:
                    record(private_project_attempt="uncertain", private_project_http_status=status)
                    raise RuntimeError("private_project_outcome_unknown_no_retry")
            if state["private_project_attempt"] not in ("confirmed", "unavailable"):
                raise RuntimeError("private_project_outcome_unknown_no_retry")
            project = state["project"]
            with RoboflowDeliveryProvider(key, workspace, project) as provider:
                if not state["project_public"] and "fixture_upload" not in state:
                    data = card()
                    asset = hashlib.sha256(data).hexdigest()
                    record(fixture_upload="issuing", fixture_sha256=asset)
                    receipt = provider.upload(
                        data,
                        asset_id=asset,
                        pair_id="generated-version-probe",
                        session_id="generated-version-probe",
                        split="test",
                        media_type="image/png",
                    )
                    record(fixture_upload="confirmed", fixture_image_id=receipt["image_id"])
                if not state["project_public"] and state.get("fixture_annotation") is None:
                    if state.get("fixture_upload") != "confirmed":
                        raise RuntimeError("fixture_upload_unknown_no_retry")
                    iid = state["fixture_image_id"]
                    annotation = voc_prediction(
                        [{"label": "generated_card_test", "confidence": 1, "box": [0.2, 0.2, 0.6, 0.6]}],
                        800,
                        480,
                        filename=iid + ".jpg",
                    )
                    record(fixture_annotation="issuing")
                    receipt = provider._request(
                        BASE + "/dataset/" + project + "/annotate/" + iid,
                        mutating=True,
                        params={
                            "api_key": key,
                            "name": "annotation.xml",
                            "prediction": "false",
                            "overwrite": "false",
                            "addToDataset": "true",
                        },
                        json={"annotationFile": annotation, "labelmap": None},
                    )
                    if receipt.get("success") is not True:
                        raise RuntimeError("fixture_annotation_unknown_no_retry")
                    record(fixture_annotation="confirmed")
                complete = provider.inventory()
                dataset = provider.inventory(in_dataset=True)
                allowed = {
                    "6afa0f8f1d4390067a6a3b61f8155268319d5d3916b25e2b5b8fabf284339780",
                    "8811df4d0498b5549def098f338f16f5bce0a02f8b6a66c0ac6783009741b544",
                }
                if not complete or not dataset or len(complete) > 3 or len(dataset) > 2:
                    raise RuntimeError("unexpected_fixture_inventory")
                expected = []
                inventory = []
                dataset_ids = {item["id"] for item in dataset}
                for row in complete:
                    asset = row.get("user_metadata", {}).get("counterpart_asset_id")
                    if asset not in allowed or row["user_metadata"].get("counterpart_media_sha256") != asset:
                        raise RuntimeError("nonfixture_asset_refused")
                    remote = provider.annotation(row["id"])
                    ann = remote["annotation"]
                    normalized = normalize_annotation(ann, 800, 480)
                    item = {
                        "asset_id": asset,
                        "image_id": row["id"],
                        "remote_name": remote["name"],
                        "split": row["split"],
                        "width": 800,
                        "height": 480,
                        "boxes": normalized,
                        "annotation_sha256": digest(ann),
                        "in_dataset": row["id"] in dataset_ids,
                    }
                    inventory.append(item)
                    if item["in_dataset"]:
                        expected.append(item)
                if "frozen_inventory" in state and state["frozen_inventory"] != inventory:
                    raise RuntimeError("frozen_fixture_inventory_changed")
                before = provider.project_state(require_private=not state["project_public"])
                if state["generation_attempts_reserved"] == 0:
                    record(
                        frozen_inventory=inventory,
                        before_versions=before["versions"],
                        max_version_images=len(dataset),
                        estimated_version_credits=len(dataset) / 20000,
                        generation_attempts_reserved=1,
                        generation_status="issuing",
                        settings={"preprocessing": {}, "augmentation": {}},
                    )
                    try:
                        version = provider.generate()
                    except ProviderError as error:
                        record(
                            generation_status="uncertain" if error.ambiguous else "refused",
                            generation_error=error.code,
                            generation_http_status=error.status_code,
                        )
                        raise RuntimeError("generation_not_confirmed_no_retry") from None
                    record(generation_status="acknowledged", provider_version=version)
                    print("One generation acknowledged; polling readonly metadata.", flush=True)
                if state.get("generation_status") == "uncertain":
                    # A candidate is inspected, never treated as a recovered write ACK.
                    candidates = [v for v in before["versions"] if v not in state["before_versions"]]
                    if len(candidates) != 1:
                        raise RuntimeError("generation_candidate_ambiguous_no_retry")
                    record(
                        provider_version=candidates[0],
                        generation_receipt_confirmed=False,
                        provider_version_source="single_observed_candidate_not_write_receipt",
                    )
                elif state.get("generation_status") not in ("acknowledged", "verified"):
                    raise RuntimeError("generation_unknown_no_retry")
                version = state["provider_version"]
                version_state = None
                for _ in range(24):
                    current = provider.project_state(require_private=not state["project_public"])
                    version_state = next(
                        (v for v in current.get("version_summaries", []) if v["version"] == version), None
                    )
                    if version_state and version_state["ready"]:
                        break
                    time.sleep(5)
                if not version_state or not version_state["ready"]:
                    record(version_state=version_state)
                    raise RuntimeError("version_not_ready_resume_reads_only")
                record(version_state=version_state)
                export = None
                for _ in range(24):
                    status, payload = request(client, key, "GET", f"/{workspace}/{project}/{version}/coco")
                    if status == 200 and isinstance(payload.get("export"), dict):
                        export = payload["export"]
                        break
                    if status not in (200, 202):
                        record(export_http_status=status)
                        raise RuntimeError("official_coco_export_unavailable")
                    time.sleep(5)
                if export is None or not isinstance(export.get("link"), str):
                    raise RuntimeError("official_export_not_ready_resume_reads_only")
                data = download(client, export["link"])
                artifact = ROOT / "artifacts/roboflow-hosted-version-probe.zip"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(data)
                documents = {}
                image_files = {}
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    infos = archive.infolist()
                    if (
                        len(infos) > 100
                        or len({i.filename for i in infos}) != len(infos)
                        or sum(i.file_size for i in infos) > MAX_BYTES
                    ):
                        raise RuntimeError("unexpected_export_size")
                    for info in infos:
                        path = Path(info.filename)
                        if path.is_absolute() or ".." in path.parts or info.file_size > 8 * 1024 * 1024:
                            raise RuntimeError("unsafe_export_member")
                        if path.name == "_annotations.coco.json":
                            split = path.parent.name
                            if split not in {"train", "valid", "test"} or split in documents:
                                raise RuntimeError("unexpected_coco_partition")
                            documents[split] = json.loads(archive.read(info))
                        elif path.suffix.lower() in {".jpg", ".png"}:
                            content = archive.read(info)
                            with Image.open(io.BytesIO(content)) as image:
                                image.verify()
                            image_files[info.filename] = {
                                "bytes": len(content),
                                "sha256": hashlib.sha256(content).hexdigest(),
                            }
                declared_paths = {
                    split + "/" + image["file_name"]
                    for split, doc in documents.items()
                    for image in doc["images"]
                }
                if set(image_files) != declared_paths:
                    raise RuntimeError("export_image_membership_mismatch")
                proof = validate_coco(documents, expected)
                if (
                    version_state["images"] != len(expected)
                    or version_state["splits"] != proof["split_counts"]
                ):
                    raise RuntimeError("hosted_version_count_mismatch")
                record(
                    generation_status=state["generation_status"],
                    artifact_status="verified",
                    verified_at=datetime.now(UTC).isoformat(),
                    official_export_sha256=hashlib.sha256(data).hexdigest(),
                    official_export_bytes=len(data),
                    image_files=image_files,
                    coco_proof=proof,
                    artifact_provenance=(
                        "downloaded from provider-returned official signed URL; URL not persisted"
                    ),
                    human_capture_claim=False,
                    model_accuracy_claim=False,
                    production_private_e2e=False,
                    private_fixture_transport=not state["project_public"],
                )
                print("Hosted generated-fixture version and genuine COCO export verified.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Neither HTTP URLs nor arbitrary provider payloads are safe error output.
        print(
            "Probe stopped safely:",
            type(error).__name__,
            str(error) if isinstance(error, RuntimeError) else "",
        )
        raise SystemExit(1) from None
