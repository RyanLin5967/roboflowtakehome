"""Strict corresponding-object evidence for permitted overlapping temporal motion.

The caller owns registration, illumination correction, recipe permission and
geometry checks. Both the whole crop and its interior must independently agree;
this supplies measured appearance evidence, never an uncertainty waiver.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import cv2
import numpy as np


def compare_partial_overlap(
    old: np.ndarray,
    new: np.ndarray,
    boxes: Sequence[Sequence[int]],
    thresholds: Mapping[str, float],
) -> tuple[bool, dict]:
    """Compare registered, illumination-corrected RGB crops with existing limits.

    Inputs use pixel coordinates in the common registered view. The helper does not
    estimate a warp or lighting correction from the objects themselves. It cannot
    prove physical identity or accept a translucent object whose appearance changes
    too much over a different background.
    """
    measurements = {"method": "temporal_overlap_object_coordinates", "regions": {}}
    if (
        not isinstance(old, np.ndarray)
        or not isinstance(new, np.ndarray)
        or old.ndim != 3
        or new.ndim != 3
        or old.shape[2] != 3
        or new.shape[2] != 3
        or len(boxes) != 2
    ):
        measurements["reason"] = "invalid_image_or_boxes"
        return False, measurements
    crops = []
    for image, box in zip((old, new), boxes, strict=True):
        if len(box) != 4 or any(not np.isfinite(value) or int(value) != value for value in box):
            measurements["reason"] = "invalid_box"
            return False, measurements
        x1, y1, x2, y2 = map(int, box)
        if (
            x1 < 0
            or y1 < 0
            or x2 > image.shape[1]
            or y2 > image.shape[0]
            or min(x2 - x1, y2 - y1) < thresholds["minimum_object_side_pixels"]
        ):
            measurements["reason"] = "unobservable_crop"
            return False, measurements
        crop = image[y1:y2, x1:x2].astype(np.float32)
        if not np.isfinite(crop).all():
            measurements["reason"] = "invalid_pixels"
            return False, measurements
        crops.append(crop)
    accepted = True
    for name, inset in (("whole", False), ("interior", True)):
        patches = []
        for crop in crops:
            selected = crop
            if inset:
                height, width = crop.shape[:2]
                dx, dy = max(1, width // 10), max(1, height // 10)
                selected = crop[dy : height - dy, dx : width - dx]
            patches.append(cv2.resize(selected, (32, 32), interpolation=cv2.INTER_AREA))
        left, right = patches
        gray_left, gray_right = (cv2.cvtColor(value, cv2.COLOR_RGB2GRAY).ravel() for value in patches)
        std_left, std_right = float(gray_left.std() / 255), float(gray_right.std() / 255)
        difference = float(np.abs(left - right).mean() / 255)
        color_delta = (left.mean(axis=(0, 1)) - right.mean(axis=(0, 1))) / 255
        chromaticity = float(np.max(np.abs(color_delta - color_delta.mean())))
        correlation = (
            float(np.corrcoef(gray_left, gray_right)[0, 1])
            if min(std_left, std_right) >= thresholds["minimum_stationary_texture"]
            else None
        )
        if correlation is None:
            passed = (
                max(std_left, std_right) < thresholds["minimum_stationary_texture"]
                and difference <= thresholds["maximum_flat_pixel_difference"]
            )
        else:
            passed = (
                correlation >= thresholds["minimum_stationary_correlation"]
                and difference <= thresholds["maximum_stationary_pixel_difference"]
            )
        passed = passed and chromaticity <= thresholds["maximum_chromaticity_difference"]
        accepted = accepted and passed
        measurements["regions"][name] = {
            "passed": bool(passed),
            "pixel_difference": difference,
            "chromaticity_difference": chromaticity,
            "texture": [std_left, std_right],
            "correlation": correlation,
        }
    return bool(accepted), measurements
