"""Versioned clause composition; generated pixels are not a physical CV study."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from random import Random
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from counterpart import visual_checks
from counterpart.fixtures import generate
from counterpart.models import CreateSession
from counterpart.recipes import (
    RecipeContractError,
    RecipeParameters,
    assert_runtime_compatible,
    build_recipe_snapshot,
    validate_recipe_snapshot,
    version_for_mode,
)
from counterpart.storage import inspect_image
from counterpart.verification import baseline_authorization_binding, verify


def image_frame(assets, *, right=False, moved_anchor=False, flat=False, protected=False):
    image = Image.new("RGB", (640, 480), "#d9dadb")
    draw = ImageDraw.Draw(image)
    if not flat:
        random = Random(320)
        for _ in range(500):
            x, y = random.randrange(640), random.randrange(480)
            radius = random.randrange(2, 8)
            draw.rectangle(
                (x, y, x + radius, y + radius), fill=tuple(random.randrange(255) for _ in range(3))
            )
    detections = [
        {
            "label": "bottle",
            "confidence": 0.95,
            "box": [0.74, 0.40, 0.84, 0.68] if right else [0.14, 0.40, 0.24, 0.68],
        },
        {
            "label": "cup",
            "confidence": 0.96,
            "box": [0.50, 0.40, 0.60, 0.68] if moved_anchor else [0.44, 0.40, 0.54, 0.68],
        },
    ]
    if protected:
        detections.append({"label": "book", "confidence": 0.93, "box": [0.65, 0.12, 0.84, 0.25]})
    for detection in detections:
        box = detection["box"]
        coords = tuple(round(v * d) for v, d in zip(box, (640, 480, 640, 480), strict=True))
        draw.rectangle(
            coords, fill={"bottle": "#208040", "cup": "#df8040", "book": "#3040c0"}[detection["label"]]
        )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    metadata = inspect_image(data)
    assets[metadata["asset_id"]] = data
    return {**metadata, "t_ms": 0.0, "detections": detections}


def pair(*, flat=False, protected=False):
    assets = {}
    snapshot = build_recipe_snapshot(
        "spatial", recipe_version="2.0.0", protected_objects=["book"] if protected else []
    )
    first = image_frame(assets, flat=flat, protected=protected)
    initial = verify(
        "spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__
    )
    return assets, snapshot, first, initial


def test_full_v2_pair_accepts_supported_named_controls_and_binds_original_bytes():
    assets, snapshot, first, initial = pair(protected=True)
    assert initial["accepted"], initial
    second = image_frame(assets, right=True, protected=True)
    result = verify(
        "spatial",
        "counterpart",
        [second],
        {**initial, "frames": [first]},
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
    )
    assert result["accepted"] and result["outcome"] == "pass", result
    assert result["verifier_version"] == "counterpart-verifier/2.0.0"
    assert result["recipe_snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert all(c["outcome"] == "pass" for c in result["checks"])
    registration = next(c for c in result["checks"] if c["name"].startswith("background_registration"))
    assert len(registration["measurements"]["candidate_to_reference_pixels"]) == 3
    assert (
        registration["measurements"]["candidate_sha256"]
        == hashlib.sha256(assets[second["asset_id"]]).hexdigest()
    )
    assert (
        verify(
            "spatial",
            "counterpart",
            [second],
            {**initial, "frames": [first]},
            recipe_snapshot=snapshot,
            media_reader=assets.__getitem__,
        )
        == result
    )


def test_flat_background_is_reviewable_uncertainty_not_automatic_acceptance():
    _, _, _, result = pair(flat=True)
    assert result["outcome"] == "unknown"
    assert not result["accepted"] and not result["hard_error"]
    assert result["code"] == "registration_uncertain"


def test_uncertain_moving_object_does_not_hide_known_reference_movement():
    assets, snapshot, first, initial = pair()
    second = image_frame(assets, right=True, moved_anchor=True)
    second["detections"] = [d for d in second["detections"] if d["label"] != "bottle"]
    result = verify(
        "spatial",
        "counterpart",
        [second],
        {**initial, "frames": [first]},
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
    )
    assert result["outcome"] == "fail" and not result["accepted"]
    assert any(c["outcome"] == "fail" and c.get("code") == "reference_moved" for c in result["checks"])
    assert any(c["outcome"] == "unknown" for c in result["checks"])


def test_uncertainty_cannot_hide_corrupted_original_bytes():
    assets, snapshot, first, _ = pair()
    first["detections"] = []
    assets[first["asset_id"]] = b"not the recorded image"
    result = verify("spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__)
    assert result["outcome"] == "fail" and result["hard_error"]
    assert result["code"] == "invalid_evidence"


def test_no_reader_cannot_silently_skip_mandatory_visual_checks():
    _, snapshot, first, _ = pair()
    result = verify("spatial", "baseline", [first], recipe_snapshot=snapshot)
    assert result["outcome"] == "unknown" and not result["accepted"]


def test_detector_ambiguity_cannot_hide_known_temporal_gap():
    observations = [
        {k: v for k, v in f.items() if k not in ("bytes", "provenance")}
        for f in generate("temporal", "baseline")
    ]
    observations[0]["detections"] = []
    for frame in observations[3:]:
        frame["t_ms"] += 1000
    result = verify(
        "temporal",
        "baseline",
        observations,
        recipe_snapshot=build_recipe_snapshot("temporal", recipe_version="2.0.0"),
    )
    assert result["outcome"] == "fail" and not result["accepted"]
    assert any(c["outcome"] == "fail" and c.get("code") == "observation_gap" for c in result["checks"])


def test_changed_runtime_visual_policy_cannot_reinterpret_historical_snapshot(monkeypatch):
    snapshot = build_recipe_snapshot("spatial", recipe_version="2.0.0")
    changed = {**visual_checks.VISUAL_THRESHOLDS, "minimum_inliers": 1}
    monkeypatch.setattr(visual_checks, "VISUAL_THRESHOLDS", changed)
    assert validate_recipe_snapshot(snapshot) == snapshot
    assert build_recipe_snapshot("spatial", recipe_version="2.0.0") == snapshot
    with pytest.raises(RecipeContractError, match="visual policy"):
        assert_runtime_compatible(snapshot)


def test_historical_v1_and_visual_v2_can_execute_concurrently_without_global_mutation():
    assets, snapshot, first, initial = pair()
    observations = [
        {k: v for k, v in f.items() if k not in ("bytes", "provenance")}
        for f in generate("spatial", "baseline")
    ]
    old = verify("spatial", "baseline", observations)

    def execute(index):
        if index % 2:
            return verify(
                "spatial", "baseline", observations, recipe_snapshot=build_recipe_snapshot("spatial")
            )
        return verify(
            "spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, result in enumerate(pool.map(execute, range(12))):
            assert result == (old if index % 2 else initial)
    assert "outcome" not in old


@pytest.mark.parametrize("protected", [["bottle"], ["cup"], ["book", "book"], ["person"], False, "book"])
def test_protected_classes_cannot_weaken_or_duplicate_roles(protected):
    with pytest.raises((ValidationError, RecipeContractError)):
        RecipeParameters(protected_objects=protected)
    with pytest.raises(RecipeContractError):
        build_recipe_snapshot("spatial", recipe_version="2.0.0", protected_objects=protected)


def test_protected_classes_are_canonical_and_historical_replay_cannot_claim_them():
    a = build_recipe_snapshot("spatial", recipe_version="2.0.0", protected_objects=["mouse", "book"])
    b = build_recipe_snapshot("spatial", recipe_version="2.0.0", protected_objects=["book", "mouse"])
    assert a == b
    with pytest.raises(RecipeContractError):
        build_recipe_snapshot("spatial", protected_objects=["book"])
    with pytest.raises(ValidationError):
        CreateSession(mode="replay", protected_objects=["book"])
    assert version_for_mode("live") == "2.1.0"
    assert version_for_mode("replay") == "1.0.0"


def test_v2_policy_cannot_be_weakened_by_rehashing():
    snapshot = build_recipe_snapshot("spatial", recipe_version="2.0.0")
    changed = deepcopy(snapshot)
    changed["verifier"]["visual"]["thresholds"]["minimum_inliers"] = 1
    with pytest.raises(RecipeContractError):
        validate_recipe_snapshot(changed)


def test_authorized_unknown_baseline_preserves_original_and_never_waives_candidate_checks():
    assets, snapshot, first, initial = pair(flat=True)
    baseline = {**initial, "frames": [first]}
    original = deepcopy(baseline)
    authorization = {**baseline_authorization_binding(snapshot, baseline), "id": str(uuid4())}
    second = image_frame(assets, right=True, flat=True)
    unauthorized = verify(
        "spatial",
        "counterpart",
        [second],
        baseline,
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
    )
    assert not unauthorized["accepted"] and unauthorized["hard_error"]
    result = verify(
        "spatial",
        "counterpart",
        [second],
        baseline,
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
        baseline_authorization=authorization,
    )
    assert result["outcome"] == "unknown" and not result["accepted"] and not result["hard_error"]
    assert result["human_review_required"] is True
    assert result["baseline_authorization_id"] == authorization["id"]
    assert baseline == original
    assert any(c["name"].startswith("background_registration") for c in result["checks"])


@pytest.mark.parametrize(
    "field", ["recipe_snapshot_sha256", "baseline_decision_sha256", "baseline_frames_sha256"]
)
def test_baseline_authorization_cannot_be_rebound_to_other_contract_or_evidence(field):
    assets, snapshot, first, initial = pair(flat=True)
    baseline = {**initial, "frames": [first]}
    authorization = {**baseline_authorization_binding(snapshot, baseline), "id": str(uuid4())}
    authorization[field] = "f" * 64
    second = image_frame(assets, right=True, flat=True)
    result = verify(
        "spatial",
        "counterpart",
        [second],
        baseline,
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
        baseline_authorization=authorization,
    )
    assert result["outcome"] == "fail" and result["hard_error"] and not result["accepted"]


def test_baseline_authorization_requires_detector_geometry_without_human_substitution():
    assets, snapshot, first, _ = pair(flat=True)
    first["detections"][0]["confidence"] = 0.4
    result = verify("spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__)
    assert result["outcome"] == "unknown"
    with pytest.raises(ValueError):
        baseline_authorization_binding(snapshot, {**result, "frames": [first]})


def test_baseline_authorization_cannot_hide_missing_protected_class():
    assets, snapshot, first, _ = pair(flat=True, protected=True)
    first["detections"] = [d for d in first["detections"] if d["label"] != "book"]
    result = verify("spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__)
    assert result["outcome"] == "unknown"
    with pytest.raises(ValueError, match="Protected baseline roles"):
        baseline_authorization_binding(snapshot, {**result, "frames": [first]})
