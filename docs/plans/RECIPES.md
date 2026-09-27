# Versioned collection recipes

2026-09-26. The catalogue makes existing verified collection tasks reusable across real campaigns. It does not add a nominal recipe whose evidence cannot be checked. `spatial` and `temporal` remain valid identifiers; both are registered at version `1.0.0`.

## Collector roles and supported classes

The caller selects two distinct canonical detector labels from `bottle`, `cup`, `book`, `cell phone`, and `mouse`. These are convenient desk objects in the configured COCO model vocabulary, not a promise that every instance is detected accurately. Objects still have to pass confidence, uniqueness, visibility, geometry and pair controls. Similar appearance, occlusion, shiny surfaces and unusual viewpoints can require a different prop or scene.

The class labels are literal. No arbitrary class text, aliases, case folding, same-class pairs or client-supplied thresholds are accepted. Tracking two different cups would need an instance identity method that this verifier does not implement. Requiring different classes is a correctness boundary, not just a form restriction.

| Recipe | Object A | Object B | Changed answer |
|---|---|---|---|
| Switch sides (`spatial`) | Moving object | Stationary reference object | A is left of B, then right of B |
| Reverse arrival order (`temporal`) | First arrival in the baseline | First arrival in the counterpart | A arrives first, then B arrives first |

Instructions, questions, expected answers, controls and recovery instructions are rendered from these roles. Temporal questions concern observed target-band arrival, never unknowable exact motion onset. Recovery is a scene check with its own instructions; it is not a replacement recording and does not establish a complete pair.

Replay mode is a separate demonstration contract. Existing generated props depict bottle/cup; the HTTP layer should keep replay restricted to that pair rather than relabeling a drawing as a different physical object. Deterministic tests can rename observation labels to establish that verifier logic is class-parameterized; that test does not evaluate detector accuracy for those classes.

## Snapshot protocol

`build_recipe_snapshot(recipe, object_a, object_b, recipe_version="1.0.0")` returns a detached JSON object. Store that object at creation, then preserve it in the session, campaign allocation and published manifest. Publication must not reconstruct it from the current catalogue.

| Field | Meaning |
|---|---|
| `schema_version` | Exact serialized contract format, currently `counterpart.recipe/1` |
| `recipe`, `recipe_version` | Exact registry entry; there is no `latest` alias |
| `object_a`, `object_b`, `roles` | Fixed detector labels and their task roles |
| `instructions` | Setup, first capture, counterpart and scene recovery instructions |
| `question`, `expected_answers` | Intended changed question and answers for the two slots |
| `capture` | Image/sequence requirements and applicable frame/time bounds |
| `verifier` | Exact verifier version and full threshold specification |
| `controls`, `limitations` | Named checks and boundaries of the evidence |
| `snapshot_sha256` | SHA-256 of canonical JSON excluding this hash field |

The hash identifies a contract; it is not a signature or a substitute for validation. `validate_recipe_snapshot` rebuilds the registered version for the supplied supported roles and compares the entire canonical JSON, including hash. Changing a threshold and recomputing its hash remains invalid. Extra fields, missing fields, unknown schema/recipe versions, nonfinite values and altered instructions all fail closed with `RecipeContractError`, a `ValueError` subclass.

Serialization uses sorted keys, compact separators, ASCII escaping and rejects nonfinite floats. API consumers may mutate their returned dictionary without altering the registry or another caller's snapshot. Database immutability remains the repository's responsibility; a mutable Python dictionary by itself is not a persistence guarantee.

## Execution and migration

Call `assert_runtime_compatible(snapshot)` before executing saved work. It first validates the complete contract, then checks that the running verifier's version and numeric threshold specification exactly match it. A future verifier upgrade must not silently reinterpret an in-progress session. Compatibility failure should produce an explicit unsupported-contract result, not replay fallback or generic retry until credits are exhausted.

Historical thresholds are literal data in the registry, independent of the current verifier's mutable defaults. To introduce changed checks or instructions:

1. Register a new exact recipe version and retain the previous contract for readback.
2. Provide a compatible verifier or explicitly prevent further capture under the retired version.
3. Start newly allocated sessions with the new version. Never alter accepted historical evidence or a published manifest to fit it.

Legacy sessions created before snapshots existed should remain explicitly marked as missing historical snapshots. `legacy_recipe_snapshot` is available only for an intentional, recorded migration: it can infer the original bottle/cup task or validate an already present snapshot. It rejects arbitrary legacy roles and does not fabricate proof that a snapshot was stored at original capture time. The repository need not use this helper; leaving legacy state explicit is safer than a silent backfill.

## Validation performed

`tests/test_recipes.py` verifies all 40 supported recipe/ordered-class combinations against the actual deterministic verifier using generated observations; every combination accepts a valid baseline and counterpart with matching answer semantics. This is parameter/logic coverage, not real camera validation or a model benchmark.

The suite also attacks same-class identity, arbitrary and malicious labels, unknown versions, extra/missing fields, recomputed malicious hashes, NaN/Infinity and type coercion, frozen registry metadata, shared mutable state, legacy migration, and changed runtime rules. Pinned default-contract hashes detect accidental edits to historical version `1.0.0`.

No new model, training job or cloud request is required for this catalogue. Real per-class capture checks remain separate evidence.
