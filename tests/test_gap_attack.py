"""Independent adversarial checks for closure of the original scope gaps.

Only deterministic fixtures and local protocol/database work; no model calls.
"""

import asyncio
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from PIL import Image, ImageDraw
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from counterpart.collections import CollectionRepository
from counterpart.config import Settings
from counterpart.execution_contract import (
    ExecutionContractError,
    build_execution_contract,
    validate_execution_contract,
)
from counterpart.inference_runtime import InferenceRuntime
from counterpart.providers import ProviderError
from counterpart.repository import Conflict, Repository
from counterpart.storage import MediaStore


class _ForbiddenDependency:
    def __getattr__(self, name):
        raise AssertionError(f"Contract refusal must precede dependency access: {name}")


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("model_id", "coco/40"),
        ("endpoint", "https://alternate.example"),
        ("provider", "different-provider"),
        ("adapter", "different.Adapter"),
        ("adapter_version", "serverless-v0-v2"),
        ("normalizer_version", "rf-xyxy-v2"),
        ("confidence_threshold", 0.5),
        ("disable_active_learning", False),
    ],
)
def test_model_semantics_drift_refused_before_storage_database_or_budget(field, changed):
    pinned = build_execution_contract()
    drifted = {**pinned["configuration"], field: changed}
    forbidden = _ForbiddenDependency()
    runtime = InferenceRuntime(
        forbidden,
        forbidden,
        SimpleNamespace(inference_concurrency=1, inference_global_limit=1),
        forbidden,
        detector_configuration=drifted,
    )
    with pytest.raises(ExecutionContractError) as error:
        runtime.observe_frames(
            [{"asset_id": "0" * 64, "width": 80, "height": 80}],
            session_id="must-not-dispatch",
            job_key="must-not-dispatch",
            execution_contract=pinned,
        )
    assert error.value.code == "execution_contract_mismatch"
    assert runtime._initialized is False


def test_contract_digest_does_not_allow_edited_policy_to_keep_original_identity():
    pinned = build_execution_contract()
    altered = deepcopy(pinned)
    altered["configuration"]["confidence_threshold"] = 0.9
    with pytest.raises(ExecutionContractError) as error:
        validate_execution_contract(altered)
    assert error.value.code == "invalid_execution_contract"


@pytest.mark.parametrize("field", ["api_key", "authorization", "token", "password"])
def test_contract_schema_refuses_credentials_in_model_configuration(field):
    configuration = build_execution_contract()["configuration"]
    configuration[field] = "synthetic-secret-do-not-persist"
    with pytest.raises(ExecutionContractError):
        build_execution_contract(detector_configuration=configuration)


def test_replay_contract_cannot_be_presented_as_live_model_provenance():
    replay = build_execution_contract(mode="replay")
    with pytest.raises(ExecutionContractError):
        validate_execution_contract(replay, mode="live")


@pytest.fixture
def attack_system(tmp_path):
    dsn = os.environ.get("COUNTERPART_TEST_DSN")
    if not dsn:
        pytest.skip("COUNTERPART_TEST_DSN is required for independent database attacks")
    schema = "cp_gap_attack_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repo = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    repo.initialize()
    media = MediaStore(tmp_path / "media")
    config = Settings(database_url=repo.dsn, media_dir=media.root, inference_limit=20)
    try:
        yield repo, media, config
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


class _WrongModelDetector:
    """Adapter intentionally returns provenance different from its declared model."""

    model_id = "coco/39"

    def __init__(self):
        self.calls = 0

    def detect(self, *_):
        self.calls += 1
        return {"detections": [], "model": {"provider": "roboflow", "model_id": "coco/40"}}


@pytest.mark.postgres
@pytest.mark.parametrize("cached_first", [False, True])
def test_wrong_observation_model_is_refused_even_when_cached(attack_system, cached_first):
    repo, media, config = attack_system
    raw = io.BytesIO()
    Image.new("RGB", (80, 80), "gray").save(raw, "PNG")
    frames = [{**media.put(raw.getvalue()), "t_ms": 0}]
    detector = _WrongModelDetector()
    runtime = InferenceRuntime(repo, media, config, detector)
    contract = build_execution_contract(config, detector=detector)
    if cached_first:
        # Standalone legacy adapter invocation creates a cache record without
        # the new collection contract. A later pinned call must inspect it.
        runtime.observe_frames(frames, session_id="legacy-probe", job_key="old-unpinned-call")
    with pytest.raises(ProviderError) as error:
        runtime.observe_frames(
            frames,
            session_id="pinned-collection",
            job_key="pinned-call",
            execution_contract=contract,
        )
    assert error.value.code == "provider_model_mismatch"
    assert detector.calls == 1
    assert repo.metrics()["budget_reservations"][0]["reserved_units"] == 1


def _uncertain_capture(recipe="spatial"):
    times = [0] if recipe == "spatial" else [index * 250 for index in range(8)]
    return {
        "id": str(uuid4()),
        "slot": "baseline",
        "status": "rejected",
        "interrupted": False,
        "error": None,
        "frames": [
            {
                "asset_id": "0" * 64,
                "width": 80,
                "height": 80,
                "t_ms": stamp,
                "model": {"provider": "fixture", "model_id": "illustrative-replay-v1"},
                "detections": [],
            }
            for stamp in times
        ],
        "result": {"accepted": False, "code": "missing_object", "checks": []},
    }


@pytest.mark.parametrize(
    "case",
    [
        "known_false_check",
        "interrupted",
        "nonfinite_box",
        "unknown_hard_error",
        "multiple_spatial_frames",
        "nonfinite_timestamp",
    ],
)
def test_unknown_top_level_result_cannot_mask_hard_evidence_failure(case):
    from counterpart.evidence_revisions import eligibility
    from counterpart.recipes import build_recipe_snapshot

    capture = _uncertain_capture()
    if case == "known_false_check":
        capture["result"]["checks"] = [{"name": "reference_position", "passed": False, "outcome": "fail"}]
    elif case == "interrupted":
        capture["interrupted"] = True
    elif case == "nonfinite_box":
        capture["frames"][0]["detections"] = [
            {"label": "cup", "confidence": 0.9, "box": [float("nan"), 0.2, 0.8, 0.8]}
        ]
    elif case == "unknown_hard_error":
        capture["result"]["hard_error"] = True
    elif case == "multiple_spatial_frames":
        capture["frames"].append({**capture["frames"][0], "t_ms": 250})
    elif case == "nonfinite_timestamp":
        capture["frames"][0]["t_ms"] = float("nan")
    assert eligibility(capture, build_recipe_snapshot("spatial"))["allowed"] is False


@pytest.mark.parametrize("case", ["missing_frames", "long_gap", "reversed_time", "duplicate_time"])
def test_perceptual_uncertainty_cannot_override_temporal_evidence_contract(case):
    from counterpart.evidence_revisions import eligibility
    from counterpart.recipes import build_recipe_snapshot

    capture = _uncertain_capture("temporal")
    if case == "missing_frames":
        capture["frames"].pop()
    elif case == "long_gap":
        for frame in capture["frames"][4:]:
            frame["t_ms"] += 1000
    elif case == "reversed_time":
        capture["frames"].reverse()
    elif case == "duplicate_time":
        capture["frames"][3]["t_ms"] = capture["frames"][2]["t_ms"]
    assert eligibility(capture, build_recipe_snapshot("temporal"))["allowed"] is False


def _save_replay(repo, media, config, session, slot, scenario):
    from counterpart.fixtures import generate
    from counterpart.worker import Worker

    source = generate(session["recipe"], scenario)
    frames = [
        {
            **media.put(frame["bytes"]),
            "t_ms": frame["t_ms"],
            "fixture_detections": frame["detections"],
        }
        for frame in source
    ]
    key = uuid4().hex
    repo.submit(
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
    assert Worker(repo, media, config).once()
    return repo.snapshot(session["id"])


@pytest.mark.postgres
def test_shared_scene_cannot_drift_between_campaigns_or_mutate_after_join(attack_system):
    repo, media, config = attack_system
    collections = CollectionRepository(repo)
    group = collections.create_split_group("Same physical desk")
    lookalike = collections.create_split_group("Same physical desk")
    assert group["id"] != lookalike["id"]
    sessions = []
    pairs = []
    for name in ("Monday", "Tuesday"):
        campaign = collections.create_campaign(name, "spatial", "replay")
        invite = collections.create_invite(campaign["id"], split_group_id=group["id"])
        session = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
        sessions.append(session)
        with pytest.raises(psycopg.errors.CheckViolation):
            with repo._connection() as connection:
                connection.execute(
                    "UPDATE counterpart_collection_invites SET split_group_id=%s WHERE id=%s",
                    (lookalike["id"], invite["id"]),
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            with repo._connection() as connection:
                connection.execute(
                    "UPDATE counterpart_sessions SET split_group_snapshot=NULL WHERE id=%s",
                    (session["id"],),
                )
        session = _save_replay(repo, media, config, session, "baseline", "baseline")
        session = _save_replay(repo, media, config, session, "counterpart", "counterpart")
        pairs.append(repo.publish(session["id"], session["revision"])["manifest"])
    assert sessions[0]["id"] != sessions[1]["id"]
    assert sessions[0]["split_group_snapshot"] == sessions[1]["split_group_snapshot"]
    assert pairs[0]["split_group"] == pairs[1]["split_group"] == group["id"]
    assert pairs[0]["split"] == pairs[1]["split"] == group["split"]


@pytest.mark.postgres
def test_reverification_retry_race_preserves_original_pair_review_and_release(attack_system):
    from counterpart.evidence_revisions import EvidenceRepository, capture_digest

    repo, media, config = attack_system
    collections = CollectionRepository(repo)
    campaign = collections.create_campaign("Original decision survives", "spatial", "replay")
    invite = collections.create_invite(campaign["id"])
    session = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
    session = _save_replay(repo, media, config, session, "baseline", "baseline")
    session = _save_replay(repo, media, config, session, "counterpart", "counterpart")
    assert session["state"] == "ready"
    pair = repo.publish(session["id"], session["revision"])
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reviewer="independent-reviewer",
        reason="Original frozen evidence reviewed",
        idempotency_key=uuid4().hex,
    )
    release = collections.export_manifests(campaign["id"], include_replay=True)
    original = deepcopy(repo.snapshot(session["id"]))
    evidence = EvidenceRepository(repo, media)
    kwargs = {
        "expected_version": 0,
        "expected_evidence_sha256": capture_digest(original["baseline"], original["recipe_snapshot"]),
        "recipe_version": "1.0.0",
        "idempotency_key": "reverify-lost-response-once",
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(
            pool.map(
                lambda _: evidence.reverify(session["id"], original["baseline"]["id"], **kwargs),
                range(8),
            )
        )
    assert all(record == records[0] for record in records)
    assert records[0]["version"] == 1
    assert records[0]["cloud_calls"] == 0
    with pytest.raises(Conflict) as error:
        evidence.reverify(
            session["id"],
            original["baseline"]["id"],
            **{**kwargs, "idempotency_key": "stale-independent-request"},
        )
    assert error.value.code == "stale_decision_version"
    current = repo.snapshot(session["id"])
    for key in ("baseline", "counterpart", "pair", "review", "state", "revision"):
        assert current[key] == original[key]
    assert collections.get_release(campaign["id"], release["id"]) == release
    with repo._connection() as connection:
        count = connection.execute("SELECT count(*) AS n FROM counterpart_evidence_revisions").fetchone()
    assert count["n"] == 1


class _DeliveryProvider:
    """Pure protocol fake; never uploads media or claims a hosted verification."""

    workspace = "test-workspace"
    project = "test-private"

    def __init__(self, repo, *, ambiguous=False):
        self.repo = repo
        self.ambiguous = ambiguous
        self.calls = 0
        self.source = {
            "asset_id": "d" * 64,
            "image_id": "remote-image-1",
            "pair_id": str(uuid4()),
            "session_id": str(uuid4()),
            "split": "train",
            "width": 80,
            "height": 80,
            "machine_detections": [],
        }

    def project_state(self):
        return {"versions": [1], "public": False}

    def inventory(self, **_):
        source = self.source
        return [
            {
                "id": source["image_id"],
                "name": "counterpart-" + source["asset_id"] + ".png",
                "split": source["split"],
                "user_metadata": {
                    "counterpart_asset_id": source["asset_id"],
                    "counterpart_media_sha256": source["asset_id"],
                    "counterpart_pair_id": source["pair_id"],
                    "counterpart_session_id": source["session_id"],
                },
            }
        ]

    def annotation(self, image_id):
        return {
            "id": image_id,
            "name": self.inventory()[0]["name"],
            "split": "train",
            "annotation": {"width": 80, "height": 80, "boxes": []},
        }

    def generate(self):
        # A separate connection observes that reservation/uncertainty boundary
        # committed before this external-effect surrogate is ever entered.
        with self.repo._connection() as connection:
            rows = connection.execute(
                "SELECT status,generation_reserved FROM counterpart_roboflow_deliveries "
                "WHERE generation_reserved"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0] == {"status": "issuing", "generation_reserved": True}
        self.calls += 1
        if self.ambiguous:
            raise ProviderError("generation_outcome_unknown", ambiguous=True)
        return 2


def _seed_downstream_delivery(repo, service):
    """Seed only upstream receipts to isolate delivery's write boundary."""
    from counterpart.roboflow_delivery import digest

    campaign = CollectionRepository(repo).create_campaign("Delivery protocol fixture", "spatial", "replay")
    release_id, snapshot_id = str(uuid4()), str(uuid4())
    source = service.provider.source
    annotation = service.provider.annotation(source["image_id"])["annotation"]
    manifest = {
        "destination": {
            "workspace": service.provider.workspace,
            "project": service.provider.project,
            "visibility": "private",
        },
        "images": [
            {
                **source,
                "remote_name": service.provider.inventory()[0]["name"],
                "annotation": annotation,
                "boxes": [],
                "annotation_sha256": digest(annotation),
            }
        ],
    }
    with repo._connection() as connection:
        connection.execute(
            "INSERT INTO counterpart_dataset_releases (id,campaign_id,manifest) VALUES (%s,%s,%s)",
            (release_id, campaign["id"], Jsonb({"test_fixture": True})),
        )
        connection.execute(
            "INSERT INTO counterpart_annotation_snapshots "
            "(id,release_id,request_key,content_sha256,manifest) VALUES (%s,%s,%s,%s,%s)",
            (snapshot_id, release_id, uuid4().hex, digest(manifest), Jsonb(manifest)),
        )
    service.approve(snapshot_id, uuid4().hex, "Independent transport-only fixture approval")
    return service.request_delivery(snapshot_id, uuid4().hex)


@pytest.mark.postgres
def test_lost_generation_response_never_retries_or_frees_reserved_credit(attack_system):
    from counterpart.roboflow_delivery import RoboflowDelivery

    repo, _, _ = attack_system
    provider = _DeliveryProvider(repo, ambiguous=True)
    delivery = RoboflowDelivery(repo, provider, generation_limit=1, budget_evidence="synthetic-test-limit")
    intent = _seed_downstream_delivery(repo, delivery)
    outcome = delivery.advance(intent["id"])
    assert outcome["status"] == "uncertain"
    assert outcome["generation_reserved"] is True
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: delivery.advance(intent["id"]), range(8)))
    assert provider.calls == 1
    assert delivery.get_delivery(intent["id"])["provider_version"] is None
    other = _seed_downstream_delivery(repo, delivery)
    with pytest.raises(Conflict) as error:
        delivery.advance(other["id"])
    assert error.value.code == "generation_budget_exhausted"
    assert provider.calls == 1


@pytest.mark.postgres
def test_two_concurrent_delivery_requests_cannot_double_spend_last_reservation(attack_system):
    from counterpart.roboflow_delivery import RoboflowDelivery

    repo, _, _ = attack_system
    provider = _DeliveryProvider(repo)
    delivery = RoboflowDelivery(repo, provider, generation_limit=1, budget_evidence="synthetic-test-limit")
    intents = [_seed_downstream_delivery(repo, delivery) for _ in range(2)]

    def advance(intent):
        try:
            return delivery.advance(intent["id"])["status"]
        except Conflict as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(advance, intents))
    assert sorted(statuses) == ["generated", "generation_budget_exhausted"]
    assert provider.calls == 1


@pytest.mark.postgres
@pytest.mark.parametrize("scenario", ["ambiguous", "camera_moved"])
def test_human_resolution_preserves_scene_group_and_cannot_waive_known_failure(attack_system, scenario):
    from counterpart.archives import archive_response
    from counterpart.consumer import PortableRelease
    from counterpart.evidence_revisions import CRITERIA, EvidenceRepository, pair_digest

    repo, media, config = attack_system
    collections = CollectionRepository(repo)
    group = collections.create_split_group("Human review same desk")
    campaign = collections.create_campaign("Human decision provenance", "spatial", "replay")
    invite = collections.create_invite(campaign["id"], split_group_id=group["id"])
    session = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
    session = _save_replay(repo, media, config, session, "baseline", "baseline")
    session = _save_replay(repo, media, config, session, "counterpart", scenario)
    original_baseline, original_attempt = deepcopy(session["baseline"]), deepcopy(session["latest_capture"])
    evidence = EvidenceRepository(repo, media)
    kwargs = {
        "baseline_id": original_baseline["id"],
        "counterpart_id": original_attempt["id"],
        "expected_session_revision": session["revision"],
        "expected_evidence_sha256": pair_digest(session, original_baseline, original_attempt),
        "labels": session["recipe_snapshot"]["expected_answers"],
        "criteria": dict.fromkeys(CRITERIA, True),
        "reason": "Synthetic protocol test of an explicitly human-derived decision.",
        "idempotency_key": "human-adjudication-once",
    }
    if scenario == "camera_moved":
        with pytest.raises(Conflict) as error:
            evidence.adjudicate(session["id"], **kwargs)
        assert error.value.code == "known_control_failure"
        assert repo.snapshot(session["id"])["pair"] is None
        return
    pair = evidence.adjudicate(session["id"], **kwargs)
    assert evidence.adjudicate(session["id"], **kwargs) == pair
    manifest = pair["manifest"]
    assert manifest["split_group"] == group["id"]
    assert manifest["split"] == group["split"]
    assert manifest["baseline"] == original_baseline
    assert manifest["counterpart"] == original_attempt
    assert manifest["counterpart"]["result"]["accepted"] is False
    assert repo.snapshot(session["id"])["review"]["status"] == "pending"
    with pytest.raises(Conflict):
        collections.export_manifests(campaign["id"], include_replay=True)
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reviewer="separate-reviewer",
        reason="Explicit synthetic review fixture",
        idempotency_key=uuid4().hex,
    )
    release = collections.export_manifests(campaign["id"], include_replay=True)
    response = archive_response(release, media, "human-review-test.zip")

    async def consume():
        try:
            return b"".join([chunk async for chunk in response.body_iterator])
        finally:
            if response.background:
                await response.background()

    with PortableRelease(io.BytesIO(asyncio.run(consume())), allow_replay=True) as portable:
        assert portable.summary["pair_count"] == 1
        rows = list(portable.iter_examples())
        assert all(row["split_group"] == group["id"] for row in rows)
        assert all("Human adjudication" in row["label_basis"] for row in rows)


def _coco_fixture():
    source = {
        "asset_id": "d" * 64,
        "width": 80,
        "height": 80,
        "split": "train",
        "boxes": [{"label": "cup", "bbox": [20, 20, 20, 20]}],
    }
    documents = {
        "train": {
            "images": [{"id": 1, "file_name": "counterpart-" + "d" * 64 + ".png", "width": 80, "height": 80}],
            "categories": [{"id": 1, "name": "cup"}],
            "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [20, 20, 20, 20]}],
        }
    }
    return documents, [source]


@pytest.mark.parametrize(
    "case",
    [
        "extra_image",
        "cross_split",
        "changed_box",
        "missing_annotation",
        "unknown_source",
        "unhashable_image_id",
        "boolean_image_id",
        "unhashable_category_id",
        "boolean_category_id",
    ],
)
def test_imported_hosted_version_cannot_change_membership_labels_or_splits(case):
    from counterpart.roboflow_delivery import validate_coco

    documents, expected = _coco_fixture()
    assert validate_coco(documents, expected)["membership_splits_annotations_verified"] is True
    doc = documents["train"]
    if case == "extra_image":
        doc["images"].append({**doc["images"][0], "id": 2})
    elif case == "cross_split":
        documents = {"test": doc}
    elif case == "changed_box":
        doc["annotations"][0]["bbox"][0] += 0.02
    elif case == "missing_annotation":
        doc["annotations"] = []
    elif case == "unknown_source":
        doc["images"][0]["file_name"] = "counterpart-" + "f" * 64 + ".png"
    elif case == "unhashable_image_id":
        doc["annotations"][0]["image_id"] = {}
    elif case == "boolean_image_id":
        doc["annotations"][0]["image_id"] = True
    elif case == "unhashable_category_id":
        doc["annotations"][0]["category_id"] = []
    elif case == "boolean_category_id":
        doc["annotations"][0]["category_id"] = True
    with pytest.raises(Conflict) as error:
        validate_coco(documents, expected)
    assert error.value.code == "coco_release_mismatch"


@pytest.mark.parametrize("key", ["polygons", "masks", "segmentation", "keypoints"])
def test_annotation_pull_cannot_silently_discard_nonbox_ground_truth(key):
    from counterpart.roboflow_delivery import normalize_annotation

    annotation = {"width": 80, "height": 80, "boxes": [], key: [[1, 2, 3, 4]]}
    with pytest.raises(ProviderError):
        normalize_annotation(annotation, 80, 80)


def _visual_unknown_session(attack_system):
    """Explicit v2 replay fixture; generated low-texture pixels, not CV accuracy."""
    from counterpart.recipes import build_recipe_snapshot

    repo, _, _ = attack_system
    with repo._connection() as connection:
        return repo._create_session(
            connection,
            "spatial",
            "replay",
            "bottle",
            "cup",
            recipe_snapshot=build_recipe_snapshot("spatial", recipe_version="2.0.0"),
        )["session"]


def _flat_visual_frames(media, *, right=False, missing_role=False):
    image = Image.new("RGB", (320, 240), "#dddddd")
    draw = ImageDraw.Draw(image)
    roles = [
        {
            "label": "bottle",
            "confidence": 0.97,
            "box": [0.74, 0.40, 0.84, 0.68] if right else [0.14, 0.40, 0.24, 0.68],
        },
        {"label": "cup", "confidence": 0.97, "box": [0.44, 0.40, 0.54, 0.68]},
    ]
    for detection in roles:
        draw.rectangle(
            tuple(round(v * d) for v, d in zip(detection["box"], (320, 240, 320, 240), strict=True)),
            fill="#208040" if detection["label"] == "bottle" else "#df8040",
        )
    raw = io.BytesIO()
    image.save(raw, "PNG")
    if missing_role:
        roles = [d for d in roles if d["label"] != "bottle"]
    return [{**media.put(raw.getvalue()), "t_ms": 0, "fixture_detections": roles}]


def _submit_flat_visual(attack_system, session, slot="baseline", *, right=False, missing_role=False):
    from counterpart.worker import Worker

    repo, media, config = attack_system
    key = uuid4().hex
    repo.submit(
        session["id"],
        {
            "slot": slot,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "idempotency_key": key,
            "client_seq": 0,
        },
        _flat_visual_frames(media, right=right, missing_role=missing_role),
        key,
    )
    assert Worker(repo, media, config).once()
    return repo.snapshot(session["id"])


@pytest.mark.postgres
def test_legitimate_visual_unknown_baseline_is_not_silently_machine_accepted(attack_system):
    session = _submit_flat_visual(attack_system, _visual_unknown_session(attack_system))
    assert session["baseline"] is None
    assert session["baseline_authorization"] is None
    result = session["latest_capture"]["result"]
    assert result["accepted"] is False and result["outcome"] == "unknown"
    assert result["code"] == "registration_uncertain"
    assert not any(check["outcome"] == "fail" for check in result["checks"])


def _baseline_authorization_body(session):
    from counterpart.evidence_revisions import BASELINE_CRITERIA, capture_digest

    return {
        "expected_session_revision": session["revision"],
        "expected_evidence_sha256": capture_digest(session["latest_capture"], session["recipe_snapshot"]),
        "answer": session["recipe_snapshot"]["expected_answers"]["baseline"],
        "criteria": dict.fromkeys(BASELINE_CRITERIA, True),
        "reason": "Synthetic protocol review of complete geometry and uncertain visual controls.",
        "idempotency_key": uuid4().hex,
    }


@pytest.mark.postgres
@pytest.mark.parametrize(
    "attack", ["foreign_capture", "stale_revision", "stale_digest", "wrong_answer", "missing_criterion"]
)
def test_baseline_authorization_rejects_unbound_or_incomplete_review(attack_system, attack):
    from counterpart.evidence_revisions import EvidenceRepository
    from counterpart.repository import NotFound

    repo, media, _ = attack_system
    session = _submit_flat_visual(attack_system, _visual_unknown_session(attack_system))
    before = deepcopy(session)
    evidence = EvidenceRepository(repo, media)
    body = _baseline_authorization_body(session)
    capture_id = session["latest_capture"]["id"]
    if attack == "foreign_capture":
        other = _submit_flat_visual(attack_system, _visual_unknown_session(attack_system))
        capture_id = other["latest_capture"]["id"]
    elif attack == "stale_revision":
        body["expected_session_revision"] -= 1
    elif attack == "stale_digest":
        body["expected_evidence_sha256"] = "f" * 64
    elif attack == "wrong_answer":
        body["answer"] = "right"
    elif attack == "missing_criterion":
        body["criteria"].pop("objects_identified")
    with pytest.raises((Conflict, NotFound)):
        evidence.authorize_baseline(session["id"], capture_id, **body)
    after = repo.snapshot(session["id"])
    for field in ("baseline", "latest_capture", "baseline_authorization", "revision", "state"):
        assert after[field] == before[field]
    with repo._connection() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM counterpart_baseline_authorizations").fetchone()[
                "n"
            ]
            == 0
        )


@pytest.mark.postgres
@pytest.mark.parametrize("attack", ["missing_role", "wrong_relation"])
def test_baseline_review_cannot_supply_missing_roles_or_waive_known_wrong_relation(attack_system, attack):
    from counterpart.evidence_revisions import EvidenceRepository

    repo, media, _ = attack_system
    session = _submit_flat_visual(
        attack_system,
        _visual_unknown_session(attack_system),
        missing_role=attack == "missing_role",
        right=attack == "wrong_relation",
    )
    assert session["latest_capture"]["result"]["accepted"] is False
    with pytest.raises(Conflict):
        EvidenceRepository(repo, media).authorize_baseline(
            session["id"], session["latest_capture"]["id"], **_baseline_authorization_body(session)
        )
    assert repo.snapshot(session["id"])["baseline_authorization"] is None


@pytest.mark.postgres
def test_baseline_authorization_after_reconnect_never_claims_scene_recovery(attack_system):
    from counterpart.evidence_revisions import CRITERIA, EvidenceRepository, pair_digest

    repo, media, _ = attack_system
    session = _submit_flat_visual(attack_system, _visual_unknown_session(attack_system))
    original = deepcopy(session["latest_capture"])
    body = _baseline_authorization_body(session)
    resumed = repo.resume(session["id"], session["epoch"])
    assert resumed["epoch"] > original["epoch"]
    assert resumed["baseline"] is None and resumed["recovery_required"] is False
    evidence = EvidenceRepository(repo, media)
    record = evidence.authorize_baseline(session["id"], original["id"], **body)
    session = repo.snapshot(session["id"])
    assert session["baseline"] == original
    assert session["baseline"]["result"]["accepted"] is False
    assert session["recovery_required"] is True
    assert session["human_review_required"] is True
    assert evidence.authorize_baseline(session["id"], original["id"], **body) == record
    session = _submit_flat_visual(attack_system, session, "counterpart", right=True)
    candidate = session["latest_capture"]
    assert candidate["baseline_authorization"] == record
    assert candidate["recovery_unresolved"] is True
    assert candidate["result"]["human_review_required"] is True
    assert candidate["result"].get("hard_error") is False
    assert session["recovery_required"] is True
    with pytest.raises(Conflict) as error:
        repo.publish(session["id"], session["revision"])
    assert error.value.code == "human_review_required"
    pair = evidence.adjudicate(
        session["id"],
        baseline_id=original["id"],
        counterpart_id=candidate["id"],
        expected_session_revision=session["revision"],
        expected_evidence_sha256=pair_digest(session, original, candidate),
        labels=session["recipe_snapshot"]["expected_answers"],
        criteria=dict.fromkeys(CRITERIA, True),
        reason="Independent synthetic review of both original observations and unresolved recovery.",
        idempotency_key=uuid4().hex,
    )
    assert pair["manifest"]["schema_version"] == "counterpart.pair.v2"
    assert pair["manifest"]["counterpart"]["recovery_unresolved"] is True
    assert pair["manifest"]["baseline"] == original


@pytest.mark.postgres
def test_replacing_authorized_baseline_cannot_reactivate_old_authorization_by_retry(attack_system):
    from counterpart.evidence_revisions import EvidenceRepository

    repo, media, _ = attack_system
    session = _submit_flat_visual(attack_system, _visual_unknown_session(attack_system))
    capture = deepcopy(session["latest_capture"])
    body = _baseline_authorization_body(session)
    evidence = EvidenceRepository(repo, media)
    record = evidence.authorize_baseline(session["id"], capture["id"], **body)
    session = _submit_flat_visual(attack_system, repo.snapshot(session["id"]))
    assert session["latest_capture"]["id"] != capture["id"]
    assert session["baseline_authorization"] is None and session["baseline"] is None
    assert evidence.authorize_baseline(session["id"], capture["id"], **body) == record
    current = repo.snapshot(session["id"])
    assert current["baseline_authorization"] is None and current["baseline"] is None
    assert current["latest_capture"] == session["latest_capture"]


def _authorized_human_pair(attack_system):
    from counterpart.evidence_revisions import CRITERIA, EvidenceRepository, pair_digest

    repo, media, _ = attack_system
    evidence = EvidenceRepository(repo, media)
    session = _submit_flat_visual(attack_system, _visual_unknown_session(attack_system))
    baseline = deepcopy(session["latest_capture"])
    body = _baseline_authorization_body(session)
    repo.resume(session["id"], session["epoch"])
    evidence.authorize_baseline(session["id"], baseline["id"], **body)
    session = _submit_flat_visual(attack_system, repo.snapshot(session["id"]), "counterpart", right=True)
    candidate = session["latest_capture"]
    pair = evidence.adjudicate(
        session["id"],
        baseline_id=baseline["id"],
        counterpart_id=candidate["id"],
        expected_session_revision=session["revision"],
        expected_evidence_sha256=pair_digest(session, baseline, candidate),
        labels=session["recipe_snapshot"]["expected_answers"],
        criteria=dict.fromkeys(CRITERIA, True),
        reason="Independent synthetic review preserving unresolved recovery and original refusals.",
        idempotency_key=uuid4().hex,
    )
    return pair["manifest"]


def _pack_actual_archive(manifest, media):
    from counterpart.archives import archive_response

    response = archive_response(manifest, media, "attack-fixture.zip")

    async def consume():
        try:
            return b"".join([chunk async for chunk in response.body_iterator])
        finally:
            if response.background:
                await response.background()

    return io.BytesIO(asyncio.run(consume()))


@pytest.mark.postgres
@pytest.mark.parametrize(
    "attack",
    ["foreign_session", "foreign_capture", "different_model", "missing_criterion", "hidden_recovery"],
)
def test_portable_authorization_cannot_be_rebound_or_hide_unresolved_recovery(attack_system, attack):
    from counterpart.consumer import ArchiveValidationError, PortableRelease
    from counterpart.evidence_revisions import digest, pair_digest

    _, media, _ = attack_system
    manifest = _authorized_human_pair(attack_system)
    original_archive = _pack_actual_archive(manifest, media).getvalue()
    with PortableRelease(io.BytesIO(original_archive), allow_replay=True) as portable:
        assert portable.summary["pair_count"] == 1
    changed = deepcopy(manifest)
    authorization = changed["baseline_authorization"]
    if attack == "foreign_session":
        authorization["session_id"] = str(uuid4())
    elif attack == "foreign_capture":
        authorization["capture_id"] = str(uuid4())
    elif attack == "different_model":
        authorization["execution_contract_digest"] = "f" * 64
    elif attack == "missing_criterion":
        authorization["criteria"].pop("objects_identified")
    elif attack == "hidden_recovery":
        assert changed["counterpart"]["epoch"] != changed["baseline"]["epoch"]
        assert changed["counterpart"].pop("recovery_unresolved") is True
    authorization["record_sha256"] = digest({k: v for k, v in authorization.items() if k != "record_sha256"})
    changed["counterpart"]["baseline_authorization"] = deepcopy(authorization)
    adjudication = changed["evidence_adjudication"]
    adjudication["evidence_sha256"] = pair_digest(
        {
            "id": changed["session_id"],
            "recipe_snapshot": changed["recipe_snapshot"],
            "execution_contract": changed["execution_contract"],
        },
        changed["baseline"],
        changed["counterpart"],
    )
    adjudication["record_sha256"] = digest({k: v for k, v in adjudication.items() if k != "record_sha256"})
    # Bypass the server writer intentionally: an offline consumer must refuse a
    # self-consistent tampered manifest even after unsigned hashes are rebuilt.
    forged = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original_archive)) as original, zipfile.ZipFile(forged, "w") as output:
        for name in original.namelist():
            content = original.read(name)
            if name == "manifest.json":
                content = json.dumps(changed).encode()
            elif name == "examples.jsonl":
                rows = [json.loads(line) for line in content.splitlines()]
                for row in rows:
                    row["adjudication"] = adjudication
                content = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
            output.writestr(name, content)
    with pytest.raises(ArchiveValidationError):
        PortableRelease(io.BytesIO(forged.getvalue()), allow_replay=True)
