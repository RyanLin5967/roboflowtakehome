"""Independent bounded pixel attacks, not detector/physical-camera evaluation.

Images and observations are generated together. These cases examine whether the
new visual controls decline evidence they cannot justify, including situations
where the two role boxes alone would look perfectly unchanged.
"""

from copy import deepcopy
from io import BytesIO
from random import Random
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw, ImageOps

from counterpart.fixtures import generate
from counterpart.recipes import RecipeContractError, build_recipe_snapshot
from counterpart.storage import inspect_image
from counterpart.verification import THRESHOLDS, baseline_authorization_binding, verify
from counterpart.visual_checks import InvalidVisualEvidence, evaluate_visual_controls

WIDTH, HEIGHT = 640, 480
ROLE_BOXES = {
    "bottle": [0.12, 0.40, 0.22, 0.72],
    "cup": [0.48, 0.42, 0.62, 0.72],
    "book": [0.75, 0.13, 0.90, 0.29],
}


def background(*, texture=True, seed=591):
    image = Image.new("RGB", (WIDTH, HEIGHT), "#d8d3c8")
    if texture:
        random = Random(seed)
        draw = ImageDraw.Draw(image)
        for _ in range(400):
            x, y = random.randrange(15, WIDTH - 30), random.randrange(15, HEIGHT - 30)
            size = random.randrange(3, 15)
            color = tuple(random.randrange(15, 225) for _ in range(3))
            draw.rectangle((x, y, x + size, y + size), fill=color)
            draw.line((x, y, x + size, y + size), fill="white", width=1)
    return image


def scene(back=None, *, boxes=None, protected=False, colors=None):
    image = (background() if back is None else back).copy()
    draw = ImageDraw.Draw(image)
    boxes = deepcopy(ROLE_BOXES if boxes is None else boxes)
    labels = ("bottle", "cup", "book") if protected else ("bottle", "cup")
    observations = []
    palette = {"bottle": "#358955", "cup": "#e98139", "book": "#626cb1"}
    palette.update(colors or {})
    for label in labels:
        box = boxes[label]
        xy = tuple(
            round(value * dimension)
            for value, dimension in zip(box, (WIDTH, HEIGHT, WIDTH, HEIGHT), strict=True)
        )
        draw.rectangle(xy, fill=palette[label], outline="#111111", width=3)
        draw.line((xy[0] + 8, xy[1] + 5, xy[2] - 7, xy[3] - 5), fill="white", width=4)
        draw.ellipse((xy[0] + 5, xy[1] + 8, xy[0] + 18, xy[1] + 21), fill="#222222")
        observations.append({"label": label, "confidence": 0.97, "box": box})
    return image, observations


def recorded(image, detections, assets, *, timestamp=0):
    buffer = BytesIO()
    image.save(buffer, "PNG")
    data = buffer.getvalue()
    frame = inspect_image(data)
    assets[frame["asset_id"]] = data
    return {**frame, "t_ms": timestamp, "detections": detections}


def check_pair(first, second, *, protected=()):
    assets = {}
    baseline = recorded(*first, assets)
    candidate = recorded(*second, assets)
    return evaluate_visual_controls(
        [candidate], [baseline], protected_objects=protected, media_reader=assets.__getitem__
    )


def test_attack_positive_control_authentic_unchanged_scene_passes():
    original = scene(protected=True)
    result = check_pair(original, original, protected=("book",))
    assert result["outcome"] == "pass", result


def test_attack_moving_only_designated_role_is_not_camera_motion():
    boxes = deepcopy(ROLE_BOXES)
    boxes["bottle"] = [0.77, 0.40, 0.87, 0.72]
    result = check_pair(scene(), scene(boxes=boxes))
    assert result["outcome"] == "pass", result


def test_attack_same_role_boxes_cannot_hide_background_translation():
    shifted = background().transform(
        (WIDTH, HEIGHT), Image.Transform.AFFINE, (1, 0, 60, 0, 1, 0), fillcolor="#d8d3c8"
    )
    result = check_pair(scene(), scene(shifted))
    assert result["outcome"] != "pass", result


def test_attack_same_role_boxes_cannot_hide_mirrored_background():
    result = check_pair(scene(), scene(ImageOps.mirror(background())))
    assert result["outcome"] != "pass", result


def test_attack_featureless_background_is_unknown_not_stationary_proof():
    result = check_pair(scene(background(texture=False)), scene(background(texture=False)))
    assert result["outcome"] == "unknown", result


def test_attack_unrelated_background_cannot_pass_from_foreground_features():
    result = check_pair(scene(), scene(background(seed=592)))
    assert result["outcome"] != "pass", result


def test_attack_protected_object_disappears_despite_perfect_role_geometry():
    result = check_pair(scene(protected=True), scene(), protected=("book",))
    assert result["outcome"] != "pass", result


def test_attack_protected_object_moves_despite_stationary_background():
    boxes = deepcopy(ROLE_BOXES)
    boxes["book"] = [0.50, 0.13, 0.65, 0.29]
    result = check_pair(scene(protected=True), scene(boxes=boxes, protected=True), protected=("book",))
    assert result["outcome"] != "pass", result


@pytest.mark.parametrize(
    "box", [[0.8, 0.15, 0.8001, 0.1501], [0, 0.15, 0.1, 0.3]], ids=["subpixel", "cropped"]
)
def test_attack_unobservable_protected_object_cannot_claim_continuity(box):
    assets = {}
    frame = recorded(*scene(protected=True), assets)
    frame["detections"][2]["box"] = box
    result = evaluate_visual_controls(
        [frame], [frame], protected_objects=("book",), media_reader=assets.__getitem__
    )
    assert result["outcome"] == "unknown", result


def test_attack_duplicate_role_detection_is_not_identity_evidence():
    image, detections = scene()
    detections.append({"label": "bottle", "confidence": 0.9, "box": [0.75, 0.4, 0.85, 0.72]})
    result = check_pair(scene(), (image, detections))
    assert result["outcome"] != "pass", result


def test_attack_one_bad_intermediate_frame_cannot_hide_behind_good_endpoints():
    assets = {}
    good = recorded(*scene(), assets)
    moved = background().transform(
        (WIDTH, HEIGHT), Image.Transform.AFFINE, (1, 0, 65, 0, 1, 0), fillcolor="#d8d3c8"
    )
    bad = recorded(*scene(moved), assets, timestamp=250)
    final = {**good, "t_ms": 500}
    result = evaluate_visual_controls([good, bad, final], [good], media_reader=assets.__getitem__)
    assert result["outcome"] != "pass", result


@pytest.mark.parametrize("transform", [(1.12, 0, 0, 0, 1.12, 0), (1, 0.20, 0, 0, 1, 0)])
def test_attack_zoom_or_shear_cannot_be_hidden_by_stationary_role_boxes(transform):
    changed = background().transform((WIDTH, HEIGHT), Image.Transform.AFFINE, transform, fillcolor="#d8d3c8")
    result = check_pair(scene(), scene(changed))
    assert result["outcome"] != "pass", result


def test_attack_many_features_in_one_small_region_do_not_prove_registration():
    localized = background(texture=False)
    localized.paste(background().crop((0, 0, 170, 120)), (0, 0))
    result = check_pair(scene(localized), scene(localized))
    assert result["outcome"] == "unknown", result


@pytest.mark.parametrize("label", ["bottle", "cup", "book"])
def test_attack_strong_appearance_change_is_not_identity_continuity(label):
    result = check_pair(
        scene(protected=True), scene(protected=True, colors={label: "#ff00ee"}), protected=("book",)
    )
    assert result["outcome"] != "pass", result


@pytest.mark.parametrize(
    ("field", "value"),
    [("confidence", 10**1000), ("box", [10**1000, 0.4, 0.7, 0.8]), ("label", "")],
    ids=["huge-confidence", "huge-coordinate", "empty-label"],
)
def test_attack_malformed_observations_raise_controlled_integrity_error(field, value):
    assets = {}
    good = recorded(*scene(), assets)
    bad = deepcopy(good)
    bad["detections"][0][field] = value
    with pytest.raises(InvalidVisualEvidence):
        evaluate_visual_controls([bad], [good], media_reader=assets.__getitem__)


@pytest.mark.parametrize("mutation", ["hash", "width", "height", "size"])
def test_attack_visual_evidence_is_bound_to_exact_saved_media(mutation):
    assets = {}
    good = recorded(*scene(), assets)
    bad = deepcopy(good)
    if mutation == "hash":
        bad["asset_id"] = "f" * 64
        assets[bad["asset_id"]] = assets[good["asset_id"]]
    else:
        key = "bytes" if mutation == "size" else mutation
        bad[key] += 1
    with pytest.raises(InvalidVisualEvidence):
        evaluate_visual_controls([bad], [good], media_reader=assets.__getitem__)


@pytest.mark.parametrize("mutation", ["zero-height", "nondict-frame", "invalid-timestamp"])
def test_attack_early_uncertainty_cannot_mask_later_corrupt_temporal_metadata(mutation):
    frames = [
        {key: value for key, value in frame.items() if key not in ("bytes", "provenance")}
        for frame in generate("temporal", "baseline")
    ]
    frames[0]["detections"] = [d for d in frames[0]["detections"] if d["label"] != "bottle"]
    if mutation == "zero-height":
        frames[1]["height"] = 0
    elif mutation == "nondict-frame":
        frames[1] = None
    else:
        frames[1]["t_ms"] = "not a timestamp"
    result = verify(
        "temporal",
        "baseline",
        frames,
        recipe_snapshot=build_recipe_snapshot("temporal", recipe_version="2.0.0"),
    )
    assert result["accepted"] is False
    assert result["outcome"] == "fail"
    assert result["hard_error"] is True


@pytest.mark.parametrize("move_background", [False, True], ids=["valid-move", "hidden-camera-motion"])
def test_attack_registered_recipe_closes_the_two_box_verifier_blind_spot(move_background):
    assets = {}
    initial = recorded(*scene(protected=True), assets)
    boxes = deepcopy(ROLE_BOXES)
    boxes["bottle"] = [0.77, 0.40, 0.87, 0.72]
    changed_background = background()
    if move_background:
        changed_background = changed_background.transform(
            (WIDTH, HEIGHT), Image.Transform.AFFINE, (1, 0, 60, 0, 1, 0), fillcolor="#d8d3c8"
        )
    candidate = recorded(*scene(changed_background, boxes=boxes, protected=True), assets)
    original_baseline = verify("spatial", "baseline", [initial])
    original_result = verify(
        "spatial", "counterpart", [candidate], {**original_baseline, "frames": [initial]}
    )
    # The exact previous recipe accepts either scene because both role boxes
    # remain plausible. The new version has independent evidence to check.
    assert original_result["accepted"] is True
    snapshot = build_recipe_snapshot("spatial", recipe_version="2.0.0", protected_objects=["book"])
    baseline = verify(
        "spatial", "baseline", [initial], recipe_snapshot=snapshot, media_reader=assets.__getitem__
    )
    assert baseline["accepted"] is True, baseline
    result = verify(
        "spatial",
        "counterpart",
        [candidate],
        {**baseline, "frames": [initial]},
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
    )
    assert result["accepted"] is (not move_background), result
    assert result["evidence"]["can_complete_pair"] is (not move_background)
    assert result["verifier_version"] == "counterpart-verifier/2.0.0"
    assert result["recipe_snapshot_sha256"] == snapshot["snapshot_sha256"]


def authorized_flat_pair():
    assets = {}
    snapshot = build_recipe_snapshot("spatial", recipe_version="2.0.0", protected_objects=["book"])
    initial = recorded(*scene(background(texture=False), protected=True), assets)
    first_result = verify(
        "spatial", "baseline", [initial], recipe_snapshot=snapshot, media_reader=assets.__getitem__
    )
    assert first_result["outcome"] == "unknown", first_result
    baseline = {**first_result, "frames": [initial]}
    authorization = {**baseline_authorization_binding(snapshot, baseline), "id": str(uuid4())}
    boxes = deepcopy(ROLE_BOXES)
    boxes["bottle"] = [0.77, 0.40, 0.87, 0.72]
    candidate = recorded(*scene(background(texture=False), boxes=boxes, protected=True), assets)
    return assets, snapshot, baseline, authorization, candidate


def authorized_result(assets, snapshot, baseline, authorization, candidate, *, reader=None):
    return verify(
        "spatial",
        "counterpart",
        [candidate],
        baseline,
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__ if reader is None else reader,
        baseline_authorization=authorization,
    )


def test_attack_baseline_authorization_does_not_waive_new_candidate_texture_check():
    assets, snapshot, baseline, authorization, candidate = authorized_flat_pair()
    before = deepcopy(baseline)
    result = authorized_result(assets, snapshot, baseline, authorization, candidate)
    assert result["accepted"] is False
    assert result["outcome"] == "unknown"
    assert result["human_review_required"] is True
    assert result["baseline_authorization_id"] == authorization["id"]
    assert any(c["code"] == "registration_uncertain" for c in result["checks"] if "code" in c)
    assert baseline == before
    assert baseline["accepted"] is False
    without_authorization = authorized_result(assets, snapshot, baseline, None, candidate)
    assert without_authorization["accepted"] is False
    assert without_authorization["hard_error"] is True


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "decision",
        "recipe_snapshot_sha256",
        "baseline_decision_sha256",
        "baseline_frames_sha256",
        "id",
    ],
)
def test_attack_foreign_or_malformed_authorization_binding_never_allows_continuation(field):
    assets, snapshot, baseline, authorization, candidate = authorized_flat_pair()
    authorization[field] = "not the bound value"
    result = authorized_result(assets, snapshot, baseline, authorization, candidate)
    assert result["accepted"] is False
    assert result["hard_error"] is True
    assert any(c.get("code") == "baseline_authorization_invalid" for c in result["checks"])


@pytest.mark.parametrize("mutation", ["timestamp", "decision-detail", "original-image"])
def test_attack_authorization_becomes_invalid_if_saved_baseline_changes(mutation):
    assets, snapshot, baseline, authorization, candidate = authorized_flat_pair()
    if mutation == "timestamp":
        baseline["frames"][0]["t_ms"] = 1
    elif mutation == "decision-detail":
        baseline["checks"][0]["detail"] = "Changed machine decision after the human authorized it"
    else:
        baseline["frames"][0] = recorded(
            *scene(Image.new("RGB", (WIDTH, HEIGHT), "#dddddd"), protected=True), assets
        )
    result = authorized_result(assets, snapshot, baseline, authorization, candidate)
    assert result["accepted"] is False
    assert result["hard_error"] is True
    assert any(c.get("code") == "baseline_authorization_invalid" for c in result["checks"])


@pytest.mark.parametrize("wrong_asset", ["baseline", "candidate"])
def test_attack_authorization_never_bypasses_original_byte_validation(wrong_asset):
    assets, snapshot, baseline, authorization, candidate = authorized_flat_pair()
    before = deepcopy(baseline)
    reference_id, candidate_id = baseline["frames"][0]["asset_id"], candidate["asset_id"]
    corrupt_id, other_id = (
        (reference_id, candidate_id) if wrong_asset == "baseline" else (candidate_id, reference_id)
    )

    def reader(asset_id):
        return assets[other_id if asset_id == corrupt_id else asset_id]

    result = authorized_result(assets, snapshot, baseline, authorization, candidate, reader=reader)
    assert result["accepted"] is False
    assert result["hard_error"] is True
    assert any(c.get("code") == "invalid_evidence" for c in result["checks"])
    assert baseline == before


def test_attack_authorized_baseline_does_not_hide_known_control_failure_behind_uncertainty():
    assets, snapshot, baseline, authorization, _ = authorized_flat_pair()
    boxes = deepcopy(ROLE_BOXES)
    boxes["bottle"] = [0.77, 0.40, 0.87, 0.72]
    boxes["cup"] = [0.30, 0.42, 0.44, 0.72]
    image, detections = scene(background(texture=False), boxes=boxes, protected=True)
    detections[0]["confidence"] = 0.50
    candidate = recorded(image, detections, assets)
    result = authorized_result(assets, snapshot, baseline, authorization, candidate)
    assert result["accepted"] is False
    assert result["outcome"] == "fail"
    assert any(c.get("code") == "reference_moved" and c["outcome"] == "fail" for c in result["checks"])


@pytest.mark.parametrize("mutation", ["wrong-relation", "missing-role", "known-failure-clause"])
def test_attack_known_bad_baseline_cannot_be_turned_into_eligible_unknown(mutation):
    _, snapshot, baseline, _, candidate = authorized_flat_pair()
    if mutation == "wrong-relation":
        baseline["frames"] = [candidate]
    elif mutation == "missing-role":
        baseline["frames"][0]["detections"] = [
            d for d in baseline["frames"][0]["detections"] if d["label"] != "bottle"
        ]
    else:
        baseline["checks"].append({"name": "known failure", "outcome": "fail", "passed": False})
    # Even a manipulated top-level uncertainty cannot substitute for observed
    # role geometry or conceal an independently known failed clause.
    baseline["outcome"] = "unknown"
    baseline["accepted"] = False
    with pytest.raises(ValueError):
        baseline_authorization_binding(snapshot, baseline)


def test_attack_authorization_eligibility_cannot_execute_a_drifted_historical_policy(monkeypatch):
    _, snapshot, baseline, _, _ = authorized_flat_pair()
    monkeypatch.setitem(THRESHOLDS, "minimum_confidence", 0.61)
    with pytest.raises(RecipeContractError):
        baseline_authorization_binding(snapshot, baseline)
