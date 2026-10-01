"""Bounded classical image checks over original, immutable evidence bytes.

These are observable appearance/registration tests, not physical identity or
3-D camera-pose proofs. ORB and RANSAC are conventional image algorithms: no
trained model, GPU, network call, or client-supplied transform is involved.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Callable, Sequence
from io import BytesIO
from types import MappingProxyType

import cv2
import numpy as np
from PIL import Image

VISUAL_VERSION = "counterpart-visual/1.0.0"
REGISTERED_APPEARANCE_VISUAL_VERSION = "counterpart-visual/2.0.0"
COMPOSITED_APPEARANCE_VISUAL_VERSION = "counterpart-visual/3.0.0"
PLAIN_BACKGROUND_VISUAL_VERSION = "counterpart-visual/4.0.0"
ROBUST_BACKGROUND_VISUAL_VERSION = "counterpart-visual/5.0.0"
# An immutable contract is copied into recipe v2. A change needs a new version.
VISUAL_THRESHOLDS = MappingProxyType(
    {
        "maximum_image_side": 640,
        "maximum_features": 900,
        "minimum_features": 24,
        "minimum_inliers": 14,
        "minimum_inlier_ratio": 0.65,
        "minimum_support_area": 0.12,
        "minimum_support_cells": 4,
        "match_ratio": 0.75,
        "ransac_pixels": 2.0,
        "ransac_iterations": 500,
        "maximum_camera_corner_displacement": 0.025,
        "maximum_camera_scale_change": 0.03,
        "maximum_camera_rotation_degrees": 2.0,
        "protected_center_tolerance": 0.025,
        "protected_scale_tolerance": 0.15,
        "appearance_histogram_pass": 0.22,
        "appearance_histogram_fail": 0.65,
        "appearance_patch_pass": 0.20,
        "appearance_patch_fail": 0.35,
        "detection_mask_margin": 0.03,
        "ambiguity_confidence": 0.35,
        "minimum_confidence": 0.60,
    }
)
VISUAL_LIMITATIONS = [
    "Background registration estimates a 2-D similarity transform, not calibrated camera motion; "
    "parallax, low texture, repeated texture, occlusion, and illumination changes can be inconclusive.",
    "Appearance continuity cannot distinguish visually identical replacements and does not prove "
    "physical-object identity. Appearance differences can also come from lighting or viewpoint.",
    "Protected-object checks cover only declared, confidently detected classes; detector omissions "
    "and undeclared scene changes can escape these checks.",
]
_CV_LOCK = threading.Lock()
_ASSET = re.compile(r"^[0-9a-f]{64}$")
# Bound native parallelism even when several workers execute concurrently.
cv2.setNumThreads(1)
cv2.ocl.setUseOpenCL(False)


class InvalidVisualEvidence(ValueError):
    """Original evidence is corrupt or inconsistent; human review cannot repair it."""


def _rounded(value):
    if isinstance(value, float):
        return round(value, 7)
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    return value


def _clause(name: str, outcome: str, detail: str, code: str, **measurements) -> dict:
    return {
        "name": name,
        "outcome": outcome,
        "passed": outcome == "pass",
        "detail": detail,
        "code": code,
        "measurements": _rounded(measurements),
    }


def _load(frame: dict, media_reader: Callable[[str], bytes]) -> dict:
    if not isinstance(frame, dict):
        raise InvalidVisualEvidence("Original image record is malformed.")
    asset_id = frame.get("asset_id")
    if not isinstance(asset_id, str) or not _ASSET.fullmatch(asset_id):
        raise InvalidVisualEvidence("Visual evidence needs an original content-addressed image.")
    raw = media_reader(asset_id)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 8_000_000:
        raise InvalidVisualEvidence("Original image size is invalid.")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != asset_id or frame.get("bytes", len(raw)) != len(raw):
        raise InvalidVisualEvidence("Original image does not match its recorded hash or length.")
    try:
        with Image.open(BytesIO(raw)) as original:
            width, height = original.size
            if (
                original.format not in ("JPEG", "PNG")
                or width * height > 4_000_000
                or min(width, height) < 80
                or max(width, height) > 2560
                or (frame.get("width"), frame.get("height")) != (width, height)
                or getattr(original, "n_frames", 1) != 1
                or original.getexif().get(274, 1) != 1
            ):
                raise InvalidVisualEvidence("Original image dimensions or format are inconsistent.")
            original.load()
            image = original.convert("RGB")
            side = VISUAL_THRESHOLDS["maximum_image_side"]
            image.thumbnail((side, side), Image.Resampling.LANCZOS)
            rgb = np.array(image)
    except (OSError, Image.DecompressionBombError) as exc:
        raise InvalidVisualEvidence("Original image cannot be decoded safely.") from exc
    mask = np.full(rgb.shape[:2], 255, dtype=np.uint8)
    detections = frame.get("detections")
    if not isinstance(detections, list) or len(detections) > 1000:
        raise InvalidVisualEvidence("Observed detections are missing or unbounded.")
    for detection in detections:
        if not isinstance(detection, dict):
            raise InvalidVisualEvidence("Observed detection is malformed.")
        box = detection.get("box")
        confidence = detection.get("confidence")
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or any(type(v) not in (float, int) or not 0 <= v <= 1 or not math.isfinite(v) for v in box)
            or box[0] >= box[2]
            or box[1] >= box[3]
            or type(confidence) not in (float, int)
            or not 0 <= confidence <= 1
            or not math.isfinite(confidence)
            or not isinstance(detection.get("label"), str)
            or not detection["label"]
        ):
            raise InvalidVisualEvidence("Observed detection geometry is invalid.")
        if confidence >= VISUAL_THRESHOLDS["ambiguity_confidence"]:
            # Exclude both moving roles AND all other detected objects from
            # registration. Expanded boxes reduce contour/box-jitter leakage.
            h, w = mask.shape
            margin = VISUAL_THRESHOLDS["detection_mask_margin"]
            x1, y1 = max(0, int((box[0] - margin) * w)), max(0, int((box[1] - margin) * h))
            x2, y2 = min(w, math.ceil((box[2] + margin) * w)), min(h, math.ceil((box[3] + margin) * h))
            mask[y1:y2, x1:x2] = 0
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    orb = cv2.ORB_create(nfeatures=VISUAL_THRESHOLDS["maximum_features"], edgeThreshold=15, fastThreshold=15)
    points, descriptors = orb.detectAndCompute(gray, mask)
    return {
        "frame": frame,
        "rgb": rgb,
        "points": points,
        "descriptors": descriptors,
        "width": width,
        "height": height,
        "sha256": digest,
    }


def _support(points: np.ndarray, width: int, height: int) -> tuple[float, int]:
    if len(points) == 0:
        return 0.0, 0
    normalized = points / np.array([width, height], dtype=float)
    area = float(np.prod(np.ptp(normalized, axis=0)))
    cells = {(min(2, int(x * 3)), min(2, int(y * 3))) for x, y in normalized}
    return area, len(cells)


def _registration(reference: dict, current: dict, index: int) -> tuple[dict, np.ndarray | None]:
    name = f"background_registration:{index}"
    old, new = reference["descriptors"], current["descriptors"]
    facts = {
        "reference_sha256": reference["sha256"],
        "candidate_sha256": current["sha256"],
        "reference_features": len(reference["points"]),
        "candidate_features": len(current["points"]),
        "coordinate_system": "candidate original pixel coordinates to reference original pixels",
    }

    def unknown(reason: str, **extra):
        return _clause(name, "unknown", reason, "registration_uncertain", **facts, **extra), None

    if old is None or new is None or min(len(old), len(new)) < VISUAL_THRESHOLDS["minimum_features"]:
        return unknown(
            "Keep textured background visible; too few background features establish registration."
        )
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    forward = matcher.knnMatch(new, old, k=2)
    backward = matcher.knnMatch(old, new, k=2)
    reverse = {
        row[0].queryIdx: row[0].trainIdx
        for row in backward
        if len(row) == 2 and row[0].distance < VISUAL_THRESHOLDS["match_ratio"] * row[1].distance
    }
    matches = [
        row[0]
        for row in forward
        if len(row) == 2
        and row[0].distance < VISUAL_THRESHOLDS["match_ratio"] * row[1].distance
        and reverse.get(row[0].trainIdx) == row[0].queryIdx
    ]
    facts["reciprocal_matches"] = len(matches)
    if len(matches) < VISUAL_THRESHOLDS["minimum_inliers"]:
        return unknown("Too few unambiguous background correspondences establish camera stability.")
    src = np.array([current["points"][m.queryIdx].pt for m in matches], dtype=np.float32)
    dst = np.array([reference["points"][m.trainIdx].pt for m in matches], dtype=np.float32)
    with _CV_LOCK:
        cv2.setRNGSeed(0)
        matrix, inliers = cv2.estimateAffinePartial2D(
            src,
            dst,
            method=cv2.RANSAC,
            ransacReprojThreshold=VISUAL_THRESHOLDS["ransac_pixels"],
            maxIters=VISUAL_THRESHOLDS["ransac_iterations"],
            confidence=0.99,
            refineIters=10,
        )
    if matrix is None or inliers is None or not np.isfinite(matrix).all():
        return unknown("Background transform could not be estimated reliably.")
    keep = inliers.ravel().astype(bool)
    count, ratio = int(keep.sum()), float(keep.mean())
    facts.update(inliers=count, inlier_ratio=ratio)
    if count < VISUAL_THRESHOLDS["minimum_inliers"] or ratio < VISUAL_THRESHOLDS["minimum_inlier_ratio"]:
        return unknown("Background matches disagree; restore the scene or submit for review.")
    old_h, old_w = reference["rgb"].shape[:2]
    new_h, new_w = current["rgb"].shape[:2]
    old_area, old_cells = _support(dst[keep], old_w, old_h)
    new_area, new_cells = _support(src[keep], new_w, new_h)
    facts.update(
        reference_support_area=old_area,
        candidate_support_area=new_area,
        reference_support_cells=old_cells,
        candidate_support_cells=new_cells,
    )
    if (
        min(old_area, new_area) < VISUAL_THRESHOLDS["minimum_support_area"]
        or min(old_cells, new_cells) < VISUAL_THRESHOLDS["minimum_support_cells"]
    ):
        return unknown("Background matches cover too little of the image to establish camera stability.")
    projected = src[keep] @ matrix[:, :2].T + matrix[:, 2]
    residual = float(np.median(np.linalg.norm(projected - dst[keep], axis=1)))
    if residual > VISUAL_THRESHOLDS["ransac_pixels"]:
        return unknown("Background registration error is too large.", median_residual_pixels=residual)
    # Normalize separately for images with different original resolutions.
    normalized = np.diag([1 / old_w, 1 / old_h, 1.0]) @ np.vstack([matrix, [0, 0, 1]])
    normalized = normalized @ np.diag([new_w, new_h, 1.0])
    original = np.diag([reference["width"], reference["height"], 1.0]) @ normalized
    original = original @ np.diag([1 / current["width"], 1 / current["height"], 1.0])
    corners = np.array([[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
    transformed = corners @ normalized.T
    displacement = float(np.max(np.linalg.norm(transformed[:, :2] - corners[:, :2], axis=1)) / math.sqrt(2))
    scale = math.sqrt(abs(float(np.linalg.det(normalized[:2, :2]))))
    rotation = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    facts.update(
        candidate_to_reference_pixels=original.tolist(),
        candidate_to_reference_normalized=normalized.tolist(),
        median_residual_pixels=residual,
        maximum_corner_displacement=displacement,
        scale=scale,
        rotation_degrees=rotation,
    )
    stable = (
        displacement <= VISUAL_THRESHOLDS["maximum_camera_corner_displacement"]
        and abs(scale - 1) <= VISUAL_THRESHOLDS["maximum_camera_scale_change"]
        and abs(rotation) <= VISUAL_THRESHOLDS["maximum_camera_rotation_degrees"]
    )
    return _clause(
        name,
        "pass" if stable else "fail",
        "Background registration is consistent with a fixed camera."
        if stable
        else "Restore the saved camera position, orientation, zoom, and crop.",
        "registration_stable" if stable else "camera_transform_changed",
        **facts,
    ), normalized


def _object(frame: dict, label: str) -> dict | None:
    matches = [
        d
        for d in frame["detections"]
        if d["label"] == label and d["confidence"] >= VISUAL_THRESHOLDS["ambiguity_confidence"]
    ]
    if len(matches) != 1 or matches[0]["confidence"] < VISUAL_THRESHOLDS["minimum_confidence"]:
        return None
    x1, y1, x2, y2 = matches[0]["box"]
    if min(x1, y1, 1 - x2, 1 - y2) < 0.01 or min(x2 - x1, y2 - y1) < 0.025:
        return None
    return matches[0]


def _crop(image: dict, detection: dict) -> np.ndarray:
    h, w = image["rgb"].shape[:2]
    x1, y1, x2, y2 = detection["box"]
    # Focus on the inner object box, limiting background changes as it moves.
    dx, dy = (x2 - x1) * 0.10, (y2 - y1) * 0.10
    left, top = int((x1 + dx) * w), int((y1 + dy) * h)
    right, bottom = max(left + 1, math.ceil((x2 - dx) * w)), max(top + 1, math.ceil((y2 - dy) * h))
    return cv2.resize(image["rgb"][top:bottom, left:right], (32, 32), interpolation=cv2.INTER_AREA)


def _appearance(reference: dict, current: dict, label: str, index: int) -> dict:
    old, new = _object(reference["frame"], label), _object(current["frame"], label)
    name = f"appearance_continuity:{label}:{index}"
    if old is None or new is None:
        return _clause(
            name,
            "unknown",
            f"Exactly one confident {label} is required for appearance comparison.",
            "appearance_uncertain",
            label=label,
        )
    left, right = _crop(reference, old), _crop(current, new)
    histograms = []
    for crop in (left, right):
        hist = cv2.calcHist([crop], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        histograms.append(cv2.normalize(hist, None, norm_type=cv2.NORM_L1))
    histogram = float(cv2.compareHist(*histograms, cv2.HISTCMP_BHATTACHARYYA))
    patch = float(np.abs(left.astype(float) - right.astype(float)).mean() / 255)
    if (
        histogram <= VISUAL_THRESHOLDS["appearance_histogram_pass"]
        and patch <= VISUAL_THRESHOLDS["appearance_patch_pass"]
    ):
        outcome = "pass"
    elif (
        histogram >= VISUAL_THRESHOLDS["appearance_histogram_fail"]
        and patch >= VISUAL_THRESHOLDS["appearance_patch_fail"]
    ):
        outcome = "fail"
    else:
        outcome = "unknown"
    return _clause(
        name,
        outcome,
        f"The {label}'s observed appearance is consistent; this does not prove physical identity."
        if outcome == "pass"
        else f"The {label}'s appearance changed or is inconclusive; "
        "restore the object and lighting, or request review.",
        "appearance_consistent"
        if outcome == "pass"
        else "appearance_changed"
        if outcome == "fail"
        else "appearance_uncertain",
        label=label,
        histogram_distance=histogram,
        patch_difference=patch,
        reference_sha256=reference["sha256"],
        candidate_sha256=current["sha256"],
    )


def _protected(reference: dict, current: dict, label: str, index: int, transform) -> dict:
    name = f"protected_object:{label}:{index}"
    old, new = _object(reference["frame"], label), _object(current["frame"], label)
    if old is None or new is None or transform is None:
        return _clause(
            name,
            "unknown",
            f"Keep one confidently visible {label} and a registrable background.",
            "protected_object_uncertain",
            label=label,
        )
    a, b = np.array(old["box"], dtype=float), np.array(new["box"], dtype=float)
    old_center, new_center = (a[:2] + a[2:]) / 2, (b[:2] + b[2:]) / 2
    transformed = transform @ np.r_[new_center, 1.0]
    delta = float(np.linalg.norm(transformed[:2] - old_center))
    corners = np.array([[b[0], b[1], 1], [b[2], b[1], 1], [b[2], b[3], 1], [b[0], b[3], 1]])
    adjusted = (corners @ transform.T)[:, :2]
    size = np.ptp(adjusted, axis=0)
    scale = float(np.max(np.abs(size / (a[2:] - a[:2]) - 1)))
    passed = (
        delta <= VISUAL_THRESHOLDS["protected_center_tolerance"]
        and scale <= VISUAL_THRESHOLDS["protected_scale_tolerance"]
    )
    return _clause(
        name,
        "pass" if passed else "fail",
        f"Keep the protected {label} in its saved position and at its saved size.",
        "protected_object_stable" if passed else "protected_object_moved",
        label=label,
        registered_center_delta=delta,
        registered_scale_delta=scale,
        reference_sha256=reference["sha256"],
        candidate_sha256=current["sha256"],
    )


def evaluate_visual_controls(
    frames: list[dict],
    baseline_frames: list[dict] | None = None,
    *,
    object_a: str = "bottle",
    object_b: str = "cup",
    protected_objects: Sequence[str] = (),
    media_reader: Callable[[str], bytes],
    version: str = VISUAL_VERSION,
    recipe: str = "spatial",
) -> dict:
    """Check every sampled frame against the original baseline's first image.

    Historical versions require distributed texture even for the first frame.
    Version 4 initializes that reference from verified original bytes and checks
    later frames with independent original-pixel background consistency, whether
    or not texture supports a camera transform. No client transform is trusted.
    """
    if version not in (
        VISUAL_VERSION,
        REGISTERED_APPEARANCE_VISUAL_VERSION,
        COMPOSITED_APPEARANCE_VISUAL_VERSION,
        PLAIN_BACKGROUND_VISUAL_VERSION,
        ROBUST_BACKGROUND_VISUAL_VERSION,
    ):
        raise InvalidVisualEvidence("Unknown visual verification version.")
    if version in (PLAIN_BACKGROUND_VISUAL_VERSION, ROBUST_BACKGROUND_VISUAL_VERSION) and recipe not in (
        "spatial",
        "temporal",
    ):
        raise InvalidVisualEvidence("Unknown visual recipe family.")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 48:
        raise InvalidVisualEvidence("Visual checks require one to 48 original images.")
    if len(protected_objects) > 3 or len(set(protected_objects)) != len(protected_objects):
        raise InvalidVisualEvidence("At most three distinct protected classes are supported.")
    if set(protected_objects) & {object_a, object_b}:
        raise InvalidVisualEvidence("Protected classes must be separate from the two role classes.")
    reference_frame = baseline_frames[0] if baseline_frames else frames[0]
    reference = _load(reference_frame, media_reader)
    checks = []
    sources = [{"asset_id": reference_frame["asset_id"], "sha256": reference["sha256"], "role": "reference"}]
    for index, frame in enumerate(frames):
        current = reference if frame is reference_frame else _load(frame, media_reader)
        sources.append(
            {
                "asset_id": frame["asset_id"],
                "sha256": current["sha256"],
                "role": "candidate",
                "frame_index": index,
            }
        )
        if version in (PLAIN_BACKGROUND_VISUAL_VERSION, ROBUST_BACKGROUND_VISUAL_VERSION):
            from .background import BACKGROUND_V2_VERSION, BACKGROUND_VERSION, compare_background

            registration, transform = compare_background(
                reference,
                current,
                index,
                moving_labels=(object_a, object_b) if recipe == "temporal" else (object_a,),
                controlled_labels=(object_a, object_b, *protected_objects),
                registration=_registration,
                version=BACKGROUND_V2_VERSION
                if version == ROBUST_BACKGROUND_VISUAL_VERSION
                else BACKGROUND_VERSION,
            )
        else:
            registration, transform = _registration(reference, current, index)
        checks.append(registration)
        for label in (object_a, object_b, *protected_objects):
            if version in (
                REGISTERED_APPEARANCE_VISUAL_VERSION,
                COMPOSITED_APPEARANCE_VISUAL_VERSION,
                PLAIN_BACKGROUND_VISUAL_VERSION,
                ROBUST_BACKGROUND_VISUAL_VERSION,
            ):
                from .appearance import APPEARANCE_VERSION, compare_appearance

                appearance_version = APPEARANCE_VERSION
                corrected_comparison = None
                if version in (
                    COMPOSITED_APPEARANCE_VISUAL_VERSION,
                    PLAIN_BACKGROUND_VISUAL_VERSION,
                    ROBUST_BACKGROUND_VISUAL_VERSION,
                ):
                    from .appearance import APPEARANCE_V3_VERSION

                    appearance_version = APPEARANCE_V3_VERSION
                    appearance_options = {}
                    if version in (PLAIN_BACKGROUND_VISUAL_VERSION, ROBUST_BACKGROUND_VISUAL_VERSION):
                        from .appearance import APPEARANCE_V4_VERSION

                        appearance_version = APPEARANCE_V4_VERSION
                        if version == ROBUST_BACKGROUND_VISUAL_VERSION:
                            from .appearance import APPEARANCE_V5_VERSION

                            appearance_version = APPEARANCE_V5_VERSION
                        appearance_options["background_labels"] = (object_a, object_b, *protected_objects)
                        appearance_options["allow_partial_overlap"] = recipe == "temporal" and label in (
                            object_a,
                            object_b,
                        )
                    corrected_comparison = compare_appearance(
                        reference,
                        current,
                        label,
                        index,
                        transform,
                        version=appearance_version,
                        **appearance_options,
                    )
                    if corrected_comparison["outcome"] == "pass":
                        # Transparent objects inherit the exposed background's
                        # colors. A measured compositing match supersedes raw
                        # color disagreement; unknown never receives this path.
                        checks.append(corrected_comparison)
                        continue
                strong_change = _appearance(reference, current, label, index)
                if strong_change["outcome"] == "fail":
                    strong_change["detail"] = (
                        f"The {label}'s appearance changed substantially; restore it and its lighting, "
                        "then retake the second photo."
                    )
                    strong_change["measurements"].update(
                        appearance_version=appearance_version,
                        method="strong_pixel_difference_guard",
                    )
                    if corrected_comparison is not None:
                        strong_change["measurements"]["corrected_comparison"] = corrected_comparison
                    checks.append(strong_change)
                elif version == REGISTERED_APPEARANCE_VISUAL_VERSION:
                    checks.append(compare_appearance(reference, current, label, index, transform))
                else:
                    checks.append(corrected_comparison)
            else:
                checks.append(_appearance(reference, current, label, index))
        for label in protected_objects:
            checks.append(_protected(reference, current, label, index, transform))
    outcomes = {check["outcome"] for check in checks}
    outcome = "fail" if "fail" in outcomes else "unknown" if "unknown" in outcomes else "pass"
    result = {
        "outcome": outcome,
        "checks": checks,
        "evidence": {
            "version": version,
            "sources": sources,
            "registration_model": "ORB reciprocal ratio matches + RANSAC 2-D similarity",
            "thresholds": dict(VISUAL_THRESHOLDS),
            "limitations": list(VISUAL_LIMITATIONS),
        },
    }
    if version in (
        REGISTERED_APPEARANCE_VISUAL_VERSION,
        COMPOSITED_APPEARANCE_VISUAL_VERSION,
        PLAIN_BACKGROUND_VISUAL_VERSION,
        ROBUST_BACKGROUND_VISUAL_VERSION,
    ):
        if version == REGISTERED_APPEARANCE_VISUAL_VERSION:
            from .appearance import APPEARANCE_METHOD, APPEARANCE_THRESHOLDS, APPEARANCE_VERSION
        elif version == COMPOSITED_APPEARANCE_VISUAL_VERSION:
            from .appearance import APPEARANCE_V3_METHOD as APPEARANCE_METHOD
            from .appearance import APPEARANCE_V3_THRESHOLDS as APPEARANCE_THRESHOLDS
            from .appearance import APPEARANCE_V3_VERSION as APPEARANCE_VERSION
        else:
            from .appearance import APPEARANCE_V4_METHOD as APPEARANCE_METHOD
            from .appearance import APPEARANCE_V4_THRESHOLDS as APPEARANCE_THRESHOLDS
            from .appearance import APPEARANCE_V4_VERSION as APPEARANCE_VERSION

        if version == ROBUST_BACKGROUND_VISUAL_VERSION:
            from .appearance import APPEARANCE_V5_METHOD as APPEARANCE_METHOD
            from .appearance import APPEARANCE_V5_THRESHOLDS as APPEARANCE_THRESHOLDS
            from .appearance import APPEARANCE_V5_VERSION as APPEARANCE_VERSION

        result["evidence"]["appearance"] = {
            "version": APPEARANCE_VERSION,
            "method": APPEARANCE_METHOD,
            "thresholds": dict(APPEARANCE_THRESHOLDS),
        }
    if version in (PLAIN_BACKGROUND_VISUAL_VERSION, ROBUST_BACKGROUND_VISUAL_VERSION):
        from .background import (
            BACKGROUND_LIMITATIONS,
            BACKGROUND_METHOD,
            BACKGROUND_THRESHOLDS,
            BACKGROUND_VERSION,
        )

        if version == ROBUST_BACKGROUND_VISUAL_VERSION:
            from .background import BACKGROUND_V2_LIMITATIONS as BACKGROUND_LIMITATIONS
            from .background import BACKGROUND_V2_METHOD as BACKGROUND_METHOD
            from .background import BACKGROUND_V2_THRESHOLDS as BACKGROUND_THRESHOLDS
            from .background import BACKGROUND_V2_VERSION as BACKGROUND_VERSION

        result["evidence"]["registration_model"] = BACKGROUND_METHOD
        result["evidence"]["background"] = {
            "version": BACKGROUND_VERSION,
            "method": BACKGROUND_METHOD,
            "thresholds": dict(BACKGROUND_THRESHOLDS),
        }
        result["evidence"]["limitations"] = list(BACKGROUND_LIMITATIONS) + list(VISUAL_LIMITATIONS[1:])
    return result
