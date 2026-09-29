"""Generated image controls for the registered, background-relative comparator.

These are algorithm regressions, not a real-world false-acceptance benchmark.
"""

from copy import deepcopy
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_visual_v2 import image_frame

from counterpart.appearance import (
    APPEARANCE_THRESHOLDS,
    APPEARANCE_V3_VERSION,
    APPEARANCE_VERSION,
    compare_appearance,
)
from counterpart.storage import inspect_image
from counterpart.visual_checks import _appearance, _load, _registration


def changed(assets, frame, edit):
    image = Image.open(BytesIO(assets[frame["asset_id"]])).convert("RGB")
    image = edit(image)
    output = BytesIO()
    image.save(output, format="PNG")
    raw = output.getvalue()
    metadata = inspect_image(raw)
    assets[metadata["asset_id"]] = raw
    return {**deepcopy(frame), **metadata}


def compare(assets, a, b, label="bottle", transform=None, *, version=APPEARANCE_VERSION):
    old, new = _load(a, assets.__getitem__), _load(b, assets.__getitem__)
    if transform is None:
        registration, transform = _registration(old, new, 0)
        assert registration["outcome"] == "pass", registration
    return compare_appearance(old, new, label, 0, transform, version=version)


def paint_box(image, color, *, label="bottle", inner=False):
    image = image.copy()
    box = (474, 192, 538, 326) if label == "bottle" else (282, 192, 346, 326)
    if inner:
        box = (box[0] + 6, box[1] + 13, box[2] - 6, box[3] - 13)
    ImageDraw.Draw(image).rectangle(box, fill=color)
    return image


@pytest.mark.parametrize("illumination", [0, 12, -12])
def test_global_illumination_does_not_turn_same_object_into_change(illumination):
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    if illumination:
        second = changed(
            assets,
            second,
            lambda image: Image.fromarray(
                np.clip(np.asarray(image).astype(int) + illumination, 0, 255).astype(np.uint8)
            ),
        )
    for label in ("bottle", "cup"):
        result = compare(assets, first, second, label)
        assert result["passed"], result
        assert result["measurements"]["reference_sha256"] == first["asset_id"]
        assert result["measurements"]["candidate_sha256"] == second["asset_id"]


def test_one_intensity_bin_boundary_no_longer_changes_maximum_histogram_distance():
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    second = changed(assets, second, lambda image: paint_box(image, (31, 128, 64)))
    old, new = _load(first, assets.__getitem__), _load(second, assets.__getitem__)
    assert _appearance(old, new, "bottle", 0)["outcome"] == "unknown"
    result = compare(assets, first, second)
    assert result["passed"], result


@pytest.mark.parametrize("color", [(64, 128, 64), (128, 128, 128), (30, 30, 230), (230, 30, 30)])
def test_object_color_changes_are_not_waived(color):
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    second = changed(assets, second, lambda image: paint_box(image, color))
    result = compare(assets, first, second)
    assert not result["passed"], result
    assert result["outcome"] == "unknown"


def test_changed_stationary_cup_is_not_normalized_away():
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    second = changed(assets, second, lambda image: paint_box(image, (50, 50, 180), label="cup"))
    assert not compare(assets, first, second, "cup")["passed"]


def test_new_competing_object_in_exposed_background_blocks_comparison():
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    second["detections"].append({"label": "book", "confidence": 0.9, "box": [0.14, 0.40, 0.24, 0.68]})
    result = compare(assets, first, second)
    assert not result["passed"]
    assert "covers the exposed background" in result["detail"]


def test_stable_background_object_can_be_revealed_after_object_moves():
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    background_object = {"label": "suitcase", "confidence": 0.9, "box": [0.12, 0.38, 0.26, 0.70]}
    first["detections"].append(deepcopy(background_object))
    second["detections"].append(deepcopy(background_object))
    result = compare(assets, first, second)
    assert result["passed"], result


def test_unknown_registration_and_excessive_lighting_are_not_accepted():
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    old, new = _load(first, assets.__getitem__), _load(second, assets.__getitem__)
    assert not compare_appearance(old, new, "bottle", 0, None)["passed"]
    second = changed(
        assets,
        second,
        lambda image: Image.fromarray(np.clip(np.asarray(image).astype(int) - 60, 0, 255).astype(np.uint8)),
    )
    result = compare(assets, first, second)
    assert not result["passed"]
    assert "lighting changed too much" in result["detail"]


def test_overlapping_positions_cannot_supply_exposed_background():
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    second["detections"][0]["box"] = [0.18, 0.40, 0.28, 0.68]
    result = compare(assets, first, second)
    assert not result["passed"]
    assert "clear of its first position" in result["detail"]


def test_out_of_view_transformation_cannot_count_black_padding_as_evidence():
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    transform = np.array([[1.0, 0.0, -0.18], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    result = compare(assets, first, second, transform=transform)
    assert not result["passed"]


def test_threshold_contract_is_immutable():
    with pytest.raises(TypeError):
        APPEARANCE_THRESHOLDS["maximum_row_profile_difference"] = 1


def transparent_pair(seed=3, *, dark_background=False, opacity=0.4):
    """Known image construction: same translucent silhouette over two backgrounds."""
    rng = np.random.default_rng(seed)
    base = Image.new("RGB", (640, 480), (215, 218, 221))
    draw = ImageDraw.Draw(base)
    if dark_background:
        draw.rectangle((420, 0, 639, 479), fill=(25, 35, 45))
    for _ in range(800):
        x, y = rng.integers(0, 630), rng.integers(0, 470)
        color = tuple(int(v) for v in rng.integers(50, 230, size=3))
        draw.rectangle((int(x), int(y), int(x + 7), int(y + 7)), fill=color)
    # A fixed textured reference makes erased/inverted appearance observable.
    for row in range(180, 330, 10):
        draw.rectangle((282, row, 346, row + 9), fill=(80, 100, 120) if row % 20 else (170, 150, 130))
    assets, frames = {}, []
    for left in (80, 470):
        silhouette = Image.new("L", base.size)
        ImageDraw.Draw(silhouette).polygon(
            [
                (left + 22, 120),
                (left + 58, 120),
                (left + 58, 150),
                (left + 80, 180),
                (left + 80, 350),
                (left, 350),
                (left, 180),
                (left + 22, 150),
            ],
            fill=255,
        )
        a = np.asarray(base).astype(float)
        mask = np.asarray(silhouette).astype(float)[..., None] / 255
        composite = a * (1 - opacity * mask) + np.array([90, 105, 100]) * opacity * mask
        image = Image.fromarray(composite.astype(np.uint8))
        ImageDraw.Draw(image).rectangle((left + 22, 120, left + 58, 143), fill=(30, 105, 65))
        out = BytesIO()
        image.save(out, format="PNG")
        raw = out.getvalue()
        metadata = inspect_image(raw)
        assets[metadata["asset_id"]] = raw
        frames.append(
            {
                **metadata,
                "t_ms": 0.0,
                "detections": [
                    {
                        "label": "bottle",
                        "confidence": 0.96,
                        "box": [left / 640, 120 / 480, (left + 80) / 640, 350 / 480],
                    },
                    {"label": "cup", "confidence": 0.96, "box": [282 / 640, 180 / 480, 346 / 640, 330 / 480]},
                ],
            }
        )
    return assets, *frames


@pytest.mark.parametrize("seed", [3, 8, 23])
def test_transparent_object_uses_revealed_background_and_preserves_shape(seed):
    assets, first, second = transparent_pair(seed)
    for label in ("bottle", "cup"):
        result = compare(assets, first, second, label)
        assert result["passed"], result
    assert compare(assets, first, second)["measurements"]["method"] == "exposed_background_silhouette"


@pytest.mark.parametrize("control", ["erased", "inverted", "shuffled"])
def test_stationary_texture_destruction_does_not_pass(control):
    assets, first, second = transparent_pair()

    def edit(image):
        data = np.array(image)
        patch = data[180:331, 282:347]
        if control == "erased":
            patch[:] = patch.mean(axis=(0, 1)).astype(np.uint8)
        elif control == "inverted":
            patch[:] = 255 - patch
        else:
            patch[:] = np.random.default_rng(10).permutation(patch.reshape(-1, 3)).reshape(patch.shape)
        return Image.fromarray(data)

    second = changed(assets, second, edit)
    result = compare(assets, first, second, "cup")
    assert not result["passed"], result


def test_moving_object_with_entirely_different_silhouette_is_not_accepted():
    assets, first, second = transparent_pair()

    def edit(image):
        image = image.copy()
        ImageDraw.Draw(image).rectangle((470, 120, 550, 350), fill=(120, 125, 120))
        return image

    second = changed(assets, second, edit)
    result = compare(assets, first, second)
    assert not result["passed"], result
    assert result["measurements"]["row_profile_difference"] > 0.1


def test_detections_without_observed_foreground_difference_cannot_supply_evidence():
    assets, first, second = transparent_pair()
    # Detector geometry alone claims a move, while exact original pixels show none.
    second = {**deepcopy(first), "detections": deepcopy(second["detections"])}
    result = compare(assets, first, second)
    assert not result["passed"]
    assert "blends into the background" in result["detail"]


@pytest.mark.parametrize("seed", [3, 8, 23])
def test_v3_transparent_shape_moving_across_light_and_dark_backgrounds(seed):
    assets, first, second = transparent_pair(seed, dark_background=True)
    legacy = compare(assets, first, second)
    assert not legacy["passed"]
    result = compare(assets, first, second, version=APPEARANCE_V3_VERSION)
    assert result["passed"], result
    assert result["measurements"]["method"] == "exposed_background_compositing"
    assert result["measurements"]["foreground_color_difference"] > 0.12
    assert result["measurements"]["maximum_compositing_residual"] < 0.06
    assert all(0 <= coefficient <= 1 for coefficient in result["measurements"]["band_fit_coefficients"])


@pytest.mark.parametrize("color", [(230, 30, 30), (30, 30, 230)])
def test_v3_localized_cap_recolor_is_not_averaged_out(color):
    assets, first, second = transparent_pair(dark_background=True)

    def edit(image):
        image = image.copy()
        ImageDraw.Draw(image).rectangle((492, 120, 528, 143), fill=color)
        return image

    second = changed(assets, second, edit)
    result = compare(assets, first, second, version=APPEARANCE_V3_VERSION)
    assert not result["passed"], result
    assert result["measurements"]["maximum_compositing_residual"] > 0.06
    assert "could not confirm" in result["detail"]


def test_v3_stationary_object_coordinates_respect_allowed_small_position_change():
    assets, first, second = transparent_pair()

    def edit(image):
        image = image.copy()
        crop = image.crop((282, 180, 347, 331))
        image.paste(crop, (282, 184))
        return image

    second = changed(assets, second, edit)
    second["detections"][1]["box"] = [282 / 640, 184 / 480, 346 / 640, 334 / 480]
    legacy = compare(assets, first, second, "cup")
    assert not legacy["passed"]
    result = compare(assets, first, second, "cup", version=APPEARANCE_V3_VERSION)
    assert result["passed"], result
    assert result["measurements"]["method"] == "stationary_object_coordinates"
    assert result["measurements"]["registered_center_delta"] < 0.01


@pytest.mark.parametrize("color", [(64, 128, 64), (128, 128, 128), (30, 30, 230), (230, 30, 30)])
def test_v3_compositing_does_not_explain_arbitrary_object_recoloring(color):
    assets = {}
    first, second = image_frame(assets), image_frame(assets, right=True)
    second = changed(assets, second, lambda image: paint_box(image, color))
    result = compare(assets, first, second, version=APPEARANCE_V3_VERSION)
    assert not result["passed"], result


def test_v3_does_not_change_legacy_result_on_same_pixels():
    assets, first, second = transparent_pair(dark_background=True)
    before = compare(assets, first, second)
    assert compare(assets, first, second, version=APPEARANCE_V3_VERSION)["passed"]
    after = compare(assets, first, second)
    assert after == before
    assert not after["passed"]


@pytest.mark.parametrize("opacity", [0.15, 0.2, 0.25])
def test_v3_checks_background_explanation_even_when_raw_color_claimed_strong_failure(opacity):
    # Explicit alpha-compositing fixture: identical material and shape move
    # from a bright to a dark background. Raw RGB appearances should differ.
    assets, first, second = transparent_pair(dark_background=True, opacity=opacity)
    old, new = _load(first, assets.__getitem__), _load(second, assets.__getitem__)
    legacy = _appearance(old, new, "bottle", 0)
    assert legacy["outcome"] == "fail"
    assert legacy["measurements"]["patch_difference"] > 0.35
    result = compare(assets, first, second, version=APPEARANCE_V3_VERSION)
    assert result["passed"], result
    assert result["measurements"]["maximum_compositing_residual"] < 0.01


@pytest.mark.parametrize("control", ["erased", "inverted", "shuffled"])
def test_v3_stationary_alignment_does_not_excuse_destroyed_texture(control):
    assets, first, second = transparent_pair()

    def edit(image):
        data = np.array(image)
        patch = data[180:331, 282:347]
        if control == "erased":
            patch[:] = patch.mean(axis=(0, 1)).astype(np.uint8)
        elif control == "inverted":
            patch[:] = 255 - patch
        else:
            patch[:] = np.random.default_rng(10).permutation(patch.reshape(-1, 3)).reshape(patch.shape)
        return Image.fromarray(data)

    second = changed(assets, second, edit)
    assert not compare(assets, first, second, "cup", version=APPEARANCE_V3_VERSION)["passed"]
