"""Conservative, deterministic checks over recorded detector observations.

This is a verifier of named image-plane conditions, not ground truth. It never
infers absent observations or interprets detector confidence as certainty.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

VERIFIER_VERSION = "counterpart-verifier/1.0.0"
THRESHOLDS = {
    "minimum_confidence": 0.60,
    "ambiguity_confidence": 0.35,
    "image_border_inset": 0.01,
    "minimum_object_dimension": 0.025,
    "relation_center_separation": 0.18,
    "relation_box_gap": 0.02,
    "anchor_center_tolerance": 0.035,
    "anchor_scale_tolerance": 0.15,
    "moving_scale_tolerance": 0.20,
    "spatial_vertical_tolerance": 0.05,
    "aspect_ratio_tolerance": 0.02,
    "temporal_min_frames": 8,
    "temporal_max_frames": 48,
    "temporal_max_duration_ms": 12000,
    "temporal_max_gap_ms": 600,
    "temporal_min_stable_ms": 150,
    "temporal_stable_position_tolerance": 0.035,
    "temporal_start_max_x": 0.42,
    "temporal_target_min_x": 0.62,
    "temporal_boundary_guard": 0.025,
    "temporal_min_lane_separation": 0.20,
    "temporal_lane_tolerance": 0.055,
    "temporal_pair_position_tolerance": 0.05,
    "temporal_min_order_margin_ms": 150,
}
LIMITATIONS = [
    "Checks use detector observations, which can be wrong or miss objects.",
    "Relations are measured in the image plane, not physical 3-D space.",
    "Object positions and sizes check named controls; they cannot prove that the camera, "
    "lighting, background, or every other scene detail stayed unchanged.",
    "Matching a class across captures does not establish the identity of a physical object.",
]


class _Rejected(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def _finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _reject(code: str, message: str) -> None:
    raise _Rejected(code, message)


def _check(checks: list[dict], name: str, passed: bool, detail: str, code: str) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})
    if not passed:
        _reject(code, detail)


def _parse_frames(frames: Any, labels: tuple[str, str]) -> list[dict]:
    if not isinstance(frames, list) or not frames:
        _reject("invalid_frames", "Supply recorded image observations.")
    observations = []
    for frame in frames:
        if not isinstance(frame, dict):
            _reject("invalid_frames", "Every observation must be an image record.")
        width, height, timestamp = frame.get("width"), frame.get("height"), frame.get("t_ms")
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in (width, height)):
            _reject("invalid_dimensions", "Image dimensions must be positive integers.")
        if not _finite_number(timestamp) or timestamp < 0:
            _reject("invalid_timestamps", "Observation timestamps must be finite, nonnegative milliseconds.")
        detections = frame.get("detections")
        if not isinstance(detections, list):
            _reject("invalid_detections", "Recorded detector observations are required.")
        candidates: dict[str, list] = {label: [] for label in labels}
        for detection in detections:
            if not isinstance(detection, dict):
                _reject("invalid_detections", "A detection record is malformed.")
            label = detection.get("label")
            confidence = detection.get("confidence")
            box = detection.get("box")
            if not isinstance(label, str) or not label:
                _reject("invalid_detections", "Detection labels must be nonempty strings.")
            if not _finite_number(confidence) or not 0 <= confidence <= 1:
                _reject("invalid_detections", "Detection confidence must be finite and between zero and one.")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                _reject("invalid_geometry", "Each detection must include a normalized xyxy box.")
            if not all(_finite_number(v) and 0 <= v <= 1 for v in box):
                _reject("invalid_geometry", "Detection coordinates must be finite and within the image.")
            x1, y1, x2, y2 = box
            if x2 <= x1 or y2 <= y1:
                _reject("invalid_geometry", "Detection boxes must have positive width and height.")
            if label in candidates and confidence >= THRESHOLDS["ambiguity_confidence"]:
                candidates[label].append(
                    {
                        "confidence": float(confidence),
                        "box": list(box),
                        "center": [(x1 + x2) / 2, (y1 + y2) / 2],
                        "size": [x2 - x1, y2 - y1],
                    }
                )
        objects = {}
        for label, matches in candidates.items():
            if not matches:
                _reject("missing_object", f"Keep one {label} fully visible in every saved observation.")
            if len(matches) != 1:
                _reject(
                    "ambiguous_object",
                    f"Show exactly one {label}; multiple plausible detections are ambiguous.",
                )
            if matches[0]["confidence"] < THRESHOLDS["minimum_confidence"]:
                _reject(
                    "low_confidence", f"The {label} detection is uncertain. Improve visibility and try again."
                )
            box = matches[0]["box"]
            inset = THRESHOLDS["image_border_inset"]
            if box[0] < inset or box[1] < inset or box[2] > 1 - inset or box[3] > 1 - inset:
                _reject("object_near_border", f"Keep the whole {label} visible, away from the image edges.")
            if min(matches[0]["size"]) < THRESHOLDS["minimum_object_dimension"]:
                _reject("object_too_small", f"Move closer so the {label} is clearly visible.")
            objects[label] = matches[0]
        observations.append(
            {
                "asset_id": frame.get("asset_id"),
                "t_ms": float(timestamp),
                "width": width,
                "height": height,
                "objects": objects,
            }
        )
    return observations


def _scale_difference(left: dict, right: dict) -> float:
    return max(abs(x / y - 1) for x, y in zip(left["size"], right["size"], strict=True))


def _distance(left: list[float], right: list[float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _baseline_observations(baseline: Any, recipe: str, labels: tuple[str, str]) -> list[dict]:
    if not isinstance(baseline, dict) or baseline.get("accepted") is not True:
        _reject("baseline_required", "Record an accepted first capture before checking its counterpart.")
    evidence = baseline.get("evidence")
    if (
        not isinstance(evidence, dict)
        or evidence.get("recipe") != recipe
        or evidence.get("slot") != "baseline"
    ):
        _reject("baseline_required", "The reference must be an accepted baseline for this recipe.")
    if evidence.get("object_a") != labels[0] or evidence.get("object_b") != labels[1]:
        _reject("baseline_required", "The reference must use the same object classes.")
    observations = _parse_frames(baseline.get("frames"), labels)
    if recipe == "spatial" and (len(observations) != 1 or _relation(observations[0], labels) != "left"):
        _reject("baseline_required", "The reference image must establish the required initial relation.")
    return observations


def _check_aspect(observations: list[dict], reference: dict, checks: list[dict]) -> None:
    ratio = reference["width"] / reference["height"]
    difference = max(abs((o["width"] / o["height"]) / ratio - 1) for o in observations)
    _check(
        checks,
        "image_aspect",
        difference <= THRESHOLDS["aspect_ratio_tolerance"],
        "Keep the same image orientation and crop.",
        "image_geometry_changed",
    )


def _spatial_controls(current: dict, reference: dict, labels: tuple[str, str], checks: list[dict]) -> dict:
    moving, anchor = labels
    old, new = reference["objects"], current["objects"]
    anchor_delta = _distance(new[anchor]["center"], old[anchor]["center"])
    anchor_scale = _scale_difference(new[anchor], old[anchor])
    moving_scale = _scale_difference(new[moving], old[moving])
    moving_y = abs(new[moving]["center"][1] - old[moving]["center"][1])
    _check(
        checks,
        "reference_position",
        anchor_delta <= THRESHOLDS["anchor_center_tolerance"],
        f"Keep the {anchor} and camera in the reference position.",
        "reference_moved",
    )
    _check(
        checks,
        "reference_scale",
        anchor_scale <= THRESHOLDS["anchor_scale_tolerance"],
        f"Keep the {anchor} the same apparent size; restore the camera distance.",
        "reference_scale_changed",
    )
    _check(
        checks,
        "moving_object_scale",
        moving_scale <= THRESHOLDS["moving_scale_tolerance"],
        f"Keep the {moving} approximately the same distance from the camera.",
        "object_scale_changed",
    )
    _check(
        checks,
        "moving_object_lane",
        moving_y <= THRESHOLDS["spatial_vertical_tolerance"],
        f"Move the {moving} sideways while keeping its vertical image position.",
        "object_lane_changed",
    )
    return {
        "anchor_center_delta": anchor_delta,
        "anchor_scale_delta": anchor_scale,
        "moving_scale_delta": moving_scale,
        "moving_vertical_delta": moving_y,
    }


def _relation(observation: dict, labels: tuple[str, str]) -> str:
    left, right = (observation["objects"][label] for label in labels)
    separation = THRESHOLDS["relation_center_separation"]
    gap = THRESHOLDS["relation_box_gap"]
    if right["center"][0] - left["center"][0] >= separation and right["box"][0] - left["box"][2] >= gap:
        return "left"
    if left["center"][0] - right["center"][0] >= separation and left["box"][0] - right["box"][2] >= gap:
        return "right"
    return "unclear"


def _temporal_controls(
    observations: list[dict], reference: dict, labels: tuple[str, str], checks: list[dict]
) -> dict:
    measured = {}
    for label in labels:
        old = reference["objects"][label]
        y_delta = max(abs(o["objects"][label]["center"][1] - old["center"][1]) for o in observations)
        scale_delta = max(_scale_difference(o["objects"][label], old) for o in observations)
        _check(
            checks,
            f"{label}_lane",
            y_delta <= THRESHOLDS["temporal_lane_tolerance"],
            f"Keep the {label} in its horizontal lane and keep the camera fixed.",
            "object_lane_changed",
        )
        _check(
            checks,
            f"{label}_scale",
            scale_delta <= THRESHOLDS["moving_scale_tolerance"],
            f"Keep the {label} the same apparent size throughout both clips.",
            "object_scale_changed",
        )
        measured[label] = {"vertical_delta": y_delta, "scale_delta": scale_delta}
    return measured


def _temporal_sequence(observations: list[dict], labels: tuple[str, str], checks: list[dict]) -> dict:
    count = len(observations)
    _check(
        checks,
        "sequence_length",
        THRESHOLDS["temporal_min_frames"] <= count <= THRESHOLDS["temporal_max_frames"],
        "Save between 8 and 48 observed frames for each sequence.",
        "sequence_length",
    )
    times = [o["t_ms"] for o in observations]
    gaps = [b - a for a, b in zip(times[:-1], times[1:], strict=True)]
    _check(
        checks,
        "ordered_observations",
        all(gap > 0 for gap in gaps),
        "Observation timestamps must strictly increase; duplicate or reversed frames cannot establish order.",
        "invalid_timestamps",
    )
    _check(
        checks,
        "observation_gaps",
        max(gaps) <= THRESHOLDS["temporal_max_gap_ms"],
        "Too much of the action was unobserved. Record again without interruption.",
        "observation_gap",
    )
    _check(
        checks,
        "sequence_duration",
        times[-1] - times[0] <= THRESHOLDS["temporal_max_duration_ms"],
        "Keep each sequence within 12 seconds.",
        "sequence_too_long",
    )
    stable_ms = THRESHOLDS["temporal_min_stable_ms"]
    _check(
        checks,
        "stable_observation_duration",
        gaps[0] >= stable_ms and gaps[-1] >= stable_ms,
        "Pause with both objects still at the start and end of the sequence.",
        "unstable_endpoints",
    )
    separation = min(
        abs(o["objects"][labels[0]]["center"][1] - o["objects"][labels[1]]["center"][1]) for o in observations
    )
    _check(
        checks,
        "separate_lanes",
        separation >= THRESHOLDS["temporal_min_lane_separation"],
        "Use separate horizontal lanes so the objects stay visibly distinguishable.",
        "ambiguous_lanes",
    )
    controls = _temporal_controls(observations, observations[0], labels, checks)
    intervals = {}
    boundary = THRESHOLDS["temporal_target_min_x"]
    guard = THRESHOLDS["temporal_boundary_guard"]
    for label in labels:
        points = [o["objects"][label]["center"] for o in observations]
        xs = [point[0] for point in points]
        _check(
            checks,
            f"{label}_initial_position",
            all(x <= THRESHOLDS["temporal_start_max_x"] for x in xs[:2]),
            f"Start the {label} to the left of the target band and pause before moving it.",
            "invalid_start",
        )
        _check(
            checks,
            f"{label}_final_position",
            all(x >= boundary + guard for x in xs[-2:]),
            f"Finish with the {label} clearly inside the target band and hold it there.",
            "invalid_finish",
        )
        position_tolerance = THRESHOLDS["temporal_stable_position_tolerance"]
        _check(
            checks,
            f"{label}_stable_endpoints",
            _distance(points[0], points[1]) <= position_tolerance
            and _distance(points[-2], points[-1]) <= position_tolerance,
            f"Hold the {label} still for the initial and final observations.",
            "unstable_endpoints",
        )
        entered = [x >= boundary for x in xs]
        first_crossing = entered.index(True)
        clearly_inside = [x >= boundary + guard for x in xs]
        entry = clearly_inside.index(True)
        last_clearly_outside = max(index for index in range(entry) if xs[index] <= boundary - guard)
        _check(
            checks,
            f"{label}_sustained_arrival",
            all(entered[first_crossing:])
            and all(clearly_inside[entry:])
            and entry < count - 1
            and times[entry + 1] - times[entry] >= stable_ms,
            f"Move the {label} into the target band once, then leave it there.",
            "unstable_arrival",
        )
        intervals[label] = {
            "after_ms": times[last_clearly_outside],
            "by_ms": times[entry],
            "first_inside_frame": entry,
            "last_outside_frame": last_clearly_outside,
            "sustained_observations": count - entry,
        }
    first, second = sorted(labels, key=lambda label: intervals[label]["by_ms"])
    margin = intervals[second]["after_ms"] - intervals[first]["by_ms"]
    _check(
        checks,
        "resolvable_arrival_order",
        margin >= THRESHOLDS["temporal_min_order_margin_ms"],
        "The arrivals were too close to establish their order. Leave a clear pause between them.",
        "ambiguous_order",
    )
    return {
        "arrival_intervals": intervals,
        "arrival_order": [first, second],
        "order_margin_ms": margin,
        "max_observation_gap_ms": max(gaps),
        "duration_ms": times[-1] - times[0],
        "controls": controls,
        "timing_semantics": "Each observed arrival is bounded by the last clearly outside sample and "
        "the first clearly inside sample, including a boundary guard against small localization errors. "
        "Exact arrival times and movement onset are unknown.",
    }


def verify(
    recipe: str,
    slot: str,
    frames: list[dict],
    baseline: dict | None = None,
    *,
    object_a: str = "bottle",
    object_b: str = "cup",
    interrupted: bool = False,
) -> dict:
    """Return a versioned decision; malformed or ambiguous evidence fails closed.

    ``baseline`` is an accepted baseline result with its original ``frames``.
    ``accepted`` for previews/recovery means only the observation check passed;
    neither slot is evidence that a pair is complete.
    """
    checks: list[dict] = []
    evidence: dict = {
        "recipe": recipe,
        "slot": slot,
        "object_a": object_a,
        "object_b": object_b,
        "thresholds": deepcopy(THRESHOLDS),
        "limitations": list(LIMITATIONS),
    }
    try:
        if recipe not in ("spatial", "temporal") or slot not in (
            "baseline",
            "counterpart",
            "recovery",
            "preview",
        ):
            _reject("invalid_request", "Choose a supported recipe and capture slot.")
        if not isinstance(object_a, str) or not isinstance(object_b, str) or not object_a or not object_b:
            _reject("invalid_classes", "Choose two named object classes.")
        if object_a == object_b:
            _reject(
                "invalid_classes",
                "Use two different object classes; same-class instance identity is unsupported.",
            )
        if interrupted:
            _reject(
                "capture_interrupted",
                "The capture was interrupted. Keep the saved baseline and record this attempt again.",
            )
        labels = (object_a, object_b)
        observations = _parse_frames(frames, labels)
        evidence["observations"] = observations
        checks.append(
            {
                "name": "visible_unambiguous_objects",
                "passed": True,
                "detail": "Both classes have one sufficiently confident detection in every observation.",
            }
        )
        reference = None
        if slot in ("counterpart", "recovery") or (slot == "preview" and baseline is not None):
            reference = _baseline_observations(baseline, recipe, labels)
            _check_aspect(observations, reference[0], checks)
        _check_aspect(observations, observations[0], checks)
        if recipe == "spatial" or slot in ("preview", "recovery"):
            _check(
                checks,
                "single_image",
                len(observations) == 1,
                "This check requires exactly one saved image.",
                "invalid_frame_count",
            )
        if recipe == "spatial":
            current = observations[0]
            relation = _relation(current, labels)
            evidence["relation"] = relation
            if reference is not None:
                evidence["controls"] = _spatial_controls(current, reference[0], labels, checks)
            if slot != "recovery":
                expected = "right" if slot == "counterpart" or (slot == "preview" and reference) else "left"
                _check(
                    checks,
                    "requested_relation",
                    relation == expected,
                    f"Place the {object_a} clearly to the {expected} of the {object_b}, "
                    "with visible space between them.",
                    "wrong_relation" if relation != "unclear" else "unclear_relation",
                )
            message = "The spatial relation and named controls passed."
        elif slot in ("preview", "recovery"):
            current = observations[0]
            if reference is not None:
                evidence["controls"] = _temporal_controls(observations, reference[0], labels, checks)
            lane_separation = abs(
                current["objects"][object_a]["center"][1] - current["objects"][object_b]["center"][1]
            )
            _check(
                checks,
                "separate_lanes",
                lane_separation >= THRESHOLDS["temporal_min_lane_separation"],
                "Place the objects in separate horizontal lanes.",
                "ambiguous_lanes",
            )
            if slot == "preview":
                _check(
                    checks,
                    "ready_to_start",
                    all(
                        o["center"][0] <= THRESHOLDS["temporal_start_max_x"]
                        for o in current["objects"].values()
                    ),
                    "Reset both objects left of the target band before recording.",
                    "invalid_start",
                )
            message = "The visible scene controls passed; record the full sequence to check arrival order."
        else:
            sequence = _temporal_sequence(observations, labels, checks)
            evidence.update(sequence)
            expected_order = list(labels if slot == "baseline" else reversed(labels))
            _check(
                checks,
                "requested_order",
                sequence["arrival_order"] == expected_order,
                f"Move the {expected_order[0]} into the band first, pause, "
                f"then move the {expected_order[1]}.",
                "wrong_order",
            )
            if reference is not None:
                # Revalidate the complete baseline evidence, rather than trusting a bare accepted flag.
                reference_checks: list[dict] = []
                reference_sequence = _temporal_sequence(reference, labels, reference_checks)
                if reference_sequence["arrival_order"] != list(labels):
                    _reject(
                        "baseline_required",
                        "The reference sequence does not establish the required first order.",
                    )
                evidence["pair_controls"] = _temporal_controls(observations, reference[0], labels, checks)
                endpoint_deltas = {}
                for label in labels:
                    start_delta = _distance(
                        observations[0]["objects"][label]["center"], reference[0]["objects"][label]["center"]
                    )
                    end_delta = _distance(
                        observations[-1]["objects"][label]["center"],
                        reference[-1]["objects"][label]["center"],
                    )
                    _check(
                        checks,
                        f"{label}_matching_endpoints",
                        max(start_delta, end_delta) <= THRESHOLDS["temporal_pair_position_tolerance"],
                        f"Match the {label}'s starting and ending positions to the first sequence.",
                        "endpoints_changed",
                    )
                    endpoint_deltas[label] = {"start_delta": start_delta, "end_delta": end_delta}
                evidence["endpoint_deltas"] = endpoint_deltas
            message = (
                "Both arrivals are sustained and their observed intervals establish the requested order."
            )
        evidence["can_complete_pair"] = slot == "counterpart"
        if slot == "recovery":
            message = (
                "The named scene controls match the saved baseline. Continue collecting your counterpart."
            )
        return {
            "accepted": True,
            "code": "ready" if slot == "preview" else "accepted",
            "message": message,
            "checks": checks,
            "evidence": evidence,
            "verifier_version": VERIFIER_VERSION,
        }
    except _Rejected as error:
        if not checks or checks[-1]["passed"]:
            checks.append({"name": error.code, "passed": False, "detail": error.message})
        evidence["can_complete_pair"] = False
        return {
            "accepted": False,
            "code": error.code,
            "message": error.message,
            "checks": checks,
            "evidence": evidence,
            "verifier_version": VERIFIER_VERSION,
        }
