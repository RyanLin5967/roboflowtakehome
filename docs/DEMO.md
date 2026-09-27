# Counterpart presentation and rehearsal

Aim for seven minutes of demonstration and three minutes of questions. Follow one useful pair from capture through review to a dataset someone else can consume. Keep the temporal example, failure reports and detailed delivery controls available for questions; demonstrating every screen would obscure the main story.

## Demonstration

**0:00–0:35 — The problem.** Show two plausible observations and ask whether they isolate the requested change. A collector may move the reference, lose an object, or change the camera. Discovering this later means the scene may be gone. Counterpart checks named controls while the collector can repair them, then preserves how the label was established.

**0:35–1:55 — Make and repair a pair.** Place a bottle left of a cup before a steady camera. Save the first image. Deliberately move the cup with the bottle; show the saved rejected evidence and the specific repair. Keep the first observation, restore the reference, and take the counterpart. Use live camera only after the deferred physical rehearsal passes. Until then, use the visibly labeled replay and describe it as a software-workflow demonstration. Replay uses its original recipe; do not present it as a real-camera demonstration of the newer registration or appearance checks.

**1:55–2:35 — Interrupt it.** Reload after the first observation, using a prepared partial session if necessary. The saved evidence survives, but continuing requires a scene recheck: restoring database state cannot restore the physical world. Point to the frozen model contract. A restarted worker with another model refuses the work before spending credits. Provider retries use the already saved frames, not whatever happens to be in front of the camera later.

**2:35–4:00 — Handle uncertainty without rewriting history.** Open a prepared session with a recorded uncertain observation. Show its original refusal, then append a comparison under another registered verifier version. The new decision appears beside the old one; no detector is rerun and nothing is silently approved. Inspect the original pair and record a human decision only for eligible perceptual uncertainty. Show that its original `accepted: false` result remains and its labels say `human_adjudication`. A known failed control still requires recapture. Finish with the ordinary pair approval: resolving uncertainty and approving data for release are separate actions. The prepared browser fixture demonstrates this behavior, not human annotation accuracy.

**4:00–5:00 — Release data that stays together.** Select the approved pairs and freeze a release. Show two sessions assigned to the same scene group: different collectors or browser sessions do not send the same physical setup into train and test. Download the ZIP and open it with the portable consumer. It checks original image hashes, execution contracts, human authority, answers and partitions without the application database. Later verifier or review decisions leave this release unchanged. Grouping is declared by the operator; it does not magically identify every related scene.

**5:00–5:50 — Show the actual Roboflow handoff.** Open the [generated-card test project](https://app.roboflow.com/ryan-lin-khj4s/counterpart-integration-test) and its recorded annotation/version evidence. A real annotation was written and read back; an actual hosted version's official COCO download matched the selected source, partition and boxes. Call this a generated-card provider integration test. It is not a captured pair, a human-review study or the private camera workflow. The private workflow uploads detector predictions, reads back corrected annotations, records a separate approval and verifies hosted contents; current private-project entitlement is unavailable. The generation acknowledgement was ambiguous, so the probe reconciled through reads and the artifact instead of making a second generation request.

**5:50–6:35 — Substantiate one infrastructure claim.** Show the saved fault report: eight synthetic collectors, an actual killed worker, seven other sessions progressing, and ten checkpointed frame observations recovered without another HTTP call or reservation. Explain fenced acceptance and the distinction between a durable checkpoint and exactly-once remote execution. Use the report's workload and limitations. Do not turn a submission-time measurement into feedback latency or quote an unfinished performance experiment.

**6:35–7:00 — State what the artifact means.** The result is an inspectable paired dataset with its original evidence, model contract, decision history and release authority preserved in the appropriate records. The physical collection study is still deferred, so improved valid-pair yield or review time remains a testable hypothesis. The next comparison is a competent written shot list plus post-hoc checks.

## Rehearsal commands

API and worker run in separate terminals; see README. The browser scripts use actual UI/API/Postgres/worker paths with explicitly labeled fixture perception. They create replay collections; scripted review actions establish software behavior, not independent human labels. Run them before the presentation, not while a performance measurement is in progress:

```sh
node scripts/browser_smoke.mjs
node scripts/browser_product_smoke.mjs
node scripts/gap_browser.mjs
uv run python scripts/inspect_release.py artifacts/product-spatial-dataset.zip --allow-replay
uv run python scripts/inspect_release.py artifacts/product-temporal-dataset.zip --allow-replay
uv run python scripts/inspect_release.py artifacts/gap-browser/dataset.zip --allow-replay
uv run python scripts/product_fault_probe.py
```

The explicit `--allow-replay` prevents a demonstration archive entering a real-data pipeline unnoticed. The app shows fixtures on-screen and preserves their provenance in every release. `gap_browser.mjs` also verifies unchanged original decisions, a separate human-origin pair and release approval, shared scene-group partitions, and refusal to approve a known failed control.

Keep the actual provider records open locally: [annotation readback](../research/roboflow_annotation_probe.json), [version and official-download receipt](../research/roboflow_version_probe.json), and [downloaded COCO annotation](../research/roboflow_version_probe_coco.json). Do not run another generation merely to animate the demo. The provider-transcoded hosted image is not byte-identical to the original local PNG; the local archive remains the source of exact original pixels.

For questions about temporal data, play the prepared opposite-arrival sequences: matching start/end arrangements, different temporal answers. A final frame cannot answer the question. Sampled observations support intervals of arrival, not exact unseen motion onset; ambiguous or interrupted evidence is refused. Leave the baseline-authorization branch and full delivery state machine for a deeper question rather than introducing another main demo path.

For physical rehearsal, follow [PHYSICAL_VALIDATION.md](plans/PHYSICAL_VALIDATION.md). Use original captures and independent judgments; save failed attempts too. Do not replace the physical gate with replay, a favorable subset of attempts, or the six-photo source diagnostic.

## Questions to prepare for

- **What is new?** The integrated collection/repair/recovery/review path, not the invention of paired data, leases or object detection.
- **What does Roboflow do?** Actual hosted object detection supplies observations. Counterpart applies the frozen collection checks and preserves decisions. The private delivery path exports predictions to Roboflow, reads returned annotations, records approval and checks a hosted dataset version against the selected release. The actual provider probe used generated public test cards; private camera export remains disabled.
- **Why not just film it and label later?** That is the competent comparison baseline. The hypothesis is that repair while the scene exists reduces invalid collection and review work; the project has not established that empirically yet.
- **What can be wrong?** Detector errors, repeated lookalike objects, occlusion, approximate controls, dishonest capture clients and unobserved movement. Explicit uncertainty and independent human review remain necessary.
- **Can a reviewer just override a failure?** Known false controls and malformed evidence cannot be waived. Eligible perceptual uncertainty can receive explicit human labels, with the original refusal preserved. Authorizing an uncertain first observation only permits further collection; pair adjudication and subsequent release approval still remain.
- **What happens when the verifier changes?** Compare saved evidence under an exact registered version and append the result. Existing pairs, approvals and releases keep their original evidence. This does not rerun a newer detector or inherit approval from an earlier decision.
- **Do hashes prove the labels are true?** They establish internal consistency and detect changes against a trusted digest. They do not authenticate an unsigned archive's author, prove a human is right or attest a hosted model's weights. Operator-imported COCO verification also cannot prove remote pixel identity or the document's download origin.
- **What survives a crash?** Original media, capture identity, successful frame observations, immutable pair/release records and external-write intent. Remote computation may still happen twice after ambiguous network failure; every attempt stays accounted.
- **Why no automatic dataset upload?** Local collection and external release have different meanings. A verified private destination and an explicit approved selection are required; enabling an adapter must not sweep historical camera captures.
- **How does it scale?** Shared storage, cross-worker admission, fairness, durable checkpoints and bounded previews address specific collection failures. Use only completed benchmark evidence with its workload and baseline. Existing crash-recovery results do not establish production capacity, camera feedback latency or thousand-user throughput.
