"""Bounded appearance evidence using the other view's exposed background.

A moved transparent object changes the colors visible through it. Comparing its
raw RGB histogram therefore confuses the intervention with an object change.
This comparator measures observable shape and color continuity after registration
and background illumination correction. It does not establish physical identity,
texture continuity, or distinguish visually equivalent replacements.
"""

from __future__ import annotations

from types import MappingProxyType

import cv2
import numpy as np

APPEARANCE_VERSION = "counterpart-appearance/2.0.0"
APPEARANCE_METHOD = "registered background-relative silhouette and color; stationary photometric agreement"
APPEARANCE_THRESHOLDS = MappingProxyType(
    {
        "minimum_background_pixels": 1024,
        "background_margin_pixels": 12,
        "minimum_object_side_pixels": 16,
        "maximum_illumination_offset": 32.0,
        "maximum_background_noise": 40.0,
        "difference_noise_floor": 8.0,
        "background_noise_percentile": 95.0,
        "closing_kernel_pixels": 5,
        "profile_width": 96,
        "profile_height": 192,
        "maximum_stationary_center_delta": 0.03,
        "minimum_stationary_box_iou": 0.70,
        "maximum_moving_box_iou": 0.03,
        "maximum_alternate_object_overlap": 0.05,
        "minimum_difference_occupancy": 0.15,
        "minimum_silhouette_iou": 0.45,
        "maximum_row_profile_difference": 0.10,
        "maximum_foreground_color_difference": 0.12,
        "maximum_chromaticity_difference": 0.06,
        "minimum_stationary_correlation": 0.93,
        "maximum_stationary_pixel_difference": 0.06,
        "minimum_stationary_texture": 0.02,
        "maximum_flat_pixel_difference": 0.025,
    }
)


APPEARANCE_V3_VERSION = "counterpart-appearance/3.0.0"
APPEARANCE_V3_METHOD = (
    "registered exposed-background compositing residual by height band; "
    "stationary object-coordinate photometric agreement"
)
APPEARANCE_V3_THRESHOLDS = MappingProxyType(
    {
        **{
            key: value
            for key, value in APPEARANCE_THRESHOLDS.items()
            if key != "maximum_foreground_color_difference"
        },
        "compositing_bands": 5,
        "minimum_compositing_band_pixels": 8,
        "minimum_compositing_background_delta": 0.02,
        "maximum_compositing_residual": 0.06,
    }
)

APPEARANCE_V4_VERSION = "counterpart-appearance/4.0.0"
APPEARANCE_V4_METHOD = (
    "role-masked exposed-background compositing residual by height band; "
    "area-weighted silhouette profiles; stationary object-coordinate photometric agreement; "
    "strict whole/interior coordinate agreement for overlapping temporal movers; reference initialization"
)
# Numeric appearance tolerances remain unchanged. V4 retains unrelated furniture
# for illumination evidence and uses area-preserving silhouette sampling.
APPEARANCE_V4_THRESHOLDS = MappingProxyType(dict(APPEARANCE_V3_THRESHOLDS))


def _rounded(value):
    if isinstance(value, float):
        return round(value, 7)
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    return value


def _box(frame, label):
    matches = [d for d in frame["detections"] if d["label"] == label and d["confidence"] >= 0.35]
    if len(matches) != 1 or matches[0]["confidence"] < 0.60:
        return None
    return np.asarray(matches[0]["box"], dtype=float)


def _map_box(box, transform):
    x1, y1, x2, y2 = box
    points = np.array([[x1, y1, 1], [x2, y1, 1], [x2, y2, 1], [x1, y2, 1]]) @ transform.T
    return np.r_[points[:, :2].min(axis=0), points[:, :2].max(axis=0)]


def _iou(a, b):
    intersection = float(np.prod(np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]))))
    return intersection / max(1e-12, float(np.prod(a[2:] - a[:2]) + np.prod(b[2:] - b[:2])) - intersection)


def compare_appearance(
    reference: dict,
    current: dict,
    label: str,
    index: int,
    transform,
    *,
    version: str = APPEARANCE_VERSION,
    background_labels: tuple[str, ...] | None = None,
    allow_partial_overlap: bool = False,
) -> dict:
    """Return one required pass/fail/unknown clause from immutable decoded pixels."""
    if version not in (APPEARANCE_VERSION, APPEARANCE_V3_VERSION, APPEARANCE_V4_VERSION):
        raise ValueError("Unknown appearance comparison version.")
    role_masked = version == APPEARANCE_V4_VERSION
    compositing = version in (APPEARANCE_V3_VERSION, APPEARANCE_V4_VERSION)
    p = (
        APPEARANCE_V4_THRESHOLDS
        if role_masked
        else APPEARANCE_V3_THRESHOLDS
        if compositing
        else APPEARANCE_THRESHOLDS
    )
    if role_masked and (not background_labels or label not in background_labels):
        raise ValueError("Appearance v4 requires the declared object roles.")
    facts = {
        "label": label,
        "appearance_version": version,
        "reference_sha256": reference["sha256"],
        "candidate_sha256": current["sha256"],
    }

    def result(outcome, detail):
        return {
            "name": f"appearance_continuity:{label}:{index}",
            "outcome": outcome,
            "passed": outcome == "pass",
            "detail": detail,
            "code": "appearance_consistent"
            if outcome == "pass"
            else "appearance_changed"
            if outcome == "fail"
            else "appearance_uncertain",
            "measurements": _rounded(facts),
        }

    old_box, candidate_box = _box(reference["frame"], label), _box(current["frame"], label)
    if old_box is None or candidate_box is None or transform is None:
        return result(
            "unknown",
            f"Keep one clearly visible {label} and the same camera view."
            if role_masked
            else f"Keep one clearly visible {label} and a stable, textured background.",
        )
    transform = np.asarray(transform, dtype=float)
    if transform.shape != (3, 3) or not np.isfinite(transform).all():
        return result("unknown", "The saved image registration is unavailable.")
    if (
        role_masked
        and reference["sha256"] == current["sha256"]
        and np.array_equal(old_box, candidate_box)
        and np.array_equal(transform, np.eye(3))
    ):
        facts["method"] = "reference_initialized"
        return result("pass", f"The {label}'s original appearance is recorded for the next comparison.")
    old = reference["rgb"].astype(np.float32)
    h, w = old.shape[:2]
    nh, nw = current["rgb"].shape[:2]
    pixel_transform = np.diag([w, h, 1.0]) @ transform @ np.diag([1 / nw, 1 / nh, 1.0])
    new = cv2.warpAffine(current["rgb"], pixel_transform[:2], (w, h)).astype(np.float32)
    valid = cv2.warpAffine(
        np.ones((nh, nw), np.uint8), pixel_transform[:2], (w, h), flags=cv2.INTER_NEAREST
    ).astype(bool)
    background = valid.copy()
    background[:8] = background[-8:] = False
    background[:, :8] = background[:, -8:] = False
    margin = p["background_margin_pixels"]
    for image, mapping in ((reference, np.eye(3)), (current, transform)):
        for detection in image["frame"]["detections"]:
            if detection["confidence"] < 0.35:
                continue
            if role_masked and detection["label"] not in background_labels:
                continue
            box = _map_box(np.asarray(detection["box"]), mapping)
            x1, y1, x2, y2 = np.rint(box * [w, h, w, h]).astype(int)
            background[
                max(0, y1 - margin) : min(h, y2 + margin), max(0, x1 - margin) : min(w, x2 + margin)
            ] = False
    background_pixels = int(background.sum())
    facts["background_pixels"] = background_pixels
    if background_pixels < p["minimum_background_pixels"]:
        return result("unknown", "Leave more unchanged background visible around both objects.")
    offset = np.median((new - old)[background], axis=0)
    facts["background_illumination_offset"] = offset.tolist()
    if float(np.max(np.abs(offset))) > p["maximum_illumination_offset"]:
        return result("unknown", "The lighting changed too much; restore the original lighting.")
    new -= offset
    difference = np.max(np.abs(new - old), axis=2)
    noise = float(np.percentile(difference[background], p["background_noise_percentile"]))
    facts["background_noise"] = noise
    if noise > p["maximum_background_noise"]:
        return result("unknown", "The background changed too much for an appearance comparison.")
    new_box = _map_box(candidate_box, transform)
    boxes = [np.rint(box * [w, h, w, h]).astype(int) for box in (old_box, new_box)]
    if any(
        x1 < 1
        or y1 < 1
        or x2 >= w - 1
        or y2 >= h - 1
        or min(x2 - x1, y2 - y1) < p["minimum_object_side_pixels"]
        for x1, y1, x2, y2 in boxes
    ):
        return result("unknown", f"Keep the entire {label} visible and large enough in the picture.")
    if any(not valid[y1:y2, x1:x2].all() for x1, y1, x2, y2 in boxes):
        return result("unknown", "Keep both object positions inside the overlapping camera view.")
    box_iou = _iou(old_box, new_box)
    center_delta = float(np.linalg.norm((old_box[:2] + old_box[2:] - new_box[:2] - new_box[2:]) / 2))
    facts.update(box_iou=box_iou, registered_center_delta=center_delta)
    if box_iou >= p["minimum_stationary_box_iou"] and center_delta <= p["maximum_stationary_center_delta"]:
        facts["method"] = "stationary_registered_pixels"
        x1, y1, x2, y2 = boxes[0]
        dx, dy = max(1, (x2 - x1) // 10), max(1, (y2 - y1) // 10)
        a, b = old[y1 + dy : y2 - dy, x1 + dx : x2 - dx], new[y1 + dy : y2 - dy, x1 + dx : x2 - dx]
        if compositing:
            # Geometry is checked independently. Compare corresponding object
            # coordinates so permitted detector jitter/small movement is not
            # mistaken for an appearance change. No pixel-driven free warp.
            bx1, by1, bx2, by2 = boxes[1]
            bdx, bdy = max(1, (bx2 - bx1) // 10), max(1, (by2 - by1) // 10)
            b = new[by1 + bdy : by2 - bdy, bx1 + bdx : bx2 - bdx]
            facts["method"] = "stationary_object_coordinates"
        a, b = (
            cv2.resize(a, (32, 32), interpolation=cv2.INTER_AREA),
            cv2.resize(b, (32, 32), interpolation=cv2.INTER_AREA),
        )
        ga, gb = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).ravel(), cv2.cvtColor(b, cv2.COLOR_RGB2GRAY).ravel()
        std_a, std_b = float(ga.std() / 255), float(gb.std() / 255)
        pixel_difference = float(np.abs(a - b).mean() / 255)
        color_delta = (a.mean(axis=(0, 1)) - b.mean(axis=(0, 1))) / 255
        chromaticity = float(np.max(np.abs(color_delta - color_delta.mean())))
        correlation = (
            float(np.corrcoef(ga, gb)[0, 1]) if min(std_a, std_b) >= p["minimum_stationary_texture"] else None
        )
        facts.update(
            pixel_difference=pixel_difference,
            chromaticity_difference=chromaticity,
            texture=[std_a, std_b],
            correlation=correlation,
        )
        if correlation is None:
            passed = (
                max(std_a, std_b) < p["minimum_stationary_texture"]
                and pixel_difference <= p["maximum_flat_pixel_difference"]
            )
        else:
            passed = (
                correlation >= p["minimum_stationary_correlation"]
                and pixel_difference <= p["maximum_stationary_pixel_difference"]
            )
        passed = passed and chromaticity <= p["maximum_chromaticity_difference"]
        return result(
            "pass" if passed else "unknown",
            f"The {label}'s registered appearance is consistent after background lighting correction."
            if passed
            else (
                f"The comparison could not confirm the {label}'s appearance. "
                "Keep it unobstructed in steady lighting and retake the second photo."
                if compositing
                else f"The {label}'s appearance changed; restore it and its lighting, "
                "then retake the second photo."
            ),
        )
    if box_iou > p["maximum_moving_box_iou"]:
        if role_masked and allow_partial_overlap:
            from .appearance_overlap import compare_partial_overlap

            matched, overlap_facts = compare_partial_overlap(old, new, boxes, p)
            facts.update(overlap_facts)
            return result(
                "pass" if matched else "unknown",
                f"The {label}'s whole shape and interior appearance agree during overlapping motion."
                if matched
                else f"Keep the {label} unobstructed and facing the same way throughout the recording.",
            )
        return result(
            "unknown",
            f"Move the {label} clear of its first position so both exposed backgrounds can be compared.",
        )
    # A newly introduced or moved competing detection cannot be treated as
    # exposed background. Stable detected furniture may itself be background.
    for source, alternate, roi, source_map, alternate_map in (
        (reference, current, old_box, np.eye(3), transform),
        (current, reference, new_box, transform, np.eye(3)),
    ):
        for detection in alternate["frame"]["detections"]:
            if detection["label"] == label or detection["confidence"] < 0.35:
                continue
            other = _map_box(np.asarray(detection["box"]), alternate_map)
            intersection = float(
                np.prod(np.maximum(0, np.minimum(roi[2:], other[2:]) - np.maximum(roi[:2], other[:2])))
            )
            overlap = intersection / max(1e-12, float(np.prod(roi[2:] - roi[:2])))
            if overlap <= p["maximum_alternate_object_overlap"]:
                continue
            stable = any(
                previous["label"] == detection["label"]
                and previous["confidence"] >= 0.35
                and _iou(_map_box(np.asarray(previous["box"]), source_map), other)
                >= p["minimum_stationary_box_iou"]
                for previous in source["frame"]["detections"]
            )
            if not stable:
                return result(
                    "unknown",
                    "Another object covers the exposed background; "
                    "restore that object before retaking the second photo.",
                )
    facts["method"] = "exposed_background_silhouette"
    threshold = max(p["difference_noise_floor"], noise)
    changed = (cv2.medianBlur(difference, 3) > threshold).astype(np.uint8)
    kernel = np.ones((p["closing_kernel_pixels"], p["closing_kernel_pixels"]), dtype=np.uint8)
    changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel)
    silhouettes, colors, occupancies = [], [], []
    band_foregrounds, band_backgrounds = [], []
    for index_, (x1, y1, x2, y2) in enumerate(boxes):
        mask = changed[y1:y2, x1:x2].astype(bool)
        occupancy = float(mask.mean())
        if occupancy < p["minimum_difference_occupancy"]:
            return result(
                "unknown",
                f"The {label} blends into the background; use a contrasting background and retake the pair.",
            )
        foreground = old if index_ == 0 else new
        colors.append(foreground[y1:y2, x1:x2][mask].mean(axis=0) / 255)
        if compositing:
            # The other registered frame supplies the background exposed when
            # this object moved. A single fitted scalar per vertical band must
            # explain all three color-channel changes; arbitrary recoloring
            # cannot be independently fitted per channel.
            background_image = new if index_ == 0 else old
            fg = foreground[y1:y2, x1:x2].astype(np.float64) / 255
            bg = background_image[y1:y2, x1:x2].astype(np.float64) / 255
            foreground_means, background_means = [], []
            height = mask.shape[0]
            for band in range(p["compositing_bands"]):
                section = slice(
                    band * height // p["compositing_bands"], (band + 1) * height // p["compositing_bands"]
                )
                support = mask[section]
                if int(support.sum()) < p["minimum_compositing_band_pixels"]:
                    return result(
                        "unknown",
                        f"Part of the {label} is indistinguishable from its "
                        "background; use clearer lighting and retake the pair.",
                    )
                foreground_means.append(fg[section][support].mean(axis=0))
                background_means.append(bg[section][support].mean(axis=0))
            band_foregrounds.append(foreground_means)
            band_backgrounds.append(background_means)
        occupancies.append(occupancy)
        silhouette = cv2.resize(
            mask.astype(np.float32) if role_masked else mask.astype(np.uint8),
            (p["profile_width"], p["profile_height"]),
            interpolation=cv2.INTER_AREA if role_masked else cv2.INTER_NEAREST,
        )
        # Fractional cell coverage preserves silhouette area across resolutions.
        # Nearest-neighbor sampling can turn a JPEG edge fluctuation into a
        # different row width just by selecting a different source pixel.
        silhouettes.append(silhouette if role_masked else silhouette.astype(bool))
    intersection = (
        float(np.minimum(silhouettes[0], silhouettes[1]).sum())
        if role_masked
        else int((silhouettes[0] & silhouettes[1]).sum())
    )
    union = (
        float(np.maximum(silhouettes[0], silhouettes[1]).sum())
        if role_masked
        else int((silhouettes[0] | silhouettes[1]).sum())
    )
    silhouette_iou = intersection / max(1, union)
    profile = float(np.abs(silhouettes[0].mean(axis=1) - silhouettes[1].mean(axis=1)).mean())
    delta = colors[0] - colors[1]
    color_difference = float(np.abs(delta).mean())
    chromaticity = float(np.max(np.abs(delta - delta.mean())))
    facts.update(
        difference_threshold=threshold,
        difference_occupancy=occupancies,
        silhouette_iou=silhouette_iou,
        row_profile_difference=profile,
        foreground_color_difference=color_difference,
        chromaticity_difference=chromaticity,
    )
    passed = (
        silhouette_iou >= p["minimum_silhouette_iou"]
        and profile <= p["maximum_row_profile_difference"]
        and (compositing or color_difference <= p["maximum_foreground_color_difference"])
        and chromaticity <= p["maximum_chromaticity_difference"]
    )
    if compositing:
        delta_foreground = np.asarray(band_foregrounds[1]) - band_foregrounds[0]
        delta_background = np.asarray(band_backgrounds[1]) - band_backgrounds[0]
        squared = np.sum(delta_background**2, axis=1)
        identifiable = squared >= p["minimum_compositing_background_delta"] ** 2
        coefficients = np.zeros(p["compositing_bands"], dtype=float)
        coefficients[identifiable] = np.clip(
            np.sum(delta_foreground[identifiable] * delta_background[identifiable], axis=1)
            / squared[identifiable],
            0,
            1,
        )
        residuals = delta_foreground - coefficients[:, None] * delta_background
        maximum_residual = float(np.max(np.abs(residuals)))
        facts.update(
            method="exposed_background_compositing",
            band_foreground_changes=delta_foreground.tolist(),
            band_background_changes=delta_background.tolist(),
            band_fit_coefficients=coefficients.tolist(),
            band_unexplained_changes=residuals.tolist(),
            maximum_compositing_residual=maximum_residual,
        )
        passed = (
            silhouette_iou >= p["minimum_silhouette_iou"]
            and profile <= p["maximum_row_profile_difference"]
            and maximum_residual <= p["maximum_compositing_residual"]
        )
    return result(
        "pass" if passed else "unknown",
        f"The {label}'s background-relative shape and color are consistent; "
        "this does not prove physical identity or unchanged texture."
        if passed
        else (
            f"The comparison could not confirm the {label}'s appearance. "
            "Keep the same object unobstructed in steady lighting and retake the second photo."
            if compositing
            else f"The {label}'s shape or color differs; restore the same object and lighting, "
            "then retake the second photo."
        ),
    )
