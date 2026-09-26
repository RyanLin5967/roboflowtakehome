from copy import deepcopy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from counterpart.fixtures import generate
from counterpart.verification import THRESHOLDS, VERIFIER_VERSION, verify


def frames(recipe="spatial", scenario="baseline"):
    return [
        {k: v for k, v in frame.items() if k not in ("bytes", "provenance")}
        for frame in generate(recipe, scenario)
    ]


def baseline(recipe="spatial"):
    observations = frames(recipe)
    result = verify(recipe, "baseline", observations)
    assert result["accepted"], result
    return {**result, "frames": observations}


def move(box, *, x=0, y=0):
    return [box[0] + x, box[1] + y, box[2] + x, box[3] + y]


def test_spatial_pair_has_changed_relation_and_preserved_controls():
    reference = baseline()
    result = verify("spatial", "counterpart", frames(scenario="counterpart"), reference)
    assert result["accepted"]
    assert reference["evidence"]["relation"] == "left"
    assert result["evidence"]["relation"] == "right"
    assert result["evidence"]["controls"]["anchor_center_delta"] == 0
    assert result["evidence"]["can_complete_pair"]
    assert result["verifier_version"] == VERIFIER_VERSION


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("wrong_relation", "wrong_relation"),
        ("camera_moved", "reference_moved"),
        ("ambiguous", "ambiguous_object"),
    ],
)
def test_spatial_bad_attempts_request_specific_repair(scenario, code):
    result = verify("spatial", "counterpart", frames(scenario=scenario), baseline())
    assert not result["accepted"]
    assert result["code"] == code
    assert not result["evidence"]["can_complete_pair"]


def test_baseline_cannot_start_with_counterpart_relation():
    assert verify("spatial", "baseline", frames(scenario="counterpart"))["code"] == "wrong_relation"


def test_close_objects_do_not_establish_relation():
    observations = frames()
    observations[0]["detections"][0]["box"] = move(observations[0]["detections"][0]["box"], x=0.18)
    assert verify("spatial", "baseline", observations)["code"] == "unclear_relation"


@pytest.mark.parametrize("slot", ["counterpart", "recovery"])
@pytest.mark.parametrize("reference", [None, {}, {"accepted": False}, {"accepted": True, "evidence": {}}])
def test_missing_or_incompatible_reference_never_passes(slot, reference):
    assert verify("spatial", slot, frames(), reference)["code"] == "baseline_required"


def test_baseline_evidence_is_checked_not_only_accepted_flag():
    reference = baseline()
    reference["frames"] = frames(scenario="counterpart")
    assert (
        verify("spatial", "counterpart", frames(scenario="counterpart"), reference)["code"]
        == "baseline_required"
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("width", 0, "invalid_dimensions"),
        ("width", True, "invalid_dimensions"),
        ("height", 100.5, "invalid_dimensions"),
        ("t_ms", float("nan"), "invalid_timestamps"),
        ("t_ms", -1, "invalid_timestamps"),
        ("t_ms", True, "invalid_timestamps"),
        ("detections", None, "invalid_detections"),
        ("detections", [], "missing_object"),
    ],
)
def test_invalid_frame_fields_fail_closed(field, value, code):
    observations = frames()
    observations[0][field] = value
    assert verify("spatial", "baseline", observations)["code"] == code


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf"), -0.01, 1.01, True, "0.9"])
def test_nonfinite_or_malformed_confidence_never_passes(bad_value):
    observations = frames()
    observations[0]["detections"][0]["confidence"] = bad_value
    assert verify("spatial", "baseline", observations)["code"] == "invalid_detections"


@pytest.mark.parametrize(
    "box",
    [
        [0, 0, 0, 0],
        [0.5, 0, 0.4, 1],
        [0, 0, 1],
        [0, 0, float("nan"), 1],
        [-0.1, 0, 0.2, 0.3],
        [False, 0, 0.2, 0.3],
    ],
)
def test_invalid_boxes_never_pass(box):
    observations = frames()
    observations[0]["detections"][0]["box"] = box
    assert verify("spatial", "baseline", observations)["code"] == "invalid_geometry"


def test_small_or_cropped_objects_require_recapture():
    observations = frames()
    observations[0]["detections"][0]["box"] = [0.20, 0.51, 0.21, 0.52]
    assert verify("spatial", "baseline", observations)["code"] == "object_too_small"
    observations[0]["detections"][0]["box"] = [0, 0.3, 0.2, 0.7]
    assert verify("spatial", "baseline", observations)["code"] == "object_near_border"


def test_low_confidence_is_uncertainty_and_second_candidate_is_ambiguity():
    observations = frames()
    observations[0]["detections"][0]["confidence"] = 0.59
    assert verify("spatial", "baseline", observations)["code"] == "low_confidence"
    observations[0]["detections"][0]["confidence"] = 0.60
    assert verify("spatial", "baseline", observations)["accepted"]
    additional = deepcopy(observations[0]["detections"][0])
    additional["confidence"] = THRESHOLDS["ambiguity_confidence"]
    observations[0]["detections"].append(additional)
    assert verify("spatial", "baseline", observations)["code"] == "ambiguous_object"


def test_spatial_recovery_checks_controls_without_forcing_moving_object_side():
    for scenario in ("baseline", "counterpart"):
        result = verify("spatial", "recovery", frames(scenario=scenario), baseline())
        assert result["accepted"]
        assert not result["evidence"]["can_complete_pair"]
    assert (
        verify("spatial", "recovery", frames(scenario="camera_moved"), baseline())["code"]
        == "reference_moved"
    )


def test_preview_advises_current_stage_but_cannot_complete_pair():
    result = verify("spatial", "preview", frames())
    assert result["accepted"]
    assert not result["evidence"]["can_complete_pair"]
    assert verify("spatial", "preview", frames(), baseline())["code"] == "wrong_relation"
    assert verify("spatial", "preview", frames(scenario="counterpart"), baseline())["accepted"]


def test_matching_aspect_required_but_resolution_can_change():
    observations = frames(scenario="counterpart")
    observations[0]["width"] *= 2
    observations[0]["height"] *= 2
    assert verify("spatial", "counterpart", observations, baseline())["accepted"]
    observations[0]["height"] *= 2
    assert verify("spatial", "counterpart", observations, baseline())["code"] == "image_geometry_changed"


def test_temporal_pair_reverses_resolvable_arrivals_with_matching_endpoints():
    reference = baseline("temporal")
    result = verify("temporal", "counterpart", frames("temporal", "counterpart"), reference)
    assert result["accepted"], result
    assert reference["evidence"]["arrival_order"] == ["bottle", "cup"]
    assert result["evidence"]["arrival_order"] == ["cup", "bottle"]
    assert result["evidence"]["arrival_intervals"]["cup"]["after_ms"] == 800
    assert result["evidence"]["arrival_intervals"]["cup"]["by_ms"] == 1200
    assert result["evidence"]["order_margin_ms"] == 800
    assert result["evidence"]["endpoint_deltas"]["bottle"]["end_delta"] == 0
    assert "onset are unknown" in result["evidence"]["timing_semantics"]


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("wrong_relation", "wrong_order"),
        ("camera_moved", "object_lane_changed"),
        ("ambiguous", "ambiguous_object"),
    ],
)
def test_temporal_bad_attempts_never_pass(scenario, code):
    assert (
        verify("temporal", "counterpart", frames("temporal", scenario), baseline("temporal"))["code"] == code
    )


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_interrupted_capture_never_passes(recipe):
    assert verify(recipe, "baseline", frames(recipe), interrupted=True)["code"] == "capture_interrupted"


@pytest.mark.parametrize(
    "timestamp,code", [(0, "invalid_timestamps"), (-1, "invalid_timestamps"), (601, "observation_gap")]
)
def test_duplicate_reordered_or_sparse_temporal_observations_rejected(timestamp, code):
    observations = frames("temporal")
    if timestamp == 601:
        for frame in observations[1:]:
            frame["t_ms"] += 201
    else:
        observations[1]["t_ms"] = timestamp
    assert verify("temporal", "baseline", observations)["code"] == code


def test_missing_object_in_even_one_temporal_frame_rejects_clip():
    observations = frames("temporal")
    observations[4]["detections"].pop()
    assert verify("temporal", "baseline", observations)["code"] == "missing_object"


def test_arrivals_in_same_interval_are_ambiguous():
    observations = frames("temporal")
    for frame in observations:
        bottle, cup = frame["detections"]
        bottle_x = (bottle["box"][0] + bottle["box"][2]) / 2
        cup_x = (cup["box"][0] + cup["box"][2]) / 2
        cup["box"] = move(cup["box"], x=bottle_x - cup_x)
    assert verify("temporal", "baseline", observations)["code"] == "ambiguous_order"


def test_touching_arrival_intervals_do_not_prove_separation():
    observations = frames("temporal")
    for index in (4, 5):
        cup = observations[index]["detections"][1]
        cup["box"] = deepcopy(observations[6]["detections"][1]["box"])
    assert verify("temporal", "baseline", observations)["code"] == "ambiguous_order"


def test_leaving_target_after_entry_is_not_a_clean_arrival():
    observations = frames("temporal")
    observations[4]["detections"][0]["box"] = deepcopy(observations[2]["detections"][0]["box"])
    assert verify("temporal", "baseline", observations)["code"] == "unstable_arrival"


def test_near_boundary_frames_widen_arrival_interval():
    observations = frames("temporal")
    bottle = observations[3]["detections"][0]
    bottle["box"] = move(bottle["box"], x=0.625 - 0.74)
    result = verify("temporal", "baseline", observations)
    assert result["accepted"]
    assert result["evidence"]["arrival_intervals"]["bottle"]["after_ms"] == 800
    assert result["evidence"]["arrival_intervals"]["bottle"]["by_ms"] == 1600


def test_boundary_uncertainty_can_prevent_establishing_order():
    observations = frames("temporal")
    for index, x in ((3, 0.625), (4, 0.635)):
        bottle = observations[index]["detections"][0]
        bottle["box"] = move(bottle["box"], x=x - 0.74)
    assert verify("temporal", "baseline", observations)["code"] == "ambiguous_order"


def test_final_placement_right_on_boundary_is_uncertain():
    observations = frames("temporal")
    for frame in observations[-2:]:
        frame["detections"][0]["box"] = move(frame["detections"][0]["box"], x=0.63 - 0.74)
    assert verify("temporal", "baseline", observations)["code"] == "invalid_finish"


def test_mismatched_starting_positions_reject_otherwise_good_opposite_order():
    observations = frames("temporal", "counterpart")
    for frame in observations[:2]:
        frame["detections"][0]["box"] = move(frame["detections"][0]["box"], x=0.08)
    assert (
        verify("temporal", "counterpart", observations, baseline("temporal"))["code"] == "endpoints_changed"
    )


def test_temporal_recovery_permits_reset_but_checks_named_controls():
    reference = baseline("temporal")
    result = verify("temporal", "recovery", frames("temporal", "recovery"), reference)
    assert result["accepted"]
    assert not result["evidence"]["can_complete_pair"]
    # A single final-arrangement image is also a valid control observation,
    # but cannot establish sequence order or readiness for a new clip.
    final = frames("temporal")[-1:]
    assert verify("temporal", "recovery", final, reference)["accepted"]
    assert verify("temporal", "preview", final, reference)["code"] == "invalid_start"


def test_two_start_and_finish_observations_must_actually_be_stable():
    observations = frames("temporal")
    observations[1]["detections"][0]["box"] = move(observations[1]["detections"][0]["box"], x=0.09)
    assert verify("temporal", "baseline", observations)["code"] == "unstable_endpoints"
    observations = frames("temporal")
    for observation in observations[1:]:
        observation["t_ms"] -= 300
    assert verify("temporal", "baseline", observations)["code"] == "unstable_endpoints"


def test_same_class_identity_is_explicitly_unsupported():
    assert verify("spatial", "baseline", frames(), object_b="bottle")["code"] == "invalid_classes"


def test_verifier_never_mutates_recorded_evidence():
    observations, reference = frames("temporal", "counterpart"), baseline("temporal")
    before = deepcopy((observations, reference))
    verify("temporal", "counterpart", observations, reference)
    assert (observations, reference) == before


@given(st.floats(min_value=0.036, max_value=0.13, allow_nan=False, allow_infinity=False))
def test_property_anchor_movement_above_tolerance_never_accepted(shift):
    observations = frames(scenario="counterpart")
    observations[0]["detections"][1]["box"] = move(observations[0]["detections"][1]["box"], y=shift)
    assert not verify("spatial", "counterpart", observations, baseline())["accepted"]


@given(st.integers(min_value=601, max_value=100000))
def test_property_hidden_temporal_gap_never_accepted(gap):
    observations = frames("temporal")
    for frame in observations[5:]:
        frame["t_ms"] += gap - 400
    assert not verify("temporal", "baseline", observations)["accepted"]
