"""Independent attacks on texture-optional visual verification.

Pixels and detections are deterministic constructions, not real model accuracy
measurements. Exact invisible changes cannot be inferred from these observations.
"""

from copy import deepcopy
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps
from test_visual_attack import HEIGHT, ROLE_BOXES, WIDTH, background, recorded, scene

from counterpart.recipes import build_recipe_snapshot
from counterpart.verification import verify
from counterpart.visual_checks import evaluate_visual_controls

VERSION = "counterpart-visual/4.0.0"


def backdrop(kind="plain"):
    if kind == "textured":
        return background()
    image = Image.new("RGB", (WIDTH, HEIGHT), {"dark": "#303033", "light": "#eeeeee"}.get(kind, "#aaa9ab"))
    if kind == "sparse":
        image.paste(background().crop((0, 0, 150, 100)), (0, 0))
    elif kind == "horizon":
        ImageDraw.Draw(image).rectangle((0, 330, WIDTH, HEIGHT), fill="#99918a")
    return image


def after_scene(back, *, protected=False, colors=None, boxes=None):
    moved = deepcopy(ROLE_BOXES if boxes is None else boxes)
    moved["bottle"] = [0.77, 0.40, 0.87, 0.72]
    return scene(back, boxes=moved, protected=protected, colors=colors)


def evaluate(first, second, *, protected=(), recipe="spatial"):
    assets = {}
    reference = recorded(*first, assets)
    candidate = recorded(*second, assets)
    return evaluate_visual_controls(
        [candidate],
        [reference],
        media_reader=assets.__getitem__,
        protected_objects=protected,
        version=VERSION,
        recipe=recipe,
    )


def assert_blocked(result):
    assert result["outcome"] != "pass", result
    assert any(item["outcome"] != "pass" for item in result["checks"]), result


@pytest.mark.parametrize("kind", ["plain", "dark", "light", "sparse", "horizon", "textured"])
def test_same_scene_accepts_without_requiring_distributed_texture(kind):
    back = backdrop(kind)
    result = evaluate(scene(back), after_scene(back))
    assert result["outcome"] == "pass", result
    assert all(item["outcome"] == "pass" for item in result["checks"]), result


@pytest.mark.parametrize("kind", ["plain", "dark", "light", "sparse", "horizon"])
def test_first_capture_has_no_comparison_texture_precondition(kind):
    assets = {}
    frame = recorded(*scene(backdrop(kind)), assets)
    result = evaluate_visual_controls([frame], media_reader=assets.__getitem__, version=VERSION)
    assert result["outcome"] == "pass", result


@pytest.mark.parametrize("change", ["noise", "jpeg", "brighter", "dimmer"])
@pytest.mark.parametrize("kind", ["plain", "dark", "light", "sparse", "horizon", "textured"])
def test_camera_noise_compression_and_small_exposure_changes_are_tolerated(change, kind):
    back = backdrop(kind)
    image, detections = after_scene(back)
    if change == "jpeg":
        out = BytesIO()
        image.save(out, "JPEG", quality=85)
        image = Image.open(BytesIO(out.getvalue())).convert("RGB")
    else:
        values = np.asarray(image).astype(np.int16)
        adjustment = (
            np.random.default_rng(456).integers(-2, 3, values.shape)
            if change == "noise"
            else (12 if change == "brighter" else -12)
        )
        image = Image.fromarray(np.clip(values + adjustment, 0, 255).astype(np.uint8))
    result = evaluate(scene(back), (image, detections))
    assert result["outcome"] == "pass", result


@pytest.mark.parametrize("kind", ["plain", "sparse", "textured"])
@pytest.mark.parametrize("patch", [(390, 65, 455, 130), (80, 370, 220, 460)])
def test_local_background_change_is_not_hidden_by_majority_unchanged_pixels(kind, patch):
    back = backdrop(kind)
    changed = back.copy()
    ImageDraw.Draw(changed).rectangle(patch, fill="#104cb7")
    assert_blocked(evaluate(scene(back), after_scene(changed)))


@pytest.mark.parametrize("detected_label", ["chair", "person", "book"])
def test_irrelevant_detection_cannot_mask_changed_background(detected_label):
    back = backdrop("plain")
    changed = back.copy()
    ImageDraw.Draw(changed).rectangle((380, 30, 580, 150), fill="#1468a3")
    first = scene(back)
    second = after_scene(changed)
    # A giant non-role detector box must not become permission to ignore the scene.
    extra = {"label": detected_label, "confidence": 0.93, "box": [0.01, 0.01, 0.99, 0.99]}
    first[1].append(deepcopy(extra))
    second[1].append(deepcopy(extra))
    assert_blocked(evaluate(first, second))


def test_large_unchanged_furniture_detection_does_not_erase_appearance_evidence():
    back = backdrop("plain")
    first, second = scene(back), after_scene(back)
    extra = {"label": "chair", "confidence": 0.93, "box": [0.01, 0.01, 0.99, 0.99]}
    first[1].append(deepcopy(extra))
    second[1].append(deepcopy(extra))
    result = evaluate(first, second)
    assert result["outcome"] == "pass", result


def test_major_lighting_change_remains_blocking():
    back = backdrop("plain")
    image, detections = after_scene(back)
    changed = Image.fromarray(np.clip(np.asarray(image).astype(np.int16) - 60, 0, 255).astype(np.uint8))
    assert_blocked(evaluate(scene(back), (changed, detections)))


def test_undetected_hand_intruding_in_plain_background_is_rejected():
    back = backdrop()
    changed = back.copy()
    ImageDraw.Draw(changed).ellipse((320, 65, 500, 200), fill="#c1947b")
    assert_blocked(evaluate(scene(back), after_scene(changed)))


@pytest.mark.parametrize("kind", ["sparse", "horizon", "textured"])
@pytest.mark.parametrize("movement", ["pan", "rotate", "zoom", "mirror"])
def test_observable_camera_changes_remain_blocking_with_stationary_role_boxes(kind, movement):
    back = backdrop(kind)
    if movement == "pan":
        changed = back.transform(back.size, Image.Transform.AFFINE, (1, 0, 70, 0, 1, 45), fillcolor="#aaa9ab")
    elif movement == "rotate":
        changed = back.rotate(8, resample=Image.Resampling.BILINEAR, fillcolor="#aaa9ab")
    elif movement == "zoom":
        changed = back.resize((768, 576)).crop((64, 48, 704, 528))
    else:
        if kind == "horizon":
            # Horizontal mirror leaves a horizontal horizon exactly unchanged.
            changed = ImageOps.flip(back)
        else:
            changed = ImageOps.mirror(back)
    assert_blocked(evaluate(scene(back), after_scene(changed)))


@pytest.mark.parametrize("label", ["bottle", "cup", "book"])
def test_flat_background_does_not_waive_changed_object_appearance(label):
    back = backdrop()
    result = evaluate(
        scene(back, protected=True),
        after_scene(back, protected=True, colors={label: "#ff00ee"}),
        protected=("book",),
    )
    assert_blocked(result)


def test_flat_background_does_not_waive_protected_movement():
    back = backdrop()
    boxes = deepcopy(ROLE_BOXES)
    boxes["book"] = [0.44, 0.13, 0.59, 0.29]
    result = evaluate(
        scene(back, protected=True), after_scene(back, boxes=boxes, protected=True), protected=("book",)
    )
    assert_blocked(result)


def test_temporal_intermediate_background_change_is_not_hidden_by_good_endpoints():
    assets = {}
    back = backdrop()
    good = recorded(*scene(back), assets)
    changed = back.copy()
    ImageDraw.Draw(changed).rectangle((390, 65, 455, 130), fill="#104cb7")
    bad = recorded(*scene(changed), assets, timestamp=250)
    end = {**good, "t_ms": 500}
    result = evaluate_visual_controls(
        [good, bad, end], [good], media_reader=assets.__getitem__, version=VERSION, recipe="temporal"
    )
    assert_blocked(result)
    assert any(item["outcome"] != "pass" and item["name"].endswith(":1") for item in result["checks"])


@pytest.mark.parametrize("violation", ["wrong_relation", "moved_cup", "scaled_bottle", "missing_bottle"])
def test_full_recipe_keeps_independent_failure_cases_on_plain_background(violation):
    assets = {}
    back = backdrop()
    first = recorded(*scene(back), assets)
    snapshot = build_recipe_snapshot("spatial", recipe_version="2.3.0")
    initial = verify(
        "spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__
    )
    assert initial["accepted"], initial
    boxes = deepcopy(ROLE_BOXES)
    boxes["bottle"] = [0.77, 0.40, 0.87, 0.72]
    if violation == "wrong_relation":
        boxes["bottle"] = ROLE_BOXES["bottle"]
    elif violation == "moved_cup":
        boxes["cup"] = [0.53, 0.42, 0.67, 0.72]
    elif violation == "scaled_bottle":
        boxes["bottle"] = [0.73, 0.32, 0.91, 0.79]
    second_image, detections = scene(back, boxes=boxes)
    if violation == "missing_bottle":
        detections = [d for d in detections if d["label"] != "bottle"]
    second = recorded(second_image, detections, assets)
    result = verify(
        "spatial",
        "counterpart",
        [second],
        {**initial, "frames": [first]},
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
    )
    assert not result["accepted"], result
