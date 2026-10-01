"""Observable control-region consistency, including genuinely plain scenes.

Texture is required to estimate a camera transform, not to compare unchanged
pixels. A direct comparison provides weaker, accurately named evidence when a
transform is not observable. It does not establish physical camera pose.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from types import MappingProxyType

import cv2
import numpy as np

BACKGROUND_VERSION = "counterpart-background/1.0.0"
BACKGROUND_METHOD = (
    "permitted-mover ORB masks; independently verified role masks for broad photometric "
    "background consistency with bounded smooth illumination, contrast-normalized edges, "
    "and localized-change guards"
)
BACKGROUND_THRESHOLDS = MappingProxyType(
    {
        "comparison_maximum_side": 320,
        "moving_mask_margin": 0.03,
        "minimum_detection_confidence": 0.35,
        "minimum_control_pixels": 1024,
        "minimum_control_fraction": 0.25,
        "minimum_control_cells": 6,
        "minimum_cell_control_fraction": 0.10,
        "comparison_blur_sigma": 0.8,
        "maximum_illumination_offset": 32.0,
        "local_illumination_sigma_pixels": 6.0,
        "maximum_local_illumination_offset": 64.0,
        "edge_low_threshold": 20.0,
        "edge_high_threshold": 45.0,
        "edge_contrast_clip_limit": 2.0,
        "edge_contrast_grid_size": 8,
        "edge_match_distance_pixels": 3.0,
        "minimum_global_edges": 32,
        "maximum_unmatched_edge_fraction": 0.12,
        "minimum_component_edges": 8,
        "component_edge_padding_pixels": 4,
        "maximum_component_chromaticity_difference": 0.04,
        "chromaticity_pseudocount": 8.0,
        "changed_pixel_threshold": 24.0,
        "maximum_mean_residual": 6.0,
        "maximum_changed_fraction": 0.025,
        "maximum_cell_changed_fraction": 0.15,
        "maximum_changed_component_fraction": 0.004,
        "minimum_changed_component_pixels": 24,
        "maximum_direct_translation_pixels": 3,
        "direct_translation_inner_pixels": 1,
        "component_opening_pixels": 3,
    }
)
BACKGROUND_LIMITATIONS = [
    "Consistent control-region pixels do not establish camera pose on a textureless scene; "
    "physically different scenes that produce indistinguishable pixels cannot be distinguished.",
    "Named object regions are checked separately and excluded from background pixels. "
    "Changes hidden there, subthreshold changes, "
    "and visually equivalent replacements may escape comparison.",
    "A bounded global color offset accommodates exposure and white-balance drift; a uniform "
    "scene-color change within that bound is observationally ambiguous with illumination.",
    "Bounded smooth achromatic illumination changes accommodate cast shadows; equally smooth "
    "achromatic scene changes cannot always be distinguished from those lighting changes.",
]


def _mask(image: dict, shape: tuple[int, int], moving_labels: Sequence[str]) -> np.ndarray:
    h, w = shape
    mask = np.ones((h, w), np.uint8)
    margin = BACKGROUND_THRESHOLDS["moving_mask_margin"]
    for detection in image["frame"]["detections"]:
        if (
            detection["label"] not in moving_labels
            or detection["confidence"] < BACKGROUND_THRESHOLDS["minimum_detection_confidence"]
        ):
            continue
        x1, y1, x2, y2 = detection["box"]
        mask[
            max(0, math.floor((y1 - margin) * h)) : min(h, math.ceil((y2 + margin) * h)),
            max(0, math.floor((x1 - margin) * w)) : min(w, math.ceil((x2 + margin) * w)),
        ] = 0
    return mask


def _features(image: dict, moving_labels: Sequence[str]) -> dict:
    """Other detected furniture remains control evidence, never a free mask."""
    orb = cv2.ORB_create(nfeatures=900, edgeThreshold=15, fastThreshold=15)
    points, descriptors = orb.detectAndCompute(
        cv2.cvtColor(image["rgb"], cv2.COLOR_RGB2GRAY),
        _mask(image, image["rgb"].shape[:2], moving_labels) * 255,
    )
    return {**image, "points": points, "descriptors": descriptors}


def compare_background(
    reference: dict,
    current: dict,
    index: int,
    *,
    moving_labels: Sequence[str],
    registration: Callable,
    controlled_labels: Sequence[str] | None = None,
) -> tuple[dict, np.ndarray | None]:
    """Measure immutable pixels; never convert registration uncertainty to a pass.

    An uncertain transform instead triggers an independent direct consistency test.
    Known excessive camera motion cannot be compensated away by that alternative.
    """
    p = BACKGROUND_THRESHOLDS
    controlled_labels = moving_labels if controlled_labels is None else controlled_labels
    facts = {
        "background_version": BACKGROUND_VERSION,
        "method": BACKGROUND_METHOD,
        "reference_sha256": reference["sha256"],
        "candidate_sha256": current["sha256"],
        "masked_moving_labels": list(moving_labels),
        "separately_checked_object_labels": list(controlled_labels),
        "camera_pose_established": False,
    }

    def result(outcome, detail, code, transform=None):
        return {
            "name": f"background_registration:{index}",
            "outcome": outcome,
            "passed": outcome == "pass",
            "detail": detail,
            "code": code,
            "measurements": facts,
        }, transform

    if (
        reference["sha256"] == current["sha256"]
        and reference["width"] == current["width"]
        and reference["height"] == current["height"]
    ):
        facts.update(comparison="reference_initialized", candidate_to_reference_normalized=np.eye(3).tolist())
        return result(
            "pass",
            "Original image saved as the background reference.",
            "background_reference_initialized",
            np.eye(3),
        )

    old_h, old_w = reference["rgb"].shape[:2]
    new_h, new_w = current["rgb"].shape[:2]
    if abs(old_w / old_h - new_w / new_h) > 0.01:
        return result(
            "fail", "Restore the saved camera framing and aspect ratio.", "camera_transform_changed"
        )

    feature_result, measured_transform = registration(
        _features(reference, moving_labels), _features(current, moving_labels), index
    )
    facts["feature_registration"] = feature_result
    if feature_result["outcome"] == "fail":
        return result("fail", feature_result["detail"], feature_result["code"])
    registered = feature_result["outcome"] == "pass" and measured_transform is not None
    transform = measured_transform if registered else np.eye(3)
    facts["comparison"] = "registered_control_pixels" if registered else "direct_control_pixels"
    side = p["comparison_maximum_side"]
    scale = min(1.0, side / max(old_h, old_w))
    h, w = max(1, round(old_h * scale)), max(1, round(old_w * scale))
    old = cv2.resize(reference["rgb"], (w, h), interpolation=cv2.INTER_AREA)
    raw_new = cv2.resize(current["rgb"], (w, h), interpolation=cv2.INTER_AREA)
    old_mask = _mask(reference, (h, w), controlled_labels)
    new_mask = _mask(current, (h, w), controlled_labels)
    old = cv2.GaussianBlur(old.astype(np.float32), (0, 0), p["comparison_blur_sigma"])
    raw_new = cv2.GaussianBlur(raw_new.astype(np.float32), (0, 0), p["comparison_blur_sigma"])

    def compare(candidate_transform):
        pixels = np.diag([w, h, 1.0]) @ candidate_transform @ np.diag([1 / w, 1 / h, 1.0])
        new = cv2.warpAffine(raw_new, pixels[:2], (w, h), flags=cv2.INTER_LINEAR)
        valid = cv2.warpAffine(new_mask, pixels[:2], (w, h), flags=cv2.INTER_NEAREST).astype(bool)
        valid &= old_mask.astype(bool)
        # Borders affected by resampling are not evidence.
        valid[:3] = valid[-3:] = False
        valid[:, :3] = valid[:, -3:] = False
        count = int(valid.sum())
        cells = []
        for row in range(3):
            for col in range(3):
                region = np.s_[row * h // 3 : (row + 1) * h // 3, col * w // 3 : (col + 1) * w // 3]
                selected = valid[region]
                if selected.mean() >= p["minimum_cell_control_fraction"]:
                    cells.append((region, selected))
        coverage = count / (w * h)
        measurements = {"control_pixels": count, "control_fraction": coverage, "control_cells": len(cells)}
        if (
            count < p["minimum_control_pixels"]
            or coverage < p["minimum_control_fraction"]
            or len(cells) < p["minimum_control_cells"]
        ):
            return False, "coverage", float("inf"), measurements
        offset = np.median((new - old)[valid], axis=0)
        difference = new - old - offset
        # Cast shadows and autoexposure affect broad intensity without replacing
        # texture or color. Estimate only a smooth scalar intensity field: unlike
        # fitting RGB locally, this cannot erase a newly colored object. Its
        # bounded spatial bandwidth leaves sharp new boundaries in the residual.
        weight = valid.astype(np.float32)
        sigma = p["local_illumination_sigma_pixels"]
        scalar = difference.mean(axis=2)
        smooth = cv2.GaussianBlur(scalar * weight, (0, 0), sigma)
        smooth /= np.maximum(cv2.GaussianBlur(weight, (0, 0), sigma), 1e-6)
        smooth = np.clip(
            smooth, -p["maximum_local_illumination_offset"], p["maximum_local_illumination_offset"]
        )
        residual = np.max(np.abs(difference - smooth[:, :, None]), axis=2)
        changed = ((residual > p["changed_pixel_threshold"]) & valid).astype(np.uint8)
        kernel = np.ones((p["component_opening_pixels"], p["component_opening_pixels"]), np.uint8)
        substantial = cv2.morphologyEx(changed, cv2.MORPH_OPEN, kernel)
        edge_valid = cv2.erode(valid.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        edge_maps = []
        for rgb in (old, new):
            gray = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
            grid = p["edge_contrast_grid_size"]
            gray = cv2.createCLAHE(clipLimit=p["edge_contrast_clip_limit"], tileGridSize=(grid, grid)).apply(
                gray
            )
            edge_maps.append(
                (cv2.Canny(gray, p["edge_low_threshold"], p["edge_high_threshold"]) > 0) & edge_valid
            )
        unmatched = []
        for source, target in ((edge_maps[0], edge_maps[1]), (edge_maps[1], edge_maps[0])):
            distance = cv2.distanceTransform((~target).astype(np.uint8), cv2.DIST_L2, 3)
            unmatched.append(source & (distance > p["edge_match_distance_pixels"]))
        edge_counts = [int(edges.sum()) for edges in edge_maps]
        edge_fractions = [
            int(bad.sum()) / max(1, count) for bad, count in zip(unmatched, edge_counts, strict=True)
        ]
        structure_failed = any(
            count >= p["minimum_global_edges"] and fraction > p["maximum_unmatched_edge_fraction"]
            for count, fraction in zip(edge_counts, edge_fractions, strict=True)
        )
        component_count, component_labels, components, _ = cv2.connectedComponentsWithStats(
            substantial, connectivity=8
        )
        photometric_components = []
        component_checks = []
        # A strong intensity change may still be the same textured surface under
        # a cast shadow. Require independently retained local edges in BOTH views
        # and matching chromaticity before treating such a component as lighting.
        # Flat occluders/new colored patches cannot obtain this evidence.
        pseudocount = p["chromaticity_pseudocount"]
        a_color, b_color = old + pseudocount, np.clip(new - offset, 0, 255) + pseudocount
        chromaticity = np.max(
            np.abs(
                a_color / a_color.sum(axis=2, keepdims=True) - b_color / b_color.sum(axis=2, keepdims=True)
            ),
            axis=2,
        )
        padding = p["component_edge_padding_pixels"]
        for component in range(1, component_count):
            x, y, cw, ch, size = components[component]
            if size < p["minimum_changed_component_pixels"]:
                continue
            region = np.s_[
                max(0, y - padding) : min(h, y + ch + padding), max(0, x - padding) : min(w, x + cw + padding)
            ]
            counts = [int(edges[region].sum()) for edges in edge_maps]
            fractions = [
                int(bad[region].sum()) / max(1, count) for bad, count in zip(unmatched, counts, strict=True)
            ]
            select = component_labels == component
            color_error = float(np.percentile(chromaticity[select], 95))
            component_checks.append(
                {
                    "pixels": int(size),
                    "box": [int(x), int(y), int(cw), int(ch)],
                    "edge_counts": counts,
                    "unmatched_edge_fractions": fractions,
                    "chromaticity_difference": color_error,
                }
            )
            if (
                min(counts) >= p["minimum_component_edges"]
                and max(fractions) <= p["maximum_unmatched_edge_fraction"]
                and color_error <= p["maximum_component_chromaticity_difference"]
            ):
                substantial[select] = 0
                photometric_components.append(
                    {
                        "pixels": int(size),
                        "edge_counts": counts,
                        "unmatched_edge_fractions": fractions,
                        "chromaticity_difference": color_error,
                    }
                )
        _, _, components, _ = cv2.connectedComponentsWithStats(substantial, connectivity=8)
        largest = int(components[1:, cv2.CC_STAT_AREA].max()) if len(components) > 1 else 0
        mean = float(residual[valid].mean())
        changed_fraction = float(changed.sum() / count)
        local = max(float(changed[region][selected].mean()) for region, selected in cells)
        measurements.update(
            illumination_offset=offset.tolist(),
            maximum_local_illumination_offset=float(np.max(np.abs(smooth[valid]))),
            edge_counts=edge_counts,
            unmatched_edge_fractions=edge_fractions,
            photometric_components_supported_by_structure=photometric_components,
            photometric_component_checks=component_checks,
            mean_residual=mean,
            changed_fraction=changed_fraction,
            maximum_cell_changed_fraction=local,
            largest_changed_component_pixels=largest,
            largest_changed_component_fraction=largest / count,
        )
        if float(np.max(np.abs(offset))) > p["maximum_illumination_offset"]:
            return False, "lighting", mean, measurements
        allowed_component = max(
            p["minimum_changed_component_pixels"], p["maximum_changed_component_fraction"] * count
        )
        passed = (
            not structure_failed
            and mean <= p["maximum_mean_residual"]
            and changed_fraction <= p["maximum_changed_fraction"]
            and local <= p["maximum_cell_changed_fraction"]
            and largest <= allowed_component
        )
        return passed, "consistent" if passed else "changed", mean, measurements

    best = compare(transform)
    # Search only a small declared translation when there is no reliable ORB fit.
    # The direct pixel test must still independently pass after that bounded move.
    if not registered and not best[0] and best[1] == "changed":
        outer, inner = p["maximum_direct_translation_pixels"], p["direct_translation_inner_pixels"]
        for dy in (-outer, -inner, 0, inner, outer):
            for dx in (-outer, -inner, 0, inner, outer):
                if dx == dy == 0:
                    continue
                proposed = np.array([[1.0, 0.0, dx / w], [0.0, 1.0, dy / h], [0.0, 0.0, 1.0]])
                candidate = compare(proposed)
                if candidate[0] or (not best[0] and candidate[2] < best[2]):
                    best, transform = candidate, proposed
                if candidate[0]:
                    break
            if best[0]:
                break
    facts.update(best[3], candidate_to_reference_normalized=transform.tolist())
    if best[0]:
        return result(
            "pass",
            "The visible background and control regions remain consistent.",
            "background_consistent",
            transform,
        )
    if best[1] == "coverage":
        return result(
            "unknown", "Keep more of the scene visible outside the moving objects.", "registration_uncertain"
        )
    if best[1] == "lighting":
        return result(
            "unknown", "Restore the original lighting before retaking the image.", "registration_uncertain"
        )
    return result(
        "fail",
        "The scene outside the moving object changed; restore the background and camera, then retake.",
        "background_changed",
    )
