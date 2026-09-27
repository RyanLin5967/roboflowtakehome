# Saved evidence, verifier revisions, and uncertainty review

Counterpart keeps the original machine decision immutable at publication. The operator evidence workbench offers three distinct actions; none silently rewrites a prior pair, approval, or release.

## Compare a verifier against saved observations

`POST /api/operator/sessions/{session}/captures/{capture}/reverify` takes an exact registered `recipe_version`, the capture's `expected_evidence_sha256`, `expected_version` of its revision history, and an idempotency key. It reads and validates the original media, preserves classes and protected-object parameters, rechecks the original reference under the requested version, and appends `counterpart.evidence-revision.v1`.

Each revision records both recipe snapshots, original capture/reference hashes, the frozen execution contract, complete new decisions, bindings to the original model observations, and whether a separately recorded baseline authorization was applied. A human authorization from one recipe version is never carried into a comparison under another version. This operation makes **zero provider calls** and does not rerun a detector. Changing detector weights would require separately budgeted new observations and is outside this operation.

Concurrent retries with the same request key return the same revision. Different requests racing the same expected version conflict. PostgreSQL triggers reject updates and deletes of revision history. Existing pair manifests, ordinary review versions, and releases remain tied to their original decisions; a later comparison does not inherit an old approval.

## Review an uncertain or rejected attempt

Every completed committed attempt can receive an append-only operator disposition: `confirmed_uncertain`, `rejected`, or `needs_recapture`. It uses its own optimistic version, source hash, reason, and idempotency key. This disposition does not turn the machine result into an acceptance.

The separate pair-adjudication action can resolve **perceptual uncertainty** into explicitly human-derived labels after the operator inspects all original images and every named recipe control. The record binds the exact original captures, assigned pair roles, recipe, model contract, answers, criteria, reason, reviewer, and timestamp. Both source captures remain verbatim, including rejected/unknown results and their original source roles. The resulting `counterpart.pair.v2` starts with ordinary pair review **pending**; release creation still requires a separate approval.

Known false controls cannot be waived by a checkbox or rationale. Structural validation independently checks frame counts, sampling order/gaps/duration, image identities/dimensions, finite detection geometry, interruption, and observation provenance. Original decisions are reproduced under their exact frozen version. Independent geometric checks also run for historical v1 decisions, so an early uncertain bottle detection cannot hide a visibly moved cup. Failed controls dominate uncertainty. Ambiguous temporal ordering, absent media, malformed evidence, and interrupted captures require recapture.

Human labels are not detector predictions or independent ground truth. Portable examples retain `label_origin: human_adjudication`, the full adjudication record, the unchanged machine decisions, and the frozen later approval. Roboflow upload annotations remain detector **predictions** until the separate returned-annotation review workflow establishes corrected boxes.

## Authorize an uncertain first observation

A visually uncertain first observation should not force a collector to pretend it was machine-accepted. A separate authorization permits collecting its counterpart when the original v2 baseline's object roles and geometry already pass independently, but registration or appearance evidence is uncertain. Missing, ambiguous, or low-confidence role geometry is ineligible; no boxes or confidence scores are fabricated. Protected objects must also have complete, stable geometry.

`POST /api/operator/sessions/{session}/captures/{capture}/authorize-baseline` records a specific human answer, all baseline criteria, rationale, frozen recipe/model/evidence bindings, expected session revision, and idempotency key. It appends `counterpart.baseline-authorization/1`, advances the session revision, and marks the original observation as the session's reference **without changing its machine result**.

The active session copy and the copy frozen into a subsequent capture must exactly match the immutable authorization row; database triggers enforce this binding. Replacing a baseline clears the active authorization. A lost-response retry returns its original receipt rather than reactivating an old reference. Resuming before or after authorization retains the need for scene recovery. Capturing a candidate in the human-review path does not silently claim that recovery passed.

The worker passes this separately authenticated authorization to the exact verifier. All new counterpart checks still execute, and the output explicitly requires human review. Ordinary publication refuses this path. The operator must adjudicate the full pair, inspect any unresolved recovery, and obtain the subsequent ordinary pair approval before releasing it.

## Portable records and limits

The offline consumer supports historical `counterpart.pair.v1` and explicit human-origin `counterpart.pair.v2`. It reproduces original decisions, validates baseline authority and declared label origin, checks scene split-group provenance and execution contracts, checks exact generated JSONL, and verifies every media hash/byte count/dimension. Related sessions retain their frozen group even after human adjudication.

These checks prove internal consistency, not the identity or truthfulness of an archive's author. Hashes are not signatures, a human assertion can be wrong, and detector confidence is not calibrated certainty. The user-deferred physical collection study remains separate from deterministic fixture, API, PostgreSQL, migration, and archive tests.
