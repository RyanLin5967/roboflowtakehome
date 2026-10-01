"""Background2 requires pixel corroboration for contrast-amplified edges."""

from itertools import cycle

import numpy as np
import pytest
from PIL import Image
from test_background import pair
from test_background_adversarial import after_scene, backdrop, scene
from test_visual_attack import recorded

from counterpart.background import BACKGROUND_V2_VERSION, BACKGROUND_VERSION, compare_background
from counterpart.visual_checks import _load, _registration


def compare(assets, old, new, version=BACKGROUND_V2_VERSION):
    return compare_background(
        _load(old, assets.__getitem__),
        _load(new, assets.__getitem__),
        0,
        moving_labels=("bottle",),
        controlled_labels=("bottle", "cup"),
        registration=_registration,
        version=version,
    )[0]


def test_uncorroborated_derived_edges_cannot_overrule_consistent_original_pixels(monkeypatch):
    assets, old, new = pair()
    sides = cycle((0, 1))

    def amplified_noise(gray, *args):
        edges = np.zeros(gray.shape, np.uint8)
        edges[10:-10, 10 if next(sides) == 0 else gray.shape[1] - 10] = 255
        return edges

    monkeypatch.setattr("counterpart.background.cv2.Canny", amplified_noise)
    historical = compare(assets, old, new, BACKGROUND_VERSION)
    corrected = compare(assets, old, new)
    assert historical["outcome"] == "fail"
    assert corrected["outcome"] == "pass"
    assert corrected["measurements"]["unmatched_edge_fractions"] == [1.0, 1.0]
    assert corrected["measurements"]["corroborated_unmatched_edge_fractions"] == [0.0, 0.0]
    assert "corroborated_unmatched_edge_fractions" not in historical["measurements"]


@pytest.mark.parametrize("kind", ["horizon", "sparse", "textured"])
def test_real_camera_shift_retains_edge_corroboration(kind):
    assets = {}
    back = backdrop(kind)
    shifted = back.transform(back.size, Image.Transform.AFFINE, (1, 0, 70, 0, 1, 45), fillcolor="#aaa9ab")
    old, new = recorded(*scene(back), assets), recorded(*after_scene(shifted), assets)
    assert compare(assets, old, new)["outcome"] == "fail"


def test_unknown_background_version_fails_explicitly():
    assets, old, new = pair()
    with pytest.raises(ValueError, match="Unknown background"):
        compare(assets, old, new, "counterpart-background/999")
