"""Plain-background appearance controls using generated pixels, not model outputs."""

from copy import deepcopy

import numpy as np
import pytest
from test_appearance_comparison import changed, paint_box, transparent_pair
from test_visual_v2 import image_frame

from counterpart.appearance import APPEARANCE_V3_VERSION, APPEARANCE_V4_VERSION, compare_appearance
from counterpart.visual_checks import _load


def appearance(assets, first, second, label="bottle", **kwargs):
    return compare_appearance(
        _load(first, assets.__getitem__),
        _load(second, assets.__getitem__),
        label,
        0,
        np.eye(3),
        version=APPEARANCE_V4_VERSION,
        background_labels=("bottle", "cup"),
        **kwargs,
    )


@pytest.mark.parametrize("label", ["bottle", "cup"])
def test_flat_reference_is_initialized_without_claiming_a_camera_comparison(label):
    assets = {}
    first = image_frame(assets, flat=True)
    result = appearance(assets, first, first, label)
    assert result["passed"]
    assert result["measurements"]["method"] == "reference_initialized"
    assert "next comparison" in result["detail"]


@pytest.mark.parametrize("label", ["bottle", "cup"])
def test_unrelated_furniture_detection_does_not_erase_background_for_appearance(label):
    assets = {}
    first, second = image_frame(assets, flat=True), image_frame(assets, right=True, flat=True)
    furniture = {"label": "dining table", "confidence": 0.95, "box": [0.001, 0.001, 0.999, 0.999]}
    first["detections"].append(deepcopy(furniture))
    second["detections"].append(deepcopy(furniture))
    result = appearance(assets, first, second, label)
    assert result["passed"], result
    assert result["measurements"]["background_pixels"] > 10000
    historical = compare_appearance(
        _load(first, assets.__getitem__),
        _load(second, assets.__getitem__),
        label,
        0,
        np.eye(3),
        version=APPEARANCE_V3_VERSION,
    )
    assert not historical["passed"]


@pytest.mark.parametrize("label", ["bottle", "cup"])
@pytest.mark.parametrize("color", [(230, 30, 30), (30, 30, 230), (128, 128, 128)])
def test_flat_background_does_not_waive_object_recoloring(label, color):
    assets = {}
    first, second = image_frame(assets, flat=True), image_frame(assets, right=True, flat=True)
    second = changed(assets, second, lambda image: paint_box(image, color, label=label))
    assert not appearance(assets, first, second, label)["passed"]


@pytest.mark.parametrize("label", ["bottle", "cup"])
def test_transparency_over_changed_exposed_background_still_uses_compositing(label):
    assets, first, second = transparent_pair(dark_background=True)
    result = appearance(assets, first, second, label)
    assert result["passed"], result


def test_v4_requires_explicit_role_mask_scope():
    assets = {}
    first = image_frame(assets)
    loaded = _load(first, assets.__getitem__)
    with pytest.raises(ValueError, match="declared object roles"):
        compare_appearance(loaded, loaded, "bottle", 0, np.eye(3), version=APPEARANCE_V4_VERSION)
