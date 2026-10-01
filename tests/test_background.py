"""Direct control-pixel contracts; generated pixels are not detector accuracy."""

from copy import deepcopy
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_visual_v2 import image_frame

from counterpart.background import BACKGROUND_VERSION, compare_background
from counterpart.storage import inspect_image
from counterpart.visual_checks import _load, _registration


def edited(assets, frame, operation):
    image = operation(Image.open(BytesIO(assets[frame["asset_id"]])).convert("RGB"))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    raw = buffer.getvalue()
    metadata = inspect_image(raw)
    assets[metadata["asset_id"]] = raw
    return {**deepcopy(frame), **metadata}


def pair(*, flat=True):
    assets = {}
    old = image_frame(assets, flat=flat)
    new = image_frame(assets, flat=flat, right=True)
    return assets, old, new


def compare(assets, old, new, *, registration=_registration, moving_labels=("bottle",)):
    return compare_background(
        _load(old, assets.__getitem__),
        _load(new, assets.__getitem__),
        0,
        moving_labels=moving_labels,
        registration=registration,
    )


def unknown(*args):
    return {
        "outcome": "unknown",
        "detail": "No observable transform.",
        "code": "registration_uncertain",
    }, None


def test_plain_initialization_does_not_pretend_to_measure_camera_pose():
    assets, old, _ = pair()
    check, transform = compare(assets, old, old)
    assert check["passed"]
    assert check["measurements"]["comparison"] == "reference_initialized"
    assert check["measurements"]["camera_pose_established"] is False
    np.testing.assert_array_equal(transform, np.eye(3))


def test_uncertain_registration_requires_independent_pixel_measurement():
    assets, old, new = pair()
    check, transform = compare(assets, old, new, registration=unknown)
    assert check["passed"]
    facts = check["measurements"]
    assert facts["background_version"] == BACKGROUND_VERSION
    assert facts["comparison"] == "direct_control_pixels"
    assert facts["control_fraction"] > 0.50
    assert facts["control_cells"] == 9
    assert facts["mean_residual"] == 0
    assert facts["camera_pose_established"] is False
    np.testing.assert_array_equal(transform, np.eye(3))


def test_known_camera_transform_failure_cannot_be_bypassed_by_identical_background():
    assets, old, new = pair()

    def forbidden_motion(*args):
        return {"outcome": "fail", "detail": "Restore camera.", "code": "camera_transform_changed"}, np.eye(3)

    check, transform = compare(assets, old, new, registration=forbidden_motion)
    assert check["outcome"] == "fail"
    assert check["code"] == "camera_transform_changed"
    assert transform is None


def test_unknown_transform_cannot_waive_changed_pixels():
    assets, old, new = pair()

    def change(image):
        ImageDraw.Draw(image).rectangle((300, 30, 420, 130), fill="black")
        return image

    new = edited(assets, new, change)
    check, transform = compare(assets, old, new, registration=unknown)
    assert check["outcome"] == "fail"
    assert check["code"] == "background_changed"
    assert check["measurements"]["largest_changed_component_pixels"] > 500
    assert transform is None


@pytest.mark.parametrize("translation", [(-2, 0), (2, 2), (0, -2)])
def test_small_jitter_uses_only_bounded_translation_when_features_are_unavailable(translation):
    assets, old, new = pair(flat=False)
    dx, dy = translation
    new = edited(
        assets, new, lambda image: image.transform(image.size, Image.Transform.AFFINE, (1, 0, dx, 0, 1, dy))
    )
    check, transform = compare(assets, old, new, registration=unknown)
    assert check["passed"], check
    assert abs(transform[0, 2]) <= 3 / 320
    assert abs(transform[1, 2]) <= 3 / 240


def test_too_little_control_region_remains_unknown():
    assets, old, new = pair()
    for frame in (old, new):
        frame["detections"][0]["box"] = [0.01, 0.01, 0.99, 0.99]
    check, transform = compare(assets, old, new, registration=unknown)
    assert check["outcome"] == "unknown"
    assert check["measurements"]["control_fraction"] == 0
    assert transform is None


def test_global_brightness_offset_is_measured_and_bounded():
    assets, old, new = pair()
    new = edited(
        assets,
        new,
        lambda image: Image.fromarray(np.clip(np.asarray(image).astype(int) - 14, 0, 255).astype(np.uint8)),
    )
    check, _ = compare(assets, old, new, registration=unknown)
    assert check["passed"]
    assert check["measurements"]["illumination_offset"] == pytest.approx([-14, -14, -14], abs=0.001)
    more = edited(
        assets,
        new,
        lambda image: Image.fromarray(np.clip(np.asarray(image).astype(int) - 40, 0, 255).astype(np.uint8)),
    )
    rejected, transform = compare(assets, old, more, registration=unknown)
    assert rejected["outcome"] == "unknown"
    assert "lighting" in rejected["detail"]
    assert transform is None


def test_repeated_evaluation_produces_identical_evidence():
    assets, old, new = pair(flat=False)
    first, first_transform = compare(assets, old, new)
    second, second_transform = compare(assets, old, new)
    assert first == second
    np.testing.assert_array_equal(first_transform, second_transform)


@pytest.mark.parametrize("flat", [True, False])
def test_smooth_cast_shadow_retains_background_structure(flat):
    assets, old, new = pair(flat=flat)

    def shadow(image):
        values = np.asarray(image).astype(np.float32)
        yy, xx = np.mgrid[: values.shape[0], : values.shape[1]]
        field = 55 * np.exp(-((xx - 440) ** 2 + (yy - 85) ** 2) / (2 * 48**2))
        return Image.fromarray(np.clip(values - field[:, :, None], 0, 255).astype(np.uint8))

    new = edited(assets, new, shadow)
    check, _ = compare(assets, old, new)
    assert check["passed"], check
    assert check["measurements"]["maximum_local_illumination_offset"] > 25


@pytest.mark.parametrize("flat", [True, False])
def test_gray_occluder_cannot_be_excused_as_photometric_change(flat):
    assets, old, new = pair(flat=flat)

    def occlude(image):
        ImageDraw.Draw(image).rectangle((360, 40, 470, 130), fill=(55, 55, 55))
        return image

    new = edited(assets, new, occlude)
    check, transform = compare(assets, old, new)
    assert check["outcome"] == "fail", check
    assert transform is None
