# Original proposal versus the current build

2026-09-26. The user asked whether literally everything from the original Counterpart description, including infrastructure, was finished. Root and two independent audit agents compared the original research descriptions with current source and saved execution evidence. **The prior unconditional completion statement was too broad.** Passing tests establish behavior of implemented features; they do not establish coverage of every original promise.

Original descriptions inspected: `roboflow-labs-takehome-research/FINAL_SHORTLIST.md` (Counterpart section), `round6/RESEARCH_OUTCOME.md` (Counterpart), and `round6/COUNTERPART_INFRA.md`. These are outside this repository. The later implementation plan narrowed some original capabilities; that narrowing needs to be stated, not silently treated as full delivery.

## Implemented and exercised

- Durable captures, immutable media, saved baselines, targeted repair, reconnect scene checks, idempotent submissions/publication and stale-worker fencing.
- Separate disposable-preview and committed-evidence scheduling, bounded admission, cross-worker dispatch fairness, per-frame checkpoints and durable cost reservations.
- Campaigns, scoped invitations, collector recovery, human review, immutable releases, pair/session-level splits and an independent archive consumer.
- Export receipts and bounded reconciliation; immutable filesystem/S3 adapters; authentication, cleanup, backup/restore and container deployment.

Executed evidence remains valid: 752 Python tests, 17 browser tests, actual product browser flows, actual worker-process failures, PostgreSQL backup/restore, S3 HTTP-emulator faults and isolated container build/restart/mobile checks. See [validation](VALIDATION_REPORT.md). Physical trials and private hosted export are separate.

## Actual gaps and narrower implementations

| Original capability | Current implementation and gap | Source evidence |
|---|---|---|
| Freeze the model identity with the collection contract | Recipe/verifier thresholds are frozen; the detector model/configuration comes from current worker settings. Recording each observation's model and separating cache keys does not pin an existing session to its initial model. A changed worker can continue with another detector configuration. | `src/counterpart/recipes.py:218`, `src/counterpart/worker.py:47`, `docs/plans/COMPLETENESS_REVIEW.md` S4 |
| Re-evaluate saved captures under a new verifier and append a decision revision | No product operation or durable revision history for this exists. Retrying a failed capture and checking an archive against the same verifier are different operations. | `src/counterpart/recipes.py:259`, `src/counterpart/consumer.py:507`, capture schema and API |
| Keep related sessions together in dataset splits | Both halves of a session stay together, and identical assets cannot cross release partitions. There is no explicit shared scene/collection-group identifier binding related sessions to one split. | `src/counterpart/splits.py`, `src/counterpart/repository.py:853`, `src/counterpart/collections.py:528` |
| Measure feedback age/p95 under increasing offered load versus a competent bounded queue | Current probes establish burst coalescing, fairness and recovery. `submit_ms_p95` measures submission latency, not end-to-end feedback age. The comparative scheduler experiment is missing. | `scripts/reliability_probe.py`, `scripts/product_fault_probe.py`, `research/product_fault_probe.json` |
| Camera registration and broader object/scene controls | Current verification uses detected box positions/sizes for two distinct classes. It has no image-registration transform, independent extra-distractor checks, or physical instance matching. Two fixed recipe families are implemented; the broader composable/three-valued clause design is not. | `src/counterpart/verification.py:73`, `src/counterpart/verification.py:189`, `src/counterpart/recipes.py:21` |
| Route ambiguous observations to review | Ambiguity currently produces a refusal/repair reason. Human review operates on published machine-accepted pairs; it cannot resolve a rejected/uncertain attempt into reviewed evidence. Explicit true/false/unknown clause results and that review path are absent. | `src/counterpart/verification.py:549`, `src/counterpart/repository.py:727`, `src/counterpart/collections.py:376` |
| Roboflow annotation-review and versioned dataset delivery | Hosted perception works. Image/prediction export and receipts are implemented, but private captured-pair export is disabled by account entitlement and has not run. Roboflow Annotate review round-trips and automatic creation of hosted dataset versions are not implemented; review and release versions currently belong to Counterpart. | `src/counterpart/providers.py`, `src/counterpart/exporting.py`, `src/counterpart/operator_api.py` |

The model-pin finding is a correctness gap under configuration changes, even though the current development worker uses one unchanged model. It is the first repair priority. New-verifier decision history, related-session split grouping and the comparative feedback benchmark are the next concrete infrastructure omissions.

## Deferred, external, or explicitly optional

- Physical-camera collection quality, temporal reliability and collection-efficiency comparison are unmeasured; the user explicitly deferred the manual trial. Do not count this as implemented proof or substitute replay.
- Private Roboflow export needs private-project entitlement. Public hosting/TLS, hosted S3 compatibility and remote CI have not been executed. Container deployment was actually exercised locally.
- Custom detector training, downstream VLM improvement, a cost-optimizing physical-action planner, automatic condition-based assignment, arbitrary recipes, Kubernetes and new transport protocols were optional or not promised in the selected scope. Their absence is not a reason to manufacture additional requirements.

This audit records discrepancies; it does not claim they were fixed. No application code, provider configuration or cloud workload was changed by this audit.
