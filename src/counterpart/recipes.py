"""Versioned collection contracts, independent of mutable campaign presentation.

The registry deliberately describes only checks the verifier implements. A
snapshot is canonical JSON suitable for persisting with a session and its
manifest, not an instruction to reconstruct a contract at publication time.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, model_validator

from .verification import THRESHOLDS, VERIFIER_VERSION

RecipeId = Literal["spatial", "temporal"]
ObjectClass = Literal["bottle", "cup", "book", "cell phone", "mouse"]
SUPPORTED_OBJECTS: tuple[ObjectClass, ...] = ("bottle", "cup", "book", "cell phone", "mouse")
SNAPSHOT_SCHEMA_VERSION = "counterpart.recipe/1"
CURRENT_RECIPE_VERSION = "1.0.0"


class RecipeParameters(BaseModel):
    """Only supported detector classes and distinguishable class-based roles."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    object_a: ObjectClass = "bottle"
    object_b: ObjectClass = "cup"

    @model_validator(mode="after")
    def distinct_classes(self) -> RecipeParameters:
        if self.object_a == self.object_b:
            raise ValueError("Use two different classes; same-class instance identity is unsupported.")
        return self


class RecipeSnapshot(TypedDict):
    schema_version: str
    recipe: RecipeId
    recipe_version: str
    title: str
    object_a: ObjectClass
    object_b: ObjectClass
    roles: dict[str, str]
    instructions: dict[str, str | list[str]]
    question: str
    expected_answers: dict[str, str]
    capture: dict[str, Any]
    verifier: dict[str, Any]
    controls: list[str]
    limitations: list[str]
    snapshot_sha256: str


class RecipeContractError(ValueError):
    """Unsupported, corrupted, or no-longer-executable collection contract."""


# Persisted contracts must not change when current verifier defaults change.
# Keep this literal historical specification when registering a new version.
_V1_THRESHOLDS = (
    ("minimum_confidence", 0.60),
    ("ambiguity_confidence", 0.35),
    ("image_border_inset", 0.01),
    ("minimum_object_dimension", 0.025),
    ("relation_center_separation", 0.18),
    ("relation_box_gap", 0.02),
    ("anchor_center_tolerance", 0.035),
    ("anchor_scale_tolerance", 0.15),
    ("moving_scale_tolerance", 0.20),
    ("spatial_vertical_tolerance", 0.05),
    ("aspect_ratio_tolerance", 0.02),
    ("temporal_min_frames", 8),
    ("temporal_max_frames", 48),
    ("temporal_max_duration_ms", 12000),
    ("temporal_max_gap_ms", 600),
    ("temporal_min_stable_ms", 150),
    ("temporal_stable_position_tolerance", 0.035),
    ("temporal_start_max_x", 0.42),
    ("temporal_target_min_x", 0.62),
    ("temporal_boundary_guard", 0.025),
    ("temporal_min_lane_separation", 0.20),
    ("temporal_lane_tolerance", 0.055),
    ("temporal_pair_position_tolerance", 0.05),
    ("temporal_min_order_margin_ms", 150),
)
_V1_LIMITATIONS = (
    "Checks use detector observations, which can be wrong or miss objects.",
    "Relations are measured in the image plane, not physical 3-D space.",
    "Object positions and sizes check named controls; they cannot prove that the camera, "
    "lighting, background, or every other scene detail stayed unchanged.",
    "Matching a class across captures does not establish the identity of a physical object.",
    "Supported classes are detector vocabulary, not a guarantee of accuracy for every object or scene.",
)


@dataclass(frozen=True)
class RecipeDefinition:
    recipe: RecipeId
    title: str
    description: str
    version: str = CURRENT_RECIPE_VERSION
    verifier_version: str = "counterpart-verifier/1.0.0"


_DEFINITIONS = (
    RecipeDefinition(
        "spatial",
        "Switch sides",
        "Change a left/right relation while keeping the reference object in place.",
    ),
    RecipeDefinition(
        "temporal",
        "Reverse arrival order",
        "Record opposite arrival orders with matching starting and ending arrangements.",
    ),
)


def get_recipe(recipe: str, version: str = CURRENT_RECIPE_VERSION) -> RecipeDefinition:
    """Resolve an exact version. Unknown versions never fall back to latest."""
    for definition in _DEFINITIONS:
        if definition.recipe == recipe and definition.version == version:
            return definition
    raise RecipeContractError("Choose a supported recipe and exact recipe version.")


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise RecipeContractError("A recipe snapshot must contain finite JSON values.") from exc


def build_recipe_snapshot(
    recipe: str,
    object_a: str = "bottle",
    object_b: str = "cup",
    *,
    recipe_version: str = CURRENT_RECIPE_VERSION,
) -> RecipeSnapshot:
    """Build a detached JSON contract. Persist it once when creating the session."""
    definition = get_recipe(recipe, recipe_version)
    try:
        parameters = RecipeParameters(object_a=object_a, object_b=object_b)
    except ValueError as exc:
        raise RecipeContractError("Choose two different supported object classes.") from exc
    a, b = parameters.object_a, parameters.object_b
    common_setup = [
        f"Use exactly one {a} and one {b}; keep both fully visible.",
        "Fix the camera position, orientation, zoom and crop for both captures.",
    ]
    if recipe == "spatial":
        instructions = {
            "setup": common_setup + [f"Keep the {b} in one position with room on both sides."],
            "baseline": f"Place the {a} clearly left of the {b}, with space between them. Save the image.",
            "counterpart": f"Move only the {a} clearly right of the {b}. Keep its apparent size and "
            "vertical position similar, then save the image.",
            "recovery": f"Restore the {b}'s saved position and size, and the {a}'s size and vertical "
            "position, before continuing. Either side is allowed for this recovery check.",
        }
        controls = [
            "one confident, unambiguous detection for each named class",
            "same image aspect ratio",
            f"stable {b} image position and apparent size",
            f"stable {a} apparent size and vertical image position",
            "clear nonoverlapping left/right relation",
        ]
        roles = {"object_a": "moving_object", "object_b": "reference_object"}
        question = f"Is the {a} to the left or right of the {b}?"
        answers = {"baseline": "left", "counterpart": "right"}
        capture = {"kind": "image", "frames_per_capture": 1, "preview_frames": 1, "recovery_frames": 1}
    else:
        instructions = {
            "setup": common_setup
            + [
                "Place both objects on the left, in separate horizontal lanes.",
                "Each object moves right into the target band and stays there; keep the lanes separate.",
            ],
            "baseline": f"Start recording with both objects still. Move the {a} into the target band "
            f"first, pause clearly, then move the {b}. Hold both still before finishing.",
            "counterpart": f"Reset both objects to their first starting positions. Record the {b} "
            f"entering first, pause clearly, then move the {a}. Match the first ending positions "
            "and hold both still before finishing.",
            "recovery": "Restore each object's saved horizontal lane and apparent size before "
            "continuing. Reset both to the left before recording the next full sequence.",
        }
        controls = [
            "one confident, unambiguous detection for each named class in every sampled frame",
            "same image aspect ratio",
            "stable separate horizontal lanes and apparent object sizes",
            "matching starting and ending positions across the pair",
            "stable initial and final observations",
            "bounded sampling gaps and disjoint guarded arrival intervals",
            "each object enters once and remains in the target band",
            "uninterrupted committed sequence",
        ]
        roles = {"object_a": "baseline_first_arrival", "object_b": "counterpart_first_arrival"}
        question = "Which object entered the target band first?"
        answers = {"baseline": a, "counterpart": b}
        thresholds = dict(_V1_THRESHOLDS)
        capture = {
            "kind": "sequence",
            "minimum_frames": thresholds["temporal_min_frames"],
            "maximum_frames": thresholds["temporal_max_frames"],
            "maximum_duration_ms": thresholds["temporal_max_duration_ms"],
            "maximum_gap_ms": thresholds["temporal_max_gap_ms"],
            "preview_frames": 1,
            "recovery_frames": 1,
            "time_basis": "monotonic recorded frame timestamps; arrival intervals, not exact movement onset",
        }
    snapshot: RecipeSnapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "recipe": definition.recipe,
        "recipe_version": definition.version,
        "title": definition.title,
        "object_a": a,
        "object_b": b,
        "roles": roles,
        "instructions": instructions,
        "question": question,
        "expected_answers": answers,
        "capture": capture,
        "verifier": {"version": definition.verifier_version, "thresholds": dict(_V1_THRESHOLDS)},
        "controls": controls,
        "limitations": list(_V1_LIMITATIONS),
        "snapshot_sha256": "",
    }
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    snapshot["snapshot_sha256"] = hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()
    return snapshot


def validate_recipe_snapshot(value: Any) -> RecipeSnapshot:
    """Validate every field against its immutable version, including digest.

    Digest verification alone would allow a client to weaken thresholds and
    recompute its hash. Compare the whole authoritative contract instead.
    """
    if not isinstance(value, dict) or value.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise RecipeContractError("Unknown or missing recipe snapshot schema version.")
    expected = build_recipe_snapshot(
        value.get("recipe"),
        value.get("object_a"),
        value.get("object_b"),
        recipe_version=value.get("recipe_version"),
    )
    if _canonical(value) != _canonical(expected):
        raise RecipeContractError("Recipe snapshot differs from its registered version.")
    return expected


def assert_runtime_compatible(value: Any) -> None:
    """Fail closed if executing a stored contract would use different rules."""
    snapshot = validate_recipe_snapshot(value)
    verifier = snapshot["verifier"]
    if (
        verifier["version"] != VERIFIER_VERSION
        or _canonical(verifier["thresholds"]) != _canonical(THRESHOLDS)
    ):
        raise RecipeContractError("The running verifier cannot execute this recipe version unchanged.")


def legacy_recipe_snapshot(session: Mapping[str, Any]) -> RecipeSnapshot:
    """Explicit migration for pre-catalogue sessions; never replace a stored one.

    Only the original bottle/cup contract can be inferred for legacy sessions.
    A caller must persist the result and record that the migration was inferred;
    this function does not make a historical snapshot exist retroactively.
    """
    if session.get("recipe_snapshot") is not None:
        return validate_recipe_snapshot(session["recipe_snapshot"])
    if session.get("object_a") != "bottle" or session.get("object_b") != "cup":
        raise RecipeContractError("Legacy sessions only supported the bottle/cup contract.")
    return build_recipe_snapshot(session.get("recipe"), "bottle", "cup")


def recipe_catalogue() -> list[dict[str, Any]]:
    """Public supported contracts. Calling clients may safely mutate the result."""
    return [
        {
            "recipe": definition.recipe,
            "recipe_version": definition.version,
            "title": definition.title,
            "description": definition.description,
            "supported_objects": list(SUPPORTED_OBJECTS),
            "requires_distinct_classes": True,
            "defaults": RecipeParameters().model_dump(),
            "snapshot": deepcopy(build_recipe_snapshot(definition.recipe)),
        }
        for definition in _DEFINITIONS
    ]
