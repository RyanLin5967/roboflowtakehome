"""Strict appearance/version contracts; synthetic observations are not CV accuracy."""

import hashlib
import json
import math
from copy import deepcopy

import pytest

from counterpart import visual_checks
from counterpart.fixtures import generate
from counterpart.recipes import (
    RecipeContractError,
    assert_runtime_compatible,
    build_recipe_snapshot,
    recipe_catalogue,
    validate_recipe_snapshot,
    version_for_mode,
)
from counterpart.verification import baseline_authorization_binding, verify


def frames(recipe="spatial", scenario="baseline"):
    return [
        {k: v for k, v in frame.items() if k not in ("bytes", "provenance")}
        for frame in generate(recipe, scenario)
    ]


def clause(name="appearance_continuity:bottle:0", outcome="unknown", code="appearance_uncertain"):
    return {
        "name": name,
        "outcome": outcome,
        "passed": outcome == "pass",
        "code": code,
        "detail": "Original appearance observation is inconclusive.",
        "measurements": {"histogram_distance": 0.7, "patch_difference": 0.15},
    }


def install_visual(monkeypatch, checks):
    original = deepcopy(checks)
    monkeypatch.setattr(
        visual_checks,
        "evaluate_visual_controls",
        lambda *a, **kw: {"checks": deepcopy(original), "evidence": {"version": "test-composition"}},
    )


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
@pytest.mark.parametrize("slot", ["baseline", "counterpart", "recovery", "preview"])
@pytest.mark.parametrize("version", ["2.1.0", "2.2.0", "2.3.0"])
def test_appearance_unknown_blocks_every_capture_slot_and_original_measurement_is_preserved(
    monkeypatch, recipe, slot, version
):
    snapshot = build_recipe_snapshot(recipe, recipe_version=version)
    first = frames(recipe)
    install_visual(monkeypatch, [])
    baseline = {
        **verify(recipe, "baseline", first, recipe_snapshot=snapshot, media_reader=lambda _: b""),
        "frames": first,
    }
    assert baseline["accepted"]
    original = clause()
    install_visual(monkeypatch, [original])
    observations = frames(recipe, "counterpart" if slot == "counterpart" else "baseline")
    if slot in ("preview", "recovery"):
        observations = frames(recipe, "recovery")
    result = verify(
        recipe,
        slot,
        observations,
        baseline if slot in ("counterpart", "recovery") else None,
        recipe_snapshot=snapshot,
        media_reader=lambda _: b"",
    )
    assert not result["accepted"] and result["outcome"] == "unknown", result
    assert result["verifier_version"] == f"counterpart-verifier/{version}"
    assert "warnings" not in result
    assert original in result["checks"]
    assert result["evidence"]["can_complete_pair"] is False
    assert "human_review_required" not in result
    assert "inconclusive" in result["message"]
    with pytest.raises(ValueError):
        baseline_authorization_binding(snapshot, {**result, "frames": observations})


@pytest.mark.parametrize(
    ("name", "code", "outcome"),
    [
        ("appearance_continuity:bottle:0", "appearance_changed", "fail"),
        ("background_registration:0", "registration_uncertain", "unknown"),
        ("background_registration:0", "camera_moved", "fail"),
        ("protected_object:book:0", "protected_object_uncertain", "unknown"),
        ("protected_object:book:0", "protected_object_moved", "fail"),
        ("unrecognized_check", "appearance_uncertain", "unknown"),
        ("appearance_continuity:bottle:0", "unrecognized_code", "unknown"),
    ],
)
@pytest.mark.parametrize("version", ["2.1.0", "2.2.0", "2.3.0"])
def test_every_unknown_or_failed_control_blocks(monkeypatch, name, code, outcome, version):
    blocked = clause(name, outcome, code)
    install_visual(monkeypatch, [blocked])
    result = verify(
        "spatial",
        "baseline",
        frames(),
        recipe_snapshot=build_recipe_snapshot("spatial", recipe_version=version),
        media_reader=lambda _: b"",
    )
    assert not result["accepted"] and result["outcome"] == outcome
    assert result["code"] == code
    assert "warnings" not in result
    assert blocked in result["checks"]


@pytest.mark.parametrize("scenario", ["wrong_relation", "camera_moved", "ambiguous"])
@pytest.mark.parametrize("version", ["2.1.0", "2.2.0", "2.3.0"])
def test_unknown_appearance_does_not_hide_actual_geometry_or_detection(monkeypatch, scenario, version):
    install_visual(monkeypatch, [])
    snapshot = build_recipe_snapshot("spatial", recipe_version=version)
    first = frames()
    baseline = {
        **verify("spatial", "baseline", first, recipe_snapshot=snapshot, media_reader=lambda _: b""),
        "frames": first,
    }
    install_visual(monkeypatch, [clause()])
    result = verify(
        "spatial",
        "counterpart",
        frames(scenario=scenario),
        baseline,
        recipe_snapshot=snapshot,
        media_reader=lambda _: b"",
    )
    assert not result["accepted"]
    assert result["outcome"] in ("unknown", "fail")
    assert result["code"] != "appearance_uncertain"
    assert "warnings" not in result


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0", "2.2.0", "2.3.0"])
def test_appearance_pass_stays_pass_and_unknown_stays_blocking(monkeypatch, version):
    snapshot = build_recipe_snapshot("spatial", recipe_version=version)
    for outcome, code in (("pass", "appearance_consistent"), ("unknown", "appearance_uncertain")):
        original = clause(outcome=outcome, code=code)
        install_visual(monkeypatch, [original])
        result = verify("spatial", "baseline", frames(), recipe_snapshot=snapshot, media_reader=lambda _: b"")
        assert result["accepted"] is not (version != "1.0.0" and outcome == "unknown")
        assert "warnings" not in result
        if version != "1.0.0" and outcome == "pass":
            assert original in result["checks"]


@pytest.mark.parametrize(
    ("recipe", "version", "snapshot_digest", "decision_digest"),
    [
        (
            "spatial",
            "1.0.0",
            "eab9dcdf33499645820773c381dec0fa689d68ffbad8a78a26c54c74876f06c3",
            "fac9f0cfecd0ef782114588537cb16c472aeb5e7ccc34618a7f9da20ee5ce9bb",
        ),
        (
            "spatial",
            "2.0.0",
            "be20b30e3a491e2e0fe547fafbb1fe383beda6340080d9109b90c98cd7d01300",
            "9067141eb9de089199c5831b3ea953f72b4970c46e0d9ac964cfa074ecd59182",
        ),
        (
            "temporal",
            "1.0.0",
            "fad8677562fc064f8ba163507751b327934d2127bbeba85a3d9e651ad83f954b",
            "e0dff277ce54b15c9ce4b6fad76e9981c53eb5a52f62c9bac83d101660f4d996",
        ),
        (
            "temporal",
            "2.0.0",
            "fd8ea613b3cd35893ace21b6a835605b661b901ca11bc30cb58a95887d8eae06",
            "a250f13b37cfa8e3275fb4d52175e7be28b7ffa41c624edb31919eb02bf2510f",
        ),
        (
            "spatial",
            "2.1.0",
            "27c517138051782b2bcf3407ec14f105f2bac0b56e2f576d95ab04b90f1f8fb3",
            "9cd6ed7d64a1e0eaa8e7f89ee71821236533eda4288caa9269ad6bf5413c06bc",
        ),
        (
            "temporal",
            "2.1.0",
            "e608e2046531a93c21ecab73400bc910fe3d99848345f18b5711b596da42436e",
            "de83af052638cc76e356067b61f164d0766d5563ab81f3153e265c4b8870647a",
        ),
        (
            "spatial",
            "2.2.0",
            "aa4ff891cdb3127afd27c333382c1fa241a475f90a065f5c86016d95633a3bfa",
            "4b13b50f13748144da07c2c3cfd29afe4912e9aea418e4cbb67e14909c99cc17",
        ),
        (
            "temporal",
            "2.2.0",
            "34569ed9d723330dafef38d6a10b88ab9df008309aea18ddea8f926c8f6b8684",
            "b8fc2a5332a8b057fc86586e2f526c6e9416927aba0b1def99421373a69c0ac3",
        ),
    ],
)
def test_prechange_contract_and_decision_goldens_are_exact(recipe, version, snapshot_digest, decision_digest):
    snapshot = build_recipe_snapshot(recipe, recipe_version=version)
    assert snapshot["snapshot_sha256"] == snapshot_digest
    assert_runtime_compatible(snapshot)
    result = verify(recipe, "baseline", frames(recipe), recipe_snapshot=snapshot)
    serialized = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(serialized).hexdigest() == decision_digest


@pytest.mark.parametrize(
    ("recipe_version", "appearance_version"),
    [("2.1.0", "2.0.0"), ("2.2.0", "3.0.0"), ("2.3.0", "4.0.0")],
)
def test_new_comparison_is_frozen_explicit_and_cannot_be_rehashed_away(recipe_version, appearance_version):
    snapshot = build_recipe_snapshot("spatial", recipe_version=recipe_version)
    assert_runtime_compatible(snapshot)
    assert validate_recipe_snapshot(snapshot) == snapshot
    assert snapshot["verifier"]["visual"]["version"] == f"counterpart-visual/{appearance_version}"
    assert (
        snapshot["verifier"]["visual"]["appearance"]["version"]
        == f"counterpart-appearance/{appearance_version}"
    )
    assert "appearance_unknown_policy" not in snapshot["verifier"]
    assert all(clause["required"] is True for clause in snapshot["clauses"])
    changed = deepcopy(snapshot)
    changed["clauses"][1]["required"] = False
    changed["snapshot_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in changed.items() if k != "snapshot_sha256"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    with pytest.raises(RecipeContractError):
        validate_recipe_snapshot(changed)
    assert version_for_mode("live") == "2.3.0"
    assert version_for_mode("replay") == "1.0.0"
    assert all(item["recipe_version"] == "2.3.0" for item in recipe_catalogue())


@pytest.mark.parametrize("version", ["2.1.0", "2.2.0", "2.3.0"])
def test_missing_original_images_still_blocks_without_review_bypass(version):
    result = verify(
        "spatial",
        "baseline",
        frames(),
        recipe_snapshot=build_recipe_snapshot("spatial", recipe_version=version),
    )
    assert not result["accepted"] and result["outcome"] == "unknown"
    assert result["code"] == "visual_evidence_unavailable"
    assert "warnings" not in result


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0", "2.2.0", "2.3.0"])
def test_signed_zero_matches_jsonb_only_in_new_decisions_without_mutating_inputs(monkeypatch, version):
    original = clause()
    original["measurements"] = {
        "candidate_to_reference_pixels": [[1.0, -0.0, 0.0], [-0.0, 1.0, 0]],
        "rotation": -0.0,
        "nested": [{"zero": -0.0, "negative": -0.05, "positive": 0.05}],
        "flag": False,
        "count": 0,
        "text": "-0.0 is an ordinary string",
    }
    before = json.dumps(original)
    install_visual(monkeypatch, [original])
    observed = frames()
    observed[0]["t_ms"] = -0.0
    result = verify(
        "spatial",
        "baseline",
        observed,
        recipe_snapshot=build_recipe_snapshot("spatial", recipe_version=version),
        media_reader=lambda _: b"",
    )
    expected_sign = 1.0 if version in ("2.1.0", "2.2.0", "2.3.0") else -1.0
    assert math.copysign(1.0, result["evidence"]["observations"][0]["t_ms"]) == expected_sign
    assert math.copysign(1.0, observed[0]["t_ms"]) == -1.0
    assert json.dumps(original) == before
    if version == "1.0.0":
        return
    measured = next(c for c in result["checks"] if c["name"] == original["name"])["measurements"]
    assert math.copysign(1.0, measured["rotation"]) == expected_sign
    assert math.copysign(1.0, measured["candidate_to_reference_pixels"][0][1]) == expected_sign
    assert math.copysign(1.0, measured["nested"][0]["zero"]) == expected_sign
    assert measured["nested"][0]["negative"] == -0.05
    assert measured["nested"][0]["positive"] == 0.05
    assert measured["flag"] is False
    assert type(measured["count"]) is int
    assert measured["text"] == original["measurements"]["text"]


@pytest.mark.parametrize("version", ["2.1.0", "2.2.0", "2.3.0"])
def test_advisory_fields_cannot_waive_an_unknown_appearance(monkeypatch, version):
    original = {**clause(), "required": False, "severity": "warning"}
    install_visual(monkeypatch, [original])
    result = verify(
        "spatial",
        "baseline",
        frames(),
        recipe_snapshot=build_recipe_snapshot("spatial", recipe_version=version),
        media_reader=lambda _: b"",
    )
    assert not result["accepted"] and result["outcome"] == "unknown"
    assert result["code"] == "appearance_uncertain"


@pytest.mark.parametrize(
    ("protected", "baseline_digest", "counterpart_digest"),
    [
        (
            False,
            "ffc9a897f528e182cd16686078f431d648d53956ac02e37de70b53cad583c6cb",
            "253ee87b09560d3aa354e58a2f31abccbf90a2198aeb479981e138aaa45d0073",
        ),
        (
            True,
            "bfbab1ea2556aeb945a46f52c3ef3565e187e712bcd1e07c109668e76d51b415",
            "a96fcf39f44cf52061e2b3789ad2c0b45bd4f4b2d3198435361728db6512c676",
        ),
    ],
)
def test_published_21_original_pixel_comparisons_are_byte_identical(
    protected, baseline_digest, counterpart_digest
):
    from test_visual_v2 import image_frame

    assets = {}
    snapshot = build_recipe_snapshot(
        "spatial", recipe_version="2.1.0", protected_objects=["book"] if protected else []
    )
    first = image_frame(assets, protected=protected)
    second = image_frame(assets, right=True, protected=protected)
    baseline = verify(
        "spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__
    )
    counterpart = verify(
        "spatial",
        "counterpart",
        [second],
        {**baseline, "frames": [first]},
        recipe_snapshot=snapshot,
        media_reader=assets.__getitem__,
    )
    for result, digest in ((baseline, baseline_digest), (counterpart, counterpart_digest)):
        assert result["accepted"]
        assert (
            hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            == digest
        )


@pytest.mark.parametrize("corrected_outcome", ["pass", "unknown", "fail"])
def test_only_a_passing_compositing_comparison_can_supersede_raw_color_disagreement(
    monkeypatch, corrected_outcome
):
    from test_visual_v2 import image_frame

    from counterpart import appearance

    def raw_comparison(reference, current, label, index):
        return clause(f"appearance_continuity:{label}:{index}", "fail", "appearance_changed")

    def corrected_comparison(reference, current, label, index, transform, **kwargs):
        return clause(f"appearance_continuity:{label}:{index}", corrected_outcome, "appearance_uncertain")

    monkeypatch.setattr(visual_checks, "_appearance", raw_comparison)
    monkeypatch.setattr(appearance, "compare_appearance", corrected_comparison)
    assets = {}
    first = image_frame(assets)
    second = image_frame(assets, right=True)
    for version in ("counterpart-visual/2.0.0", "counterpart-visual/3.0.0"):
        result = visual_checks.evaluate_visual_controls(
            [second],
            [first],
            media_reader=assets.__getitem__,
            version=version,
        )
        expected = "pass" if version.endswith("3.0.0") and corrected_outcome == "pass" else "fail"
        assert result["outcome"] == expected
        measured = [c for c in result["checks"] if c["name"].startswith("appearance_continuity:")]
        assert all(c["outcome"] == expected for c in measured)
        if version.endswith("3.0.0") and corrected_outcome != "pass":
            assert all(
                c["measurements"]["corrected_comparison"]["outcome"] == corrected_outcome for c in measured
            )


def test_changed_v3_runtime_thresholds_cannot_reinterpret_any_frozen_contract(monkeypatch):
    from counterpart import appearance

    historical = build_recipe_snapshot("spatial", recipe_version="2.1.0")
    latest = build_recipe_snapshot("spatial", recipe_version="2.2.0")
    changed = {**appearance.APPEARANCE_V3_THRESHOLDS, "maximum_compositing_residual": 1.0}
    monkeypatch.setattr(appearance, "APPEARANCE_V3_THRESHOLDS", changed)
    assert build_recipe_snapshot("spatial", recipe_version="2.2.0") == latest
    assert validate_recipe_snapshot(latest) == latest
    assert_runtime_compatible(historical)
    with pytest.raises(RecipeContractError, match="appearance policy"):
        assert_runtime_compatible(latest)


@pytest.mark.parametrize("opacity", [0.15, 0.20, 0.25])
def test_complete_verifier_accepts_background_explained_transparent_change_without_waiving_checks(opacity):
    from test_appearance_comparison import transparent_pair

    assets, first, second = transparent_pair(dark_background=True, opacity=opacity)
    results = {}
    for version in ("2.1.0", "2.2.0"):
        snapshot = build_recipe_snapshot("spatial", recipe_version=version)
        baseline = verify(
            "spatial", "baseline", [first], recipe_snapshot=snapshot, media_reader=assets.__getitem__
        )
        assert baseline["accepted"]
        results[version] = verify(
            "spatial",
            "counterpart",
            [second],
            {**baseline, "frames": [first]},
            recipe_snapshot=snapshot,
            media_reader=assets.__getitem__,
        )
    assert results["2.1.0"]["outcome"] == "fail"
    assert results["2.1.0"]["code"] == "appearance_changed"
    corrected = results["2.2.0"]
    assert corrected["accepted"] and corrected["outcome"] == "pass"
    assert all(check["outcome"] == "pass" and check.get("required", True) for check in corrected["checks"])
    moving = next(check for check in corrected["checks"] if check["name"] == "appearance_continuity:bottle:0")
    assert moving["measurements"]["method"] == "exposed_background_compositing"
    assert moving["measurements"]["maximum_compositing_residual"] < 0.06
    assert corrected["evidence"]["visual"]["appearance"]["version"] == "counterpart-appearance/3.0.0"
    assert "warnings" not in corrected and "human_review_required" not in corrected


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_plain_background_contract_records_weaker_camera_claim_and_keeps_required_checks(recipe):
    snapshot = build_recipe_snapshot(recipe, recipe_version="2.3.0")
    assert_runtime_compatible(snapshot)
    assert snapshot["verifier"]["visual"]["background"]["version"] == "counterpart-background/1.0.0"
    assert snapshot["verifier"]["visual"]["appearance"]["version"] == "counterpart-appearance/4.0.0"
    assert all(clause["required"] for clause in snapshot["clauses"])
    assert not any("Leave a textured background" in line for line in snapshot["instructions"]["setup"])
    assert any("Plain backgrounds are supported" in line for line in snapshot["instructions"]["setup"])
    assert any("do not establish camera pose" in line for line in snapshot["limitations"])
    assert any("first image initializes the reference" in line for line in snapshot["limitations"])
    historical = build_recipe_snapshot(recipe, recipe_version="2.2.0")
    assert "background" not in historical["verifier"]["visual"]
    assert any("Leave a textured background" in line for line in historical["instructions"]["setup"])


@pytest.mark.parametrize("policy", ["background", "appearance"])
def test_new_background_and_appearance_contracts_reject_silent_runtime_changes(monkeypatch, policy):
    from counterpart import appearance, background

    saved = build_recipe_snapshot("spatial", recipe_version="2.3.0")
    if policy == "background":
        monkeypatch.setattr(
            background,
            "BACKGROUND_THRESHOLDS",
            {**background.BACKGROUND_THRESHOLDS, "maximum_changed_fraction": 1.0},
        )
    else:
        monkeypatch.setattr(
            appearance,
            "APPEARANCE_V4_THRESHOLDS",
            {**appearance.APPEARANCE_V4_THRESHOLDS, "maximum_compositing_residual": 1.0},
        )
    assert build_recipe_snapshot("spatial", recipe_version="2.3.0") == saved
    with pytest.raises(RecipeContractError, match=f"{policy} policy"):
        assert_runtime_compatible(saved)
    # The change cannot alter how any saved pre-2.3 contract executes.
    for version in ("1.0.0", "2.0.0", "2.1.0", "2.2.0"):
        assert_runtime_compatible(build_recipe_snapshot("spatial", recipe_version=version))


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_recipe_reaches_visual_controls_so_only_permitted_movers_are_masked(monkeypatch, recipe):
    observed = []

    def comparison(*args, **kwargs):
        observed.append(kwargs)
        return {"checks": [], "evidence": {}}

    monkeypatch.setattr(visual_checks, "evaluate_visual_controls", comparison)
    snapshot = build_recipe_snapshot(recipe, recipe_version="2.3.0", protected_objects=["book"])
    verify(recipe, "baseline", frames(recipe), recipe_snapshot=snapshot, media_reader=lambda _: b"")
    assert len(observed) == 1
    assert observed[0]["recipe"] == recipe
    assert observed[0]["version"] == "counterpart-visual/4.0.0"
    assert observed[0]["protected_objects"] == ["book"]
