"""Real PostgreSQL delivery state machines and adversarial provider contracts.

Generated observations only. Hosted provider evidence is recorded separately.
"""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_exporting import run_all
from test_exporting import system as system

from counterpart.collections import CollectionRepository
from counterpart.providers import ProviderError
from counterpart.repository import Conflict
from counterpart.roboflow_delivery import (
    RoboflowDelivery,
    RoboflowDeliveryProvider,
    normalize_annotation,
    validate_coco,
)

pytestmark = pytest.mark.postgres


class FakeProvider:
    workspace = "test-workspace"
    project = "test-project"

    def __init__(self, source):
        self.images, self.annotations = [], {}
        self.versions = []
        self.calls = 0
        self.error = None
        self.private = True
        self.in_dataset = True
        for slot in ("baseline", "counterpart"):
            for frame in source.pair["manifest"][slot]["frames"]:
                asset = frame["asset_id"]
                iid = source.uploaded[asset]
                image = {
                    "id": iid,
                    "name": f"counterpart-{asset}.png",
                    "split": source.pair["manifest"]["split"],
                    "user_metadata": {
                        "counterpart_asset_id": asset,
                        "counterpart_media_sha256": asset,
                        "counterpart_pair_id": source.pair["id"],
                        "counterpart_session_id": source.pair["session_id"],
                    },
                }
                self.images.append(image)
                self.annotations[iid] = {
                    "id": iid,
                    "name": image["name"],
                    "split": image["split"],
                    "annotation": {
                        "key": iid + ".jpg",
                        "width": 100,
                        "height": 100,
                        "boxes": [{"label": "cup", "x": 30, "y": 30, "width": 20, "height": 20}],
                    },
                }

    def project_state(self):
        if not self.private:
            raise ProviderError("private_destination_required")
        return {
            "public": False,
            "versions": list(self.versions),
            "version_summaries": [
                {
                    "version": version,
                    "ready": True,
                    "images": len(self.images),
                    "splits": {
                        split: sum(image["split"] == split for image in self.images)
                        for split in ("train", "valid", "test")
                    },
                }
                for version in self.versions
            ],
        }

    def inventory(self, *, in_dataset=None):
        if in_dataset is True and not self.in_dataset:
            return []
        return copy.deepcopy(self.images)

    def annotation(self, image_id):
        return copy.deepcopy(self.annotations[image_id])

    def generate(self):
        self.calls += 1
        if self.error:
            raise self.error
        self.versions.append(len(self.versions) + 1)
        return self.versions[-1]


@pytest.fixture
def delivery(system):
    with system.repo._connection() as conn:
        conn.execute(Path("src/counterpart/delivery_schema.sql").read_text())
    run_all(system)
    collections = CollectionRepository(system.repo)
    campaign_id = system.pair["manifest"]["campaign_id"]
    release = collections.export_manifests(campaign_id)
    provider = FakeProvider(system)
    service = RoboflowDelivery(
        system.repo, provider, generation_limit=2, budget_evidence="Synthetic test budget"
    )
    return SimpleNamespace(source=system, release=release, provider=provider, service=service)


def snapshot(rig, key=None):
    return rig.service.sync_annotations(rig.release["id"], key or uuid4().hex)


def planned(rig):
    snap = snapshot(rig)
    rig.service.approve(snap["id"], uuid4().hex, "Inspected synthetic corrected boxes")
    return rig.service.request_delivery(snap["id"], uuid4().hex)


def coco(snap):
    docs = {}
    for index, image in enumerate(snap["manifest"]["images"]):
        doc = docs.setdefault(
            image["split"], {"images": [], "annotations": [], "categories": [{"id": 0, "name": "cup"}]}
        )
        doc["images"].append(
            {
                "id": index,
                "file_name": image["remote_name"],
                "width": image["width"],
                "height": image["height"],
            }
        )
        for box in image["boxes"]:
            doc["annotations"].append(
                {
                    "id": len(doc["annotations"]),
                    "image_id": index,
                    "category_id": 0,
                    "bbox": list(box["bbox"]),
                    "iscrowd": 0,
                }
            )
    return docs


def test_snapshot_exact_retry_and_corrections_do_not_mutate_pair(delivery):
    original = copy.deepcopy(delivery.source.pair["manifest"])
    first = snapshot(delivery, "one-snapshot-request")
    iid = delivery.provider.images[0]["id"]
    delivery.provider.annotations[iid]["annotation"]["boxes"][0]["x"] = 40
    assert snapshot(delivery, "one-snapshot-request") == first
    changed = snapshot(delivery)
    assert changed["content_sha256"] != first["content_sha256"]
    assert first["manifest"]["provider_review_attested"] is False
    assert first["approval"] is None
    assert (
        CollectionRepository(delivery.source.repo).get_pair(delivery.source.pair["id"])["manifest"]
        == original
    )


def test_snapshot_concurrent_retry_single_record(delivery):
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: snapshot(delivery, "same-concurrent-request"), range(3)))
    assert len({row["id"] for row in rows}) == 1
    assert len(delivery.service.list_release(delivery.release["id"])["snapshots"]) == 1


def test_review_required_and_frozen(delivery):
    snap = snapshot(delivery)
    with pytest.raises(Conflict, match="Review the returned"):
        delivery.service.request_delivery(snap["id"], uuid4().hex)
    reviewed = delivery.service.approve(snap["id"], "snapshot-approval", "Checked every image")
    assert delivery.service.approve(snap["id"], "snapshot-approval", "Checked every image") == reviewed
    with pytest.raises(Conflict, match="immutable"):
        delivery.service.approve(snap["id"], "different-review-key", "Change history")


def test_budget_default_blocks_before_generation(delivery):
    item = planned(delivery)
    delivery.service.generation_limit = 0
    with pytest.raises(Conflict) as error:
        delivery.service.advance(item["id"])
    assert error.value.code == "generation_budget_unverified"
    assert delivery.provider.calls == 0
    assert delivery.service.get_delivery(item["id"])["generation_reserved"] is False


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("extra", "project_inventory_mismatch"),
        ("split", "remote_source_mismatch"),
        ("hash", "remote_source_mismatch"),
        ("boxes", "annotations_changed"),
        ("not_dataset", "annotations_not_in_dataset"),
    ],
)
def test_generation_rejects_drift_and_unrelated_images(delivery, mutation, code):
    item = planned(delivery)
    first = delivery.provider.images[0]
    if mutation == "extra":
        delivery.provider.images.append({**copy.deepcopy(first), "id": "unapproved-other-image"})
    elif mutation == "split":
        first["split"] = "valid" if first["split"] != "valid" else "test"
    elif mutation == "hash":
        first["user_metadata"]["counterpart_media_sha256"] = "0" * 64
    elif mutation == "boxes":
        delivery.provider.annotations[first["id"]]["annotation"]["boxes"][0]["x"] = 40
    else:
        delivery.provider.in_dataset = False
    with pytest.raises(Conflict) as error:
        delivery.service.advance(item["id"])
    assert error.value.code == code
    assert delivery.provider.calls == 0


def test_privacy_checked_again_before_effect(delivery):
    item = planned(delivery)
    delivery.provider.private = False
    with pytest.raises(ProviderError, match="private_destination_required"):
        delivery.service.advance(item["id"])
    assert delivery.provider.calls == 0


def test_ambiguous_generation_keeps_reservation_never_retries_or_adopts(delivery):
    item = planned(delivery)
    delivery.provider.error = ProviderError("generation_outcome_unknown", ambiguous=True)
    result = delivery.service.advance(item["id"])
    assert result["status"] == "uncertain" and result["generation_reserved"]
    delivery.provider.versions = [7]  # Could belong to another operator. Not ours by inference.
    for _ in range(3):
        result = delivery.service.advance(item["id"])
    assert result["status"] == "uncertain" and result["provider_version"] is None
    assert delivery.provider.calls == 1


def test_concurrent_advances_submit_generation_once(delivery):
    item = planned(delivery)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: delivery.service.advance(item["id"]), range(4)))
    assert delivery.provider.calls == 1
    assert delivery.service.get_delivery(item["id"])["status"] == "generated"


def test_crash_issuing_boundary_never_reissues(delivery):
    item = planned(delivery)
    with delivery.source.repo._connection() as conn:
        conn.execute(
            "UPDATE counterpart_roboflow_deliveries SET status='issuing',generation_reserved=true "
            "WHERE id=%s",
            (item["id"],),
        )
    assert delivery.service.advance(item["id"])["status"] == "issuing"
    assert delivery.provider.calls == 0


def test_failed_and_ambiguous_calls_consume_bounded_allowance(delivery):
    delivery.service.generation_limit = 1
    first = planned(delivery)
    delivery.provider.error = ProviderError("provider_auth_failed")
    assert delivery.service.advance(first["id"])["status"] == "failed"
    second = planned(delivery)
    with pytest.raises(Conflict) as error:
        delivery.service.advance(second["id"])
    assert error.value.code == "generation_budget_exhausted"
    assert delivery.provider.calls == 1


def test_ack_is_not_membership_verified_then_full_coco_roundtrip(delivery):
    item = planned(delivery)
    result = delivery.service.advance(item["id"])
    assert result["status"] == "generated" and result["contents_verified"] is False
    snap = delivery.service.get_snapshot(result["snapshot_id"])
    documents = coco(snap)
    delivery.service.advance(item["id"])
    verified = delivery.service.verify_coco(item["id"], result["provider_version"], documents)
    assert verified["status"] == "verified"
    assert verified["receipt"]["artifact"]["provider_authenticated_artifact"] is False
    assert verified["receipt"]["artifact"]["remote_pixel_identity_verified"] is False
    assert delivery.service.verify_coco(item["id"], result["provider_version"], documents) == verified
    assert delivery.provider.calls == 1


@pytest.mark.parametrize(
    "attack",
    [
        "extra_image",
        "missing_image",
        "wrong_split",
        "changed_box",
        "extra_box",
        "bad_category",
        "wrong_dimensions",
        "path_traversal",
        "missing_annotation",
        "nan",
        "duplicate_id",
        "boolean_id",
        "segmentation",
    ],
)
def test_coco_rejects_mismatched_export(delivery, attack):
    snap = snapshot(delivery)
    documents = coco(snap)
    split, document = next(iter(documents.items()))
    if attack == "extra_image":
        document["images"].append({**document["images"][0], "id": 88, "file_name": "other.jpg"})
    elif attack == "missing_image":
        document["images"].pop()
    elif attack == "wrong_split":
        documents["valid" if split != "valid" else "test"] = documents.pop(split)
    elif attack == "changed_box":
        document["annotations"][0]["bbox"][0] += 1
    elif attack == "extra_box":
        document["annotations"].append({**document["annotations"][0], "id": 88})
    elif attack == "bad_category":
        document["annotations"][0]["category_id"] = 99
    elif attack == "wrong_dimensions":
        document["images"][0]["width"] = 99
    elif attack == "path_traversal":
        document["images"][0]["file_name"] = "../" + document["images"][0]["file_name"]
    elif attack == "missing_annotation":
        document["annotations"].pop()
    elif attack == "nan":
        document["annotations"][0]["bbox"][0] = float("nan")
    elif attack == "duplicate_id":
        document["images"].append(document["images"][0])
    elif attack == "boolean_id":
        document["images"][0]["id"] = True
    else:
        document["annotations"][0]["segmentation"] = [[1, 2, 3]]
    with pytest.raises(Conflict):
        validate_coco(documents, snap["manifest"]["images"])


@pytest.mark.parametrize("change", ["polygon", "outside", "nan", "wrong_size"])
def test_unknown_geometry_fails_closed(change):
    annotation = {
        "width": 100,
        "height": 100,
        "boxes": [{"label": "cup", "x": 50, "y": 50, "width": 20, "height": 20}],
    }
    if change == "polygon":
        annotation["boxes"][0]["points"] = [[1, 2]]
    elif change == "outside":
        annotation["boxes"][0]["x"] = 101
    elif change == "nan":
        annotation["boxes"][0]["x"] = float("nan")
    else:
        annotation["width"] = 101
    with pytest.raises(ProviderError):
        normalize_annotation(annotation, 100, 100)


def test_provider_wire_contract_and_bounded_sanitized_failures():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/generate"):
            assert json.loads(request.content) == {"preprocessing": {}, "augmentation": {}}
            return httpx.Response(200, json={"version": 3})
        if "/images/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "image": {
                        "id": "iid",
                        "name": "fixture.png",
                        "split": "test",
                        "annotation": {"width": 1, "height": 1, "boxes": []},
                        "urls": {"secret": "never_return"},
                    }
                },
            )
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "project": {"id": "workspace/project", "public": False},
                "versions": [{"id": "workspace/project/2"}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with RoboflowDeliveryProvider("test-private-key", "workspace", "project", client=client) as provider:
            assert provider.project_state()["versions"] == [2]
            assert "urls" not in provider.annotation("iid")
            assert provider.inventory() == []
            assert provider.generate() == 3
    assert [r.method for r in calls] == ["GET", "GET", "POST", "POST"]


def human_live_pair(system):
    """Production live-mode contracts with generated pixels/observations, no model call."""
    from psycopg.types.json import Jsonb

    from counterpart.evidence_revisions import CRITERIA, EvidenceRepository
    from counterpart.fixtures import generate
    from counterpart.recipes import build_recipe_snapshot
    from counterpart.verification import verify

    repo = system.repo
    collections = CollectionRepository(repo)
    campaign = collections.create_campaign("Synthetic live-contract adjudication", "spatial", "live")
    # Explicit historical live v1 contract: generated flat cards are not camera-registration evidence.
    with repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_campaigns SET recipe_snapshot=%s WHERE id=%s",
            (Jsonb(build_recipe_snapshot("spatial")), campaign["id"]),
        )
    invite = collections.create_invite(campaign["id"])
    session = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
    first = None
    for slot in ("baseline", "counterpart"):
        frames = []
        for fixture in generate("spatial", slot):
            detections = copy.deepcopy(fixture["detections"])
            if slot == "counterpart":
                detections[0]["confidence"] = 0.50
            frames.append(
                {
                    **system.media.put(fixture["bytes"]),
                    "t_ms": fixture["t_ms"],
                    "detections": detections,
                    "model": {"provider": "roboflow", "model_id": "coco/39"},
                    "inference": {"configuration_digest": session["execution_contract"]["digest"]},
                }
            )
        key = uuid4().hex
        saved = repo.submit(
            session["id"],
            {
                "slot": slot,
                "expected_revision": session["revision"],
                "epoch": session["epoch"],
                "idempotency_key": key,
                "client_seq": 0,
            },
            frames,
            key,
        )
        job = repo.claim("synthetic-observations-no-model")
        assert job["capture"]["id"] == saved["capture"]["id"]
        reference = (
            None
            if slot == "baseline"
            else {**session["baseline"]["result"], "frames": session["baseline"]["frames"]}
        )
        result = verify(
            "spatial",
            slot,
            frames,
            reference,
            recipe_snapshot=session["recipe_snapshot"],
            media_reader=system.media.read,
        )
        assert slot != "baseline" or result["accepted"], result
        repo.finish(job["id"], job["lease_token"], result, frames)
        session = repo.snapshot(session["id"])
        if slot == "baseline":
            first = session["latest_capture"]["id"]
    evidence = EvidenceRepository(repo, system.media)
    last = session["latest_capture"]["id"]
    detail = evidence.detail(session["id"], first, last)
    pair = evidence.adjudicate(
        session["id"],
        baseline_id=first,
        counterpart_id=last,
        expected_session_revision=session["revision"],
        expected_evidence_sha256=detail["selected_pair"]["evidence_sha256"],
        labels=detail["recipe_snapshot"]["expected_answers"],
        criteria=dict.fromkeys(CRITERIA, True),
        reason="Synthetic reviewer inspects saved generated media and every fixed control.",
        idempotency_key=uuid4().hex,
    )
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reviewer="synthetic-test-reviewer",
        idempotency_key=uuid4().hex,
    )
    return pair


def test_human_adjudicated_live_pair_exports_original_predictions_only(system):
    run_all(system)
    pair = human_live_pair(system)
    assert pair["manifest"]["counterpart"]["result"]["accepted"] is False
    original = copy.deepcopy(pair["manifest"])
    CollectionRepository(system.repo).request_export(
        pair["id"], system.destination, "synthetic-test-reviewer"
    )
    before = len(system.calls)
    run_all(system)
    with system.repo._connection() as conn:
        row = conn.execute(
            "SELECT status FROM counterpart_export_intents WHERE pair_id=%s", (pair["id"],)
        ).fetchone()
    assert row["status"] == "complete"
    calls = system.calls[before:]
    assert sum("/upload" in req.url.path for req in calls) == 2
    annotations = [req for req in calls if "/annotate/" in req.url.path]
    assert len(annotations) == 2
    assert all(
        req.url.params["prediction"] == "true" and req.url.params["addToDataset"] == "false"
        for req in annotations
    )
    assert CollectionRepository(system.repo).get_pair(pair["id"])["manifest"] == original


@pytest.mark.parametrize("mutation", ["record", "control", "source"])
def test_tampered_adjudication_never_dispatches(system, mutation):
    from psycopg.types.json import Jsonb

    run_all(system)
    pair = human_live_pair(system)
    CollectionRepository(system.repo).request_export(
        pair["id"], system.destination, "synthetic-test-reviewer"
    )
    damaged = copy.deepcopy(pair["manifest"])
    if mutation == "record":
        damaged["evidence_adjudication"]["reason"] = "Mutated review history without its original digest."
    elif mutation == "control":
        damaged["evidence_adjudication"]["criteria"][
            next(iter(damaged["evidence_adjudication"]["criteria"]))
        ] = False
    else:
        damaged["counterpart"]["frames"][0]["asset_id"] = "a" * 64
    with system.repo._connection() as conn:
        conn.execute("UPDATE counterpart_pairs SET manifest=%s WHERE id=%s", (Jsonb(damaged), pair["id"]))
    before = len(system.calls)
    assert system.worker.once()
    assert len(system.calls) == before


def test_uncertain_generation_explicit_artifact_reconciliation_never_repeats_write(delivery):
    item = planned(delivery)
    delivery.provider.error = ProviderError("generation_outcome_unknown", ambiguous=True)
    result = delivery.service.advance(item["id"])
    assert result["status"] == "uncertain"
    delivery.provider.versions = [7]
    observed = delivery.service.advance(item["id"])
    assert observed["provider_version"] is None
    assert observed["receipt"]["reconciliation_candidates"][0]["version"] == 7
    snap = delivery.service.get_snapshot(item["snapshot_id"])
    documents = coco(snap)
    verified = delivery.service.verify_coco(item["id"], 7, documents)
    assert verified["status"] == "verified" and verified["provider_version"] == 7
    assert verified["receipt"]["artifact"]["write_receipt_recovered"] is False
    assert delivery.service.verify_coco(item["id"], 7, documents) == verified
    assert delivery.provider.calls == 1


def test_old_provider_version_cannot_be_adopted_after_uncertain_call(delivery):
    delivery.provider.versions = [6]
    item = planned(delivery)
    delivery.provider.error = ProviderError("generation_outcome_unknown", ambiguous=True)
    delivery.service.advance(item["id"])
    delivery.service.advance(item["id"])
    snap = delivery.service.get_snapshot(item["snapshot_id"])
    with pytest.raises(Conflict) as error:
        delivery.service.verify_coco(item["id"], 6, coco(snap))
    assert error.value.code == "version_not_ready"
    assert delivery.provider.calls == 1


def test_import_before_hosted_readiness_does_not_claim_completion(delivery):
    item = planned(delivery)
    result = delivery.service.advance(item["id"])
    snap = delivery.service.get_snapshot(item["snapshot_id"])
    with pytest.raises(Conflict) as error:
        delivery.service.verify_coco(item["id"], result["provider_version"], coco(snap))
    assert error.value.code == "version_not_ready"
    assert delivery.service.get_delivery(item["id"])["status"] == "generated"


def test_delivery_api_operator_boundary_and_full_review_flow(delivery, monkeypatch):
    from fastapi.testclient import TestClient

    from counterpart.api import create_app
    from counterpart.config import Settings

    token = "only-a-synthetic-local-operator-token-never-a-provider"
    config = Settings(
        database_url=delivery.source.repo.dsn,
        media_dir=delivery.source.media.root,
        require_operator=True,
        operator_token=token,
        operator_cookie_secure=True,
        allowed_origins=["https://testserver"],
        trusted_hosts=["testserver"],
        export_enabled=True,
        export_project="test-project",
        roboflow_generation_limit=2,
        roboflow_generation_budget_evidence="Synthetic test allowance",
    )
    app = create_app(config, delivery.source.repo)
    app.state.destination = SimpleNamespace(resolve=lambda **kwargs: delivery.source.destination)
    delivery.provider.close = lambda: None
    monkeypatch.setattr(
        "counterpart.roboflow_delivery_api.RoboflowDeliveryProvider",
        lambda *args, **kwargs: delivery.provider,
    )
    release_path = f"/api/operator/releases/{delivery.release['id']}/roboflow"
    key = {"idempotency_key": uuid4().hex}
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get(release_path).status_code == 401
        assert client.post(release_path + "/snapshots", json=key).status_code == 401
        assert client.post("/api/operator/login", json={"token": token}).status_code == 200
        response = client.post(release_path + "/snapshots", json=key)
        assert response.status_code == 201, response.text
        snap = response.json()
        path = "/api/operator/roboflow/snapshots/" + snap["id"]
        assert client.get(path).status_code == 200
        assert client.post(path + "/deliver", json=key).status_code == 409
        assert (
            client.post(
                path + "/approve", json={**key, "reason": "All generated corrected boxes inspected."}
            ).status_code
            == 200
        )
        result = client.post(path + "/deliver", json=key)
        assert result.status_code == 202, result.text
        item = result.json()
        delivery_path = "/api/operator/roboflow/deliveries/" + item["id"]
        generated = client.post(delivery_path + "/advance").json()
        assert generated["status"] == "generated"
        assert client.post(delivery_path + "/advance").status_code == 200
        response = client.post(
            delivery_path + "/verify-coco",
            json={"version": generated["provider_version"], "documents": coco(snap)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["contents_verified"] is True
        assert client.get(release_path).json()["snapshots"][0]["approved_at"]
        client.cookies.clear()
        for suffix in ["", "/advance", "/verify-coco"]:
            if not suffix:
                response = client.get(delivery_path)
            elif suffix == "/verify-coco":
                response = client.post(delivery_path + suffix, json={"version": 1, "documents": {}})
            else:
                response = client.post(delivery_path + suffix)
            assert response.status_code == 401


@pytest.mark.parametrize("value", ["1", 1, "99999999"])
def test_generation_accepts_sdk_numeric_version_wire_forms(value):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"version": value}))
    ) as h:
        with RoboflowDeliveryProvider("test-key", "workspace", "project", client=h) as provider:
            assert provider.generate() == int(value)


@pytest.mark.parametrize("value", [True, 1.0, "1.0", "01", "-1", "0", "100000000", None, {}])
def test_generation_refuses_malformed_version_ack_without_repeating(value):
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json={"version": value})

    with httpx.Client(transport=httpx.MockTransport(response)) as h:
        with RoboflowDeliveryProvider("test-key", "workspace", "project", client=h) as provider:
            with pytest.raises(ProviderError) as error:
                provider.generate()
            assert error.value.ambiguous
            assert len(calls) == 1


def test_actual_hosted_sparse_partition_metadata_and_generated_coco_regression():
    # Saved public generated-fixture API/export data, no provider call or human/ML claim.
    report = json.loads(Path("research/roboflow_version_probe.json").read_text())
    documents = json.loads(Path("research/roboflow_version_probe_coco.json").read_text())
    expected = [item for item in report["frozen_inventory"] if item["in_dataset"]]
    assert validate_coco(documents, expected) == report["coco_proof"]
    payload = {
        "project": {"id": "workspace/project", "public": False},
        "versions": [{"id": "workspace/project/1", "images": 1, "splits": {"test": 1}, "generating": False}],
    }
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as h:
        with RoboflowDeliveryProvider("test-key", "workspace", "project", client=h) as provider:
            assert provider.project_state()["version_summaries"][0]["splits"] == {
                "train": 0,
                "valid": 0,
                "test": 1,
            }
