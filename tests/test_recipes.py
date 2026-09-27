"""Recipe contracts and compatibility, using deterministic observations only."""

import hashlib
import itertools
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from counterpart import recipes
from counterpart.fixtures import generate
from counterpart.recipes import (
    SUPPORTED_OBJECTS,
    RecipeContractError,
    RecipeParameters,
    assert_runtime_compatible,
    build_recipe_snapshot,
    get_recipe,
    legacy_recipe_snapshot,
    recipe_catalogue,
    validate_recipe_snapshot,
)
from counterpart.verification import THRESHOLDS, VERIFIER_VERSION, verify


def digest(snapshot):
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
@pytest.mark.parametrize(("object_a", "object_b"), list(itertools.permutations(SUPPORTED_OBJECTS, 2)))
def test_every_advertised_role_pair_uses_actual_verifier(recipe, object_a, object_b):
    snapshot = build_recipe_snapshot(recipe, object_a, object_b)
    assert validate_recipe_snapshot(snapshot) == snapshot
    assert snapshot["snapshot_sha256"] == digest(snapshot)
    assert snapshot["verifier"] == {"version": VERIFIER_VERSION, "thresholds": THRESHOLDS}
    assert_runtime_compatible(snapshot)

    def observations(scenario):
        # Remapping a deterministic observation tests the parameterized contract,
        # not detector accuracy or physical appearance of these object classes.
        result = []
        for frame in generate(recipe, scenario):
            frame = {
                key: deepcopy(value) for key, value in frame.items() if key not in ("bytes", "provenance")
            }
            for detection in frame["detections"]:
                detection["label"] = {"bottle": object_a, "cup": object_b}[detection["label"]]
            result.append(frame)
        return result

    first_frames = observations("baseline")
    first = verify(recipe, "baseline", first_frames, object_a=object_a, object_b=object_b)
    assert first["accepted"], first
    second = verify(
        recipe,
        "counterpart",
        observations("counterpart"),
        {**first, "frames": first_frames},
        object_a=object_a,
        object_b=object_b,
    )
    assert second["accepted"], second
    if recipe == "spatial":
        assert first["evidence"]["relation"] == snapshot["expected_answers"]["baseline"]
        assert second["evidence"]["relation"] == snapshot["expected_answers"]["counterpart"]
    else:
        assert first["evidence"]["arrival_order"][0] == snapshot["expected_answers"]["baseline"]
        assert second["evidence"]["arrival_order"][0] == snapshot["expected_answers"]["counterpart"]


@pytest.mark.parametrize("object_class", SUPPORTED_OBJECTS)
def test_same_class_cannot_claim_physical_instance_identity(object_class):
    with pytest.raises(RecipeContractError, match="different supported"):
        build_recipe_snapshot("spatial", object_class, object_class)
    with pytest.raises(ValidationError, match="same-class instance identity"):
        RecipeParameters(object_a=object_class, object_b=object_class)


@pytest.mark.parametrize(
    "bad_class",
    [None, True, 4, [], {}, "", "person", "phone", "Bottle", " cup", "cup ", "<script>", "cup\x00"],
)
@pytest.mark.parametrize("field", ["object_a", "object_b"])
def test_classes_fail_closed_without_aliases_or_user_defined_html(field, bad_class):
    with pytest.raises(RecipeContractError):
        build_recipe_snapshot("spatial", **{field: bad_class})


@given(st.text().filter(lambda label: label not in SUPPORTED_OBJECTS))
def test_arbitrary_class_labels_are_not_silently_mapped(label):
    with pytest.raises(RecipeContractError):
        build_recipe_snapshot("temporal", label, "cup")


@pytest.mark.parametrize("recipe", [None, "", "vertical", "SPATIAL", "temporal/1", {}, [], True])
def test_unknown_recipes_do_not_fall_back(recipe):
    with pytest.raises(RecipeContractError):
        build_recipe_snapshot(recipe)


@pytest.mark.parametrize("version", [None, "", "latest", "1", "1.0", "0.9.0", "3.0.0", 1, True, {}])
def test_unknown_versions_do_not_fall_back(version):
    with pytest.raises(RecipeContractError):
        build_recipe_snapshot("spatial", recipe_version=version)


def test_registry_and_parameter_objects_are_immutable():
    with pytest.raises(FrozenInstanceError):
        get_recipe("spatial").title = "new title"
    with pytest.raises(ValidationError):
        RecipeParameters().object_a = "book"
    with pytest.raises(ValidationError):
        RecipeParameters(threshold=0.01)


def test_returned_json_has_no_shared_nested_mutable_registry_state():
    clean = build_recipe_snapshot("temporal", "mouse", "book")
    dirty = build_recipe_snapshot("temporal", "mouse", "book")
    dirty["verifier"]["thresholds"]["minimum_confidence"] = 0.0
    dirty["instructions"]["setup"].append("invented instruction")
    dirty["controls"].clear()
    assert clean == build_recipe_snapshot("temporal", "mouse", "book")
    validated = validate_recipe_snapshot(clean)
    clean["expected_answers"]["baseline"] = "book"
    assert validated["expected_answers"]["baseline"] == "mouse"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("verifier", "thresholds", "minimum_confidence"), 0.01),
        (("verifier", "version"), "counterpart-verifier/0.0.1"),
        (("expected_answers", "baseline"), "cup"),
        (("roles", "object_a"), "reference_object"),
        (("instructions", "baseline"), "No recording needed."),
        (("capture", "maximum_gap_ms"), 30000),
        (("controls",), []),
        (("limitations",), []),
        (("title",), "Different task"),
        (("snapshot_sha256",), "0" * 64),
    ],
)
def test_tampering_is_rejected_even_with_recomputed_digest(path, value):
    snapshot = build_recipe_snapshot("temporal")
    parent = snapshot
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value
    if path != ("snapshot_sha256",):
        snapshot["snapshot_sha256"] = digest(snapshot)
    with pytest.raises(RecipeContractError):
        validate_recipe_snapshot(snapshot)


def test_numbers_cannot_be_replaced_by_booleans_or_coerced_strings():
    for bad_value in (True, "0.6", float("nan"), float("inf")):
        snapshot = build_recipe_snapshot("spatial")
        snapshot["verifier"]["thresholds"]["minimum_confidence"] = bad_value
        with pytest.raises(RecipeContractError):
            validate_recipe_snapshot(snapshot)


def test_missing_or_extra_fields_are_not_accepted():
    original = build_recipe_snapshot("spatial")
    for key in original:
        snapshot = deepcopy(original)
        snapshot.pop(key)
        with pytest.raises(RecipeContractError):
            validate_recipe_snapshot(snapshot)
    original["accept_everything"] = True
    with pytest.raises(RecipeContractError):
        validate_recipe_snapshot(original)


@pytest.mark.parametrize("value", [None, [], "spatial", {}, {"schema_version": "counterpart.recipe/2"}])
def test_unknown_snapshot_schema_cannot_be_guessed(value):
    with pytest.raises(RecipeContractError):
        validate_recipe_snapshot(value)


def test_runtime_threshold_upgrade_cannot_reinterpret_old_session(monkeypatch):
    snapshot = build_recipe_snapshot("spatial")
    monkeypatch.setitem(recipes.THRESHOLDS, "minimum_confidence", 0.25)
    # Historical contracts remain parseable but cannot run with changed rules.
    assert validate_recipe_snapshot(snapshot) == snapshot
    assert build_recipe_snapshot("spatial") == snapshot
    with pytest.raises(RecipeContractError, match="running verifier"):
        assert_runtime_compatible(snapshot)


def test_runtime_verifier_upgrade_requires_explicit_version_support(monkeypatch):
    snapshot = build_recipe_snapshot("temporal")
    monkeypatch.setattr(recipes, "VERIFIER_VERSION", "counterpart-verifier/2.0.0")
    assert validate_recipe_snapshot(snapshot) == snapshot
    with pytest.raises(RecipeContractError, match="running verifier"):
        assert_runtime_compatible(snapshot)


def test_explicit_legacy_migration_only_infers_original_known_roles():
    snapshot = legacy_recipe_snapshot({"recipe": "spatial", "object_a": "bottle", "object_b": "cup"})
    assert snapshot == build_recipe_snapshot("spatial")
    with pytest.raises(RecipeContractError, match="Legacy sessions"):
        legacy_recipe_snapshot({"recipe": "spatial", "object_a": "book", "object_b": "cup"})
    with pytest.raises(RecipeContractError):
        legacy_recipe_snapshot({"recipe": "spatial"})


def test_existing_snapshot_never_gets_silently_replaced_by_migration():
    snapshot = build_recipe_snapshot("temporal", "book", "mouse")
    restored = legacy_recipe_snapshot({"recipe_snapshot": snapshot})
    assert restored == snapshot
    snapshot["verifier"]["thresholds"]["minimum_confidence"] = 0.0
    with pytest.raises(RecipeContractError):
        legacy_recipe_snapshot({"recipe_snapshot": snapshot})
    with pytest.raises(RecipeContractError):
        legacy_recipe_snapshot(
            {"recipe_snapshot": {}, "recipe": "spatial", "object_a": "bottle", "object_b": "cup"}
        )


def test_catalogue_is_exactly_the_supported_executable_versions():
    catalogue = recipe_catalogue()
    assert [entry["recipe"] for entry in catalogue] == ["spatial", "temporal"]
    for entry in catalogue:
        assert entry["requires_distinct_classes"]
        assert entry["supported_objects"] == list(SUPPORTED_OBJECTS)
        assert entry["defaults"] == {"object_a": "bottle", "object_b": "cup", "protected_objects": []}
        assert_runtime_compatible(entry["snapshot"])
    catalogue[0]["supported_objects"].append("dragon")
    catalogue[0]["snapshot"]["verifier"]["thresholds"].clear()
    assert recipe_catalogue()[0]["supported_objects"] == list(SUPPORTED_OBJECTS)
    assert_runtime_compatible(recipe_catalogue()[0]["snapshot"])


def test_temporal_instructions_describe_arrival_not_unobserved_motion_onset():
    snapshot = build_recipe_snapshot("temporal", "book", "cell phone")
    assert snapshot["question"] == "Which object entered the target band first?"
    assert snapshot["expected_answers"] == {"baseline": "book", "counterpart": "cell phone"}
    assert "book" in snapshot["instructions"]["baseline"]
    assert "cell phone" in snapshot["instructions"]["counterpart"]
    assert snapshot["capture"]["maximum_gap_ms"] == THRESHOLDS["temporal_max_gap_ms"]
    assert "not exact movement onset" in snapshot["capture"]["time_basis"]


def test_spatial_instructions_distinguish_moving_and_reference_object():
    snapshot = build_recipe_snapshot("spatial", "mouse", "book")
    assert snapshot["roles"] == {"object_a": "moving_object", "object_b": "reference_object"}
    assert snapshot["question"] == "Is the mouse to the left or right of the book?"
    assert "Move only the mouse" in snapshot["instructions"]["counterpart"]
    assert "book's saved position" in snapshot["instructions"]["recovery"]


@pytest.mark.parametrize(
    ("recipe", "expected_digest"),
    [
        ("spatial", "eab9dcdf33499645820773c381dec0fa689d68ffbad8a78a26c54c74876f06c3"),
        ("temporal", "fad8677562fc064f8ba163507751b327934d2127bbeba85a3d9e651ad83f954b"),
    ],
)
def test_version_one_historical_contracts_do_not_silently_change(recipe, expected_digest):
    # Changing instructions, thresholds or output semantics requires registering
    # a new recipe version while retaining the existing contract for readback.
    assert build_recipe_snapshot(recipe)["snapshot_sha256"] == expected_digest
