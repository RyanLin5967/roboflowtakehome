"""Bounded background-only shadow correction; generated pixels, not model results."""

from io import BytesIO

import numpy as np
import pytest
from PIL import Image
from test_visual_v2 import image_frame

from counterpart.appearance import APPEARANCE_V4_VERSION, APPEARANCE_V5_VERSION, compare_appearance
from counterpart.storage import inspect_image
from counterpart.visual_checks import _load


def observations(*, altered=None):
    assets = {}
    first, second = image_frame(assets, flat=True), image_frame(assets, flat=True, right=True)
    raw = np.asarray(Image.open(BytesIO(assets[second["asset_id"]])).convert("RGB")).astype(float)
    y, x = np.mgrid[: raw.shape[0], : raw.shape[1]]
    field = -75 * np.exp(-((x - 170) ** 2 + (y - 80) ** 2) / (2 * 115**2))
    background = np.ones(raw.shape[:2], bool)
    for detection in second["detections"]:
        x1, y1, x2, y2 = np.rint(np.array(detection["box"]) * [640, 480, 640, 480]).astype(int)
        background[y1 : y2 + 1, x1 : x2 + 1] = False
        if altered == detection["label"]:
            raw[y1 : y2 + 1, x1 : x2 + 1] = [230, 20, 220]
    raw[background] += field[background, None]
    out = BytesIO()
    Image.fromarray(raw.clip(0, 255).astype(np.uint8)).save(out, "PNG")
    data = out.getvalue()
    metadata = inspect_image(data)
    assets[metadata["asset_id"]] = data
    second.update(metadata)
    return _load(first, assets.__getitem__), _load(second, assets.__getitem__)


def compare(old, new, label, version):
    return compare_appearance(
        old, new, label, 0, np.eye(3), version=version, background_labels=("bottle", "cup")
    )


@pytest.mark.parametrize("label", ["bottle", "cup"])
def test_smooth_background_shadow_is_not_object_appearance_change(label):
    old, new = observations()
    previous = compare(old, new, label, APPEARANCE_V4_VERSION)
    result = compare(old, new, label, APPEARANCE_V5_VERSION)
    assert previous["outcome"] == "unknown", previous
    assert previous["measurements"]["background_noise"] > 40
    assert result["outcome"] == "pass", result
    assert result["measurements"]["background_noise"] < 40


@pytest.mark.parametrize("label", ["bottle", "cup"])
def test_background_shadow_correction_cannot_fit_away_role_recoloring(label):
    old, new = observations(altered=label)
    result = compare(old, new, label, APPEARANCE_V5_VERSION)
    assert result["outcome"] != "pass", result


def test_new_shadow_estimator_does_not_mutate_historical_result():
    old, new = observations()
    before = compare(old, new, "bottle", APPEARANCE_V4_VERSION)
    compare(old, new, "bottle", APPEARANCE_V5_VERSION)
    assert compare(old, new, "bottle", APPEARANCE_V4_VERSION) == before


def test_shadow_tolerance_cannot_accept_erased_object_surface_detail():
    old, new = observations()
    old["rgb"] = old["rgb"].copy()
    new["rgb"] = new["rgb"].copy()
    height, width = new["rgb"].shape[:2]
    original_box = next(d["box"] for d in old["frame"]["detections"] if d["label"] == "bottle")
    a, b, c, d = np.rint(np.asarray(original_box) * [width, height, width, height]).astype(int)
    # The ordinary drawn bottle is solid; add visible surface detail to the
    # reference so removing that detail is an actual observable alteration.
    old["rgb"][b:d:3, a:c] = [240, 240, 240]
    box = next(d["box"] for d in new["frame"]["detections"] if d["label"] == "bottle")
    x1, y1, x2, y2 = np.rint(np.asarray(box) * [width, height, width, height]).astype(int)
    dx, dy = max(1, (x2 - x1) // 10), max(1, (y2 - y1) // 10)
    patch = new["rgb"][y1 + dy : y2 - dy, x1 + dx : x2 - dx]
    patch[:] = patch.mean(axis=(0, 1))
    result = compare(old, new, "bottle", APPEARANCE_V5_VERSION)
    assert result["outcome"] != "pass", result
    assert "surface detail" in result["detail"]
