# Counterpart presentation and rehearsal

Aim for seven minutes of demonstration and three minutes of questions. Lead with the collector's task and the resulting data. Infrastructure explains why that path still works after things fail.

## Demonstration

**0:00–0:40 — The problem.** Show two plausible observations and ask whether they really isolate the requested change. A collector may move the reference, lose an object, or change the camera. Discovering this later means the scene may be gone. Counterpart checks specific controls while the collector can repair them.

**0:40–2:15 — Make and repair a pair.** Place a bottle left of a cup before a steady camera. Save the first image. Deliberately move the cup with the bottle; show the saved rejected evidence and the precise repair message. Keep the first observation, restore the reference, and take the counterpart. Use live camera only after the physical rehearsal passes; the visibly labeled replay is a reliable software-workflow fallback, not evidence of model quality.

**2:15–3:00 — Interrupt it.** Reload after the first observation. The original evidence survives, but continuing requires a scene recheck: restoring database state cannot restore the physical world. Show the session library. Provider retries use the already saved frames, not whatever happens to be in front of the camera later.

**3:00–4:15 — Make it useful to a data operator.** Open Workspace, the collection, and its sessions. One collector has finished; another remains partway through. Open the complete pair, inspect both observations, record a review, select it and create a release. Download its ZIP. Machine acceptance, human approval and dataset release are separate durable events.

**4:15–5:10 — Why video matters.** Play the two arrival-order clips: matching start/end arrangements, opposite temporal answers. A final frame cannot answer the question. Explain the actual limitation: sampled observations support intervals of arrival; uncertain or occluded order is refused.

**5:10–6:10 — Prove the infrastructure with one failure.** Show the measured fault report, not an invented scale claim: eight synthetic collectors, a real killed worker, seven other sessions still making progress, and ten checkpointed frame observations recovered without another HTTP call or reservation. Explain immutable bytes, frame checkpoints, fenced acceptance and the difference between bounded dispatch and remote exactly-once execution.

**6:10–7:00 — Hand over the dataset and state limits.** Run the portable consumer on the downloaded release. It validates hashes, answers, frozen recipe/review records and partitions without the application database. Mention that actual hosted tabletop diagnostics and physical collector trials are separate. Finish with the next measurable question: does guided collection improve valid-pair yield or review time versus a competent written shot list and post-hoc checks?

## Rehearsal commands

API and worker run in separate terminals; see README. Both scripts below use actual browser/API/Postgres/worker paths with clearly identified fixture perception:

```sh
node scripts/browser_smoke.mjs
node scripts/browser_product_smoke.mjs
uv run python scripts/inspect_release.py artifacts/product-spatial-dataset.zip --allow-replay
uv run python scripts/inspect_release.py artifacts/product-temporal-dataset.zip --allow-replay
uv run python scripts/product_fault_probe.py
```

The explicit `--allow-replay` prevents a demonstration archive entering a real-data pipeline unnoticed. The app shows fixtures on-screen and preserves their provenance in every release.

For physical rehearsal, follow [PHYSICAL_VALIDATION.md](plans/PHYSICAL_VALIDATION.md). Use original captures and independent judgments; save failed attempts too. Do not replace the physical gate with replay, a favorable subset of attempts, or the six-photo source diagnostic.

## Questions to prepare for

- **What is new?** The integrated collection/repair/recovery/review path, not the invention of paired data, leases or object detection.
- **What does Roboflow do?** Actual hosted object detection supplies geometry; the recipe verifies recorded conditions. Optional private dataset export attaches machine predictions and independently tracked receipts.
- **Why not just film it and label later?** That is the competent comparison baseline. The hypothesis is that repair while the scene exists reduces invalid collection and review work; the project has not established that empirically yet.
- **What can be wrong?** Detector errors, repeated lookalike objects, occlusion, approximate controls, dishonest capture clients and unobserved movement. Explicit uncertainty and independent human review remain necessary.
- **What survives a crash?** Original media, capture identity, successful frame observations, immutable pair/release records and external-write intent. Remote computation may still happen twice after ambiguous network failure; every attempt stays accounted.
- **Why no automatic dataset upload?** Local collection and external release have different meanings. A verified private destination and an explicit approved selection are required; enabling an adapter must not sweep historical camera captures.
- **How does it scale?** There is measured bounded multi-collector/failure behavior, shared storage and cross-worker scheduling. No thousand-user throughput or hosted production deployment is claimed.
