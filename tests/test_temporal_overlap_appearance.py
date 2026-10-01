"""Real recipe regressions for sampled, partially overlapping object motion.

Images and detections are constructed together. No trained model is evaluated.
The positives move the same drawn object through intermediate positions, rather
than jumping from its first position directly to a non-overlapping final box.
"""

from copy import deepcopy

import numpy as np
import pytest
from PIL import ImageDraw
from test_background_adversarial import backdrop
from test_visual_attack import HEIGHT, WIDTH, recorded, scene

from counterpart.recipes import build_recipe_snapshot
from counterpart.verification import verify
from counterpart.visual_checks import evaluate_visual_controls

VERSION = "counterpart-visual/4.0.0"
EARLY = [0.20, 0.20, 0.24, 0.28, 0.42, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70]
LATE = [0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.20, 0.24, 0.28, 0.42, 0.70, 0.70, 0.70, 0.70]


def sequence(assets, *, reverse=False, kind="plain", edit=None, protected=False):
    frames = []
    first_xs, second_xs = (LATE, EARLY) if reverse else (EARLY, LATE)
    for index, (a, b) in enumerate(zip(first_xs, second_xs, strict=True)):
        boxes = {
            "bottle": [a - 0.05, 0.16, a + 0.05, 0.40],
            "cup": [b - 0.07, 0.62, b + 0.07, 0.82],
            "book": [0.75, 0.07, 0.90, 0.17],
        }
        image, detections = scene(backdrop(kind), boxes=boxes, protected=protected)
        if edit:
            image, detections = edit(image, detections, index)
        frames.append(recorded(image, detections, assets, timestamp=index * 250))
    return frames


def visual(frames, assets, reference=None, *, protected=(), recipe="temporal"):
    return evaluate_visual_controls(
        frames,
        reference,
        media_reader=assets.__getitem__,
        version=VERSION,
        recipe=recipe,
        protected_objects=protected,
    )


@pytest.mark.parametrize("kind", ["plain", "dark", "sparse", "textured"])
def test_temporal_partial_overlap_accepts_complete_reversed_arrival_pair(kind):
    assets = {}
    first = sequence(assets, kind=kind)
    second = sequence(assets, reverse=True, kind=kind)
    snapshot = build_recipe_snapshot("temporal", recipe_version="2.3.0")
    initial = verify("temporal", "baseline", first, recipe_snapshot=snapshot, media_reader=assets.__getitem__)
    assert initial["accepted"], initial
    result = verify(
        "temporal",
        "counterpart",
        second,
        {**initial, "frames": first},
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
    )
    assert result["accepted"], result
    assert result["evidence"]["arrival_order"] == ["cup", "bottle"]
    assert all(check["outcome"] == "pass" for check in result["checks"]), result


@pytest.mark.parametrize("label,index", [("bottle", 2), ("cup", 7)])
@pytest.mark.parametrize("change", ["recolored", "shape", "replacement", "hand_occlusion"])
def test_temporal_partial_overlap_cannot_hide_changed_object(label, index, change):
    def edit(image, detections, current):
        if current != index:
            return image, detections
        target = next(d for d in detections if d["label"] == label)
        x1, y1, x2, y2 = (
            round(value * dim)
            for value, dim in zip(target["box"], (WIDTH, HEIGHT, WIDTH, HEIGHT), strict=True)
        )
        draw = ImageDraw.Draw(image)
        if change == "recolored":
            draw.rectangle((x1, y1, x2, y2), fill="#c519d1")
        elif change == "shape":
            # Keep the detector's plausible outer box while removing a large
            # physical portion of the object. A box-only comparison is blind.
            draw.polygon(((x1, y1), (x1, y2), ((x1 + x2) // 2, y2)), fill="#aaa9ab")
        elif change == "replacement":
            draw.rectangle((x1, y1, x2, y2), fill="#358955" if label == "bottle" else "#e98139")
            for row in range(y1, y2, 8):
                draw.rectangle((x1, row, x2, min(row + 3, y2)), fill="#111111")
        else:
            draw.ellipse((x1, (y1 + y2) // 2, x2, y2), fill="#c1947b")
        return image, detections

    assets = {}
    frames = sequence(assets, edit=edit)
    result = visual(frames, assets)
    assert result["outcome"] != "pass", result
    assert any(
        check["outcome"] != "pass" and check["name"] == f"appearance_continuity:{label}:{index}"
        for check in result["checks"]
    ), result


def test_temporal_partial_overlap_does_not_waive_stationary_protected_movement():
    def edit(image, detections, index):
        if index != 2:
            return image, detections
        updated = deepcopy(detections)
        protected = next(d for d in updated if d["label"] == "book")
        protected["box"] = [0.70, 0.07, 0.85, 0.17]
        return image, updated

    assets = {}
    frames = sequence(assets, edit=edit, protected=True)
    result = visual(frames, assets, protected=("book",))
    assert result["outcome"] != "pass", result
    assert any(
        check["name"] == "protected_object:book:2" and check["outcome"] != "pass"
        for check in result["checks"]
    ), result


def test_temporal_overlap_solution_does_not_relax_spatial_appearance_policy():
    assets = {}
    frames = sequence(assets)
    result = visual([frames[2]], assets, [frames[0]], recipe="spatial")
    assert result["outcome"] != "pass", result
    assert any(
        check["name"] == "appearance_continuity:bottle:0" and check["outcome"] != "pass"
        for check in result["checks"]
    ), result


def test_historical_appearance_policy_remains_unchanged_for_overlap():
    assets = {}
    frames = sequence(assets, kind="textured")
    result = evaluate_visual_controls(
        [frames[2]],
        [frames[0]],
        media_reader=assets.__getitem__,
        version="counterpart-visual/3.0.0",
        recipe="temporal",
    )
    assert result["outcome"] != "pass", result
    assert any(
        check["name"] == "appearance_continuity:bottle:0" and check["outcome"] != "pass"
        for check in result["checks"]
    ), result


def test_overlap_comparison_checks_outer_shape_even_when_interior_is_unchanged():
    from counterpart.appearance import APPEARANCE_V4_THRESHOLDS
    from counterpart.appearance_overlap import compare_partial_overlap
    from counterpart.visual_checks import _load

    assets = {}
    frames = sequence(assets)
    old = _load(frames[0], assets.__getitem__)["rgb"].astype(np.float32)
    new = _load(frames[2], assets.__getitem__)["rgb"].astype(np.float32)
    boxes = [
        np.rint(np.asarray(frame["detections"][0]["box"]) * [WIDTH, HEIGHT, WIDTH, HEIGHT]).astype(int)
        for frame in (frames[0], frames[2])
    ]
    x1, y1, x2, y2 = boxes[1]
    new[y1:y2, x1 : x1 + 4] = [250, 0, 250]
    new[y1:y2, x2 - 4 : x2] = [250, 0, 250]
    new[y1 : y1 + 4, x1:x2] = [250, 0, 250]
    new[y2 - 4 : y2, x1:x2] = [250, 0, 250]
    passed, measurements = compare_partial_overlap(old, new, boxes, APPEARANCE_V4_THRESHOLDS)
    assert not passed, measurements
    assert measurements["regions"]["interior"]["passed"], measurements
    assert not measurements["regions"]["whole"]["passed"], measurements
