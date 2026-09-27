# Bounded cloud perception diagnostic

This probe runs Roboflow's official RF-DETR Small on one Modal L4 GPU. It checks real public tabletop photos, preserves incorrect and uncertain outcomes, and measures GPU prediction service time. It does not train a model or substitute for the user's deferred physical-camera trial.

The plan is frozen in [modal_perception_probe.plan.json](../research/modal_perception_probe.plan.json). The durable reservation and eventual function-call ID are in [modal_perception_probe.state.json](../research/modal_perception_probe.state.json). A completed call writes [modal_perception_probe.json](../research/modal_perception_probe.json). The launcher is [modal_perception_probe.py](../scripts/modal_perception_probe.py).

## Selection and controls

The independent [What's “up” source](https://github.com/amitakamath/whatsup_vlms/tree/7c1f2550eace32e7b8c77de5a792347c402960d1) defines the first caption as correct. Controlled Images B contains 408 annotations, including 204 left/right photos. A predeclared mapping of original names to distinct COCO classes selects **all 58 compatible left/right images, forming 29 pairs**. For example, mug maps to cup and phone to cell phone; source names that collapse into the same detector class are excluded. Both filename and first-caption labels must agree before the plan is frozen. There is no prediction-driven selection.

Six originals overlap the earlier serverless diagnostic and their byte hashes must agree; 52 are new to this diagnostic. All selected image hashes and the downloaded archive hash are recorded before model inference. Original photos and model weights stay in the remote ephemeral filesystem and are not redistributed.

The detector uses `rfdetr==1.10.1`, official `RFDETRSmall`, CUDA, float32, and a fixed 0.25 detection threshold. It is related to the application's hosted `coco/39` model, but **checkpoint equivalence is not established**. The actual downloaded checkpoint SHA256 and package versions are retained. There is no threshold sweep or training.

For each photo, the existing conservative geometry parser either reports a source relation or abstains. A pair scores correct only when both independent source relations are correct. Denominators include failed predictions and abstentions. The visual checks run over all available pairs; their outcomes are descriptive because source labels establish left/right, not unchanged background or physical identity. The three pairs compatible with the application's supported classes additionally run the frozen v2 recipe. No invented human scene-control labels are supplied.

The timing probe repeats the six existing images 20 times each after six warm-up predictions. CUDA synchronization brackets each prediction. These 120 samples measure PIL-image-to-prediction service time inside the cloud container; they exclude download, GPU cold start, network round trips and scheduling. They are not the end-to-end shared-queue benchmark or an application latency promise.

## Billing controls

The user explicitly reconfirmed **$3.39 available** and authorized its use. A fresh authenticated Modal summary returned **$26.61 metered, $0 billed**; all existing apps had zero tasks. The previous user-set workspace spend limit was $0. Its current value is not exposed by the available Starter billing API; the environment-budget API refuses Starter access. We record that limitation instead of claiming a fresh spend-limit read.

The single experiment reserves **$0.80** before image build. One L4 container is capped at 1,200 seconds execution, 120 seconds startup, two CPU cores, 8 GiB RAM, two seconds idle tail, and zero retries. At the live API's $0.80/L4-hour, $0.0473/core-hour, and $0.008/GiB-hour rates, the specified resource-time envelope is approximately **$0.352**; the rest of the reservation covers image build and incident headroom. There are no persistent volumes or deployed endpoints. Build/platform overhead is not a mathematically guaranteed invoice bound; the confirmed credits exceed the $0.80 reservation by $2.59. The historical $0 spend-limit setting is recorded, but its current enforcement is not freshly verified and is not represented as a guaranteed control.

Pricing and control references: [Modal pricing](https://modal.com/pricing), [budgets and spend limits](https://modal.com/docs/guide/budgets), [billing summaries and reporting delay](https://modal.com/docs/guide/billing). Usage estimates must not be described as settled invoice charges.

## Recovery

The launcher reserves with an exclusive file creation and refuses another run if its state exists. It persists the app identity and function-call ID immediately after submission, before waiting for the result. If the local client exits, use the recorded call with the `collect` action; do not delete state and rerun. An interrupted build or submission with no call ID needs an explicit provider-state investigation, not an automatic retry. The remote timeout bounds a disconnected function.

Use the existing separate Modal environment; the app runtime does not gain GPU packages:

```sh
../coolcvproject/.venv/bin/python scripts/modal_perception_probe.py collect
```

The original `prepare` and `run` were executed once; one explicit `repair-archive` followed the confirmed pre-inference archive-format failure. A distinct future experiment needs a new reviewed plan and reservation, not reuse of this run ID.

## Executed result — 2026-09-27 UTC

The L4 run completed **184 real GPU predictions** in a 34.52-second function body, including source/model loading. All 58 source photos produced observations. The fixed relation parser answered **53/58 correctly (91.38%)**, abstained on five, and answered none incorrectly; **25/29 pairs (86.21%)** had both relations correct. This is a 91.38% all-image success rate, not an advertised 100% accuracy obtained by hiding abstentions. The previously unseen 52-photo subset had 48 correct relations.

The five abstentions were preserved: three low-confidence observations, one missing object, and one ambiguous object. Among the six photos shared with the earlier hosted probe, this separately loaded checkpoint abstained on one; the hosted six-photo result must not be substituted for it.

The 120 synchronized warm predictions measured **23.30 ms median, 24.85 ms p95, 25.74 ms maximum**. These are unoptimized float32 GPU service times under the exact scope above, not network or shared-load latencies.

Every one of the 29 pairs had an **uncertain visual-control outcome**, because too few background features established registration. The three supported-product baselines likewise remained uncertain. This is a practical limitation of the conservative v2 controls on mostly textureless tabletop backgrounds, and a reason to retain the human adjudication route. No threshold was loosened to turn these into passes.

The first invocation failed before any model inference because the source archive was gzip TAR and the launcher initially expected ZIP. Its failed call is retained. A read-only magic-byte check established the format; an explicit, known-failure repair used the same selection/thresholds and a shorter 900-second timeout. The combined specified resource envelopes for both attempts are $0.6242, with the remainder of the original $0.80 reservation reserved for build overhead. This was not an automatic retry of an unknown outcome.

Both apps are **stopped with zero tasks**. The post-run billing summary was **$26.65 metered, $0 billed**, a displayed $0.04 increase from before the probe; reporting can lag and this is not a settled invoice. [Execution receipt](../research/modal_perception_probe.execution.json) binds the original frozen-plan hash, actually executed repaired runner source/hash, returned-result hash, billing response, and shutdown evidence.

Independent review found two additional harness weaknesses: the running script augmented the report's plan with discovered image hashes, and an exception in later visual/timing diagnostics could discard earlier measurements. The actual successful returned artifact is preserved unchanged. The final launcher now retains an unchanged embedded plan and isolates later diagnostic failures. A fake-CUDA/fake-model regression exercises 58 preserved observations, 29 visual failures and 126 warmup/timing failures without local model compute; it passed. This robustness patch was not rerun on the GPU, and the receipt preserves the exact earlier source that produced the measurements.

## Evidence boundary

This small controlled source cannot establish broad model accuracy. Pretraining overlap is unknown. Most selected object combinations exceed the product's five-class recipe registry and are explicitly detector diagnostics, not newly supported capture recipes. Visual consistency does not prove physical identity. The user deferred the real-camera trial, and this probe does not mark it complete.
