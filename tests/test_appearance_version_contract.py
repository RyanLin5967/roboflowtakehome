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
def test_appearance_unknown_blocks_every_capture_slot_and_original_measurement_is_preserved(
    monkeypatch, recipe, slot
):
    snapshot = build_recipe_snapshot(recipe, recipe_version="2.1.0")
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
    assert result["verifier_version"] == "counterpart-verifier/2.1.0"
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
def test_every_unknown_or_failed_control_blocks(monkeypatch, name, code, outcome):
    blocked = clause(name, outcome, code)
    install_visual(monkeypatch, [blocked])
    result = verify(
        "spatial",
        "baseline",
        frames(),
        recipe_snapshot=build_recipe_snapshot("spatial", recipe_version="2.1.0"),
        media_reader=lambda _: b"",
    )
    assert not result["accepted"] and result["outcome"] == outcome
    assert result["code"] == code
    assert "warnings" not in result
    assert blocked in result["checks"]


@pytest.mark.parametrize("scenario", ["wrong_relation", "camera_moved", "ambiguous"])
def test_unknown_appearance_does_not_hide_actual_geometry_or_detection(monkeypatch, scenario):
    install_visual(monkeypatch, [])
    snapshot = build_recipe_snapshot("spatial", recipe_version="2.1.0")
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


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0"])
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
    ],
)
def test_prechange_contract_and_decision_goldens_are_exact(recipe, version, snapshot_digest, decision_digest):
    snapshot = build_recipe_snapshot(recipe, recipe_version=version)
    assert snapshot["snapshot_sha256"] == snapshot_digest
    assert_runtime_compatible(snapshot)
    result = verify(recipe, "baseline", frames(recipe), recipe_snapshot=snapshot)
    serialized = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(serialized).hexdigest() == decision_digest


def test_new_comparison_is_frozen_explicit_and_cannot_be_rehashed_away():
    snapshot = build_recipe_snapshot("spatial", recipe_version="2.1.0")
    assert_runtime_compatible(snapshot)
    assert validate_recipe_snapshot(snapshot) == snapshot
    assert snapshot["verifier"]["visual"]["version"] == "counterpart-visual/2.0.0"
    assert snapshot["verifier"]["visual"]["appearance"]["version"] == "counterpart-appearance/2.0.0"
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
    assert version_for_mode("live") == "2.1.0"
    assert version_for_mode("replay") == "1.0.0"
    assert all(item["recipe_version"] == "2.1.0" for item in recipe_catalogue())


def test_missing_original_images_still_blocks_without_review_bypass():
    result = verify(
        "spatial",
        "baseline",
        frames(),
        recipe_snapshot=build_recipe_snapshot("spatial", recipe_version="2.1.0"),
    )
    assert not result["accepted"] and result["outcome"] == "unknown"
    assert result["code"] == "visual_evidence_unavailable"
    assert "warnings" not in result


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0"])
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
    expected_sign = 1.0 if version == "2.1.0" else -1.0
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


def test_advisory_fields_cannot_waive_an_unknown_appearance(monkeypatch):
    original = {**clause(), "required": False, "severity": "warning"}
    install_visual(monkeypatch, [original])
    result = verify(
        "spatial",
        "baseline",
        frames(),
        recipe_snapshot=build_recipe_snapshot("spatial", recipe_version="2.1.0"),
        media_reader=lambda _: b"",
    )
    assert not result["accepted"] and result["outcome"] == "unknown"
    assert result["code"] == "appearance_uncertain"
