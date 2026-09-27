# Original-scope closure verification — 2026-09-27 UTC

All seven audited implementation categories now have code, operator UI and consumer support. The final Python source passed **1,043 tests, zero skipped, in 82.01 seconds**. The browser suite passed **30 tests in 31.9 seconds**, including the measured adaptive-polling fix. The final rebuilt HTTPS container passed and its disposable VM, containers, volumes and credentials were removed. See [scope audit](ORIGINAL_SCOPE_AUDIT.md) for the feature-to-evidence mapping.

Actual compiled UI/API/PostgreSQL/worker walkthroughs passed both existing recipes and the new saved-evidence flow: append a v2 comparison without changing the original refusal, record explicit human adjudication, reject a known failed control, separately approve and release, download two related sessions in one split, and validate the ZIP with the offline consumer. All three downloaded releases passed independently. Mobile screenshots were inspected; no overflow or browser errors were observed. These are generated workflow fixtures with scripted reviews, not physical-quality evidence.

The original process-failure harness was also rerun on current source. It first failed because its fake provider did not match the newly required session contract; the worker correctly refused it. After explicitly pinning the trusted fake HTTP fixture and using the same runtime identity, the full probe passed in **3.92 seconds**: seven other collectors progressed during a dead worker's lease; ten checkpointed observations survived actual exit 73 with zero additional HTTP calls or reservations. Production pinning was unchanged. [Current-source fault report](../research/product_fault_probe.gap.json).

New independent attacks found and fixed dropped protected-object declarations, cached-model drift, hidden known failures after early uncertainty, stale baseline authority and forged removal of cross-epoch recovery. Historical migration and real `pg_dump`/`pg_restore` preserve all nine new feature tables and immutable records. See [migration drill](GAP_MIGRATION_EVIDENCE.md), [evidence rules](EVIDENCE_REVISIONS.md) and [frozen execution](plans/EXECUTION_CONTRACT.md).

Real Roboflow validation now includes nonempty annotation readback and **one reserved dataset-generation write**, followed by a ready version and genuine provider-downloaded COCO verification. The initial acknowledgement remains recorded as uncertain; no second generation request was sent. Exact fixture membership, split, dimensions and corrected boxes match. Private-project creation still returns 422, so this proves generated-card public transport, not camera export to a private project. [Provider evidence](../research/roboflow_version_probe.json).

The Modal L4 run completed **58 public photos: 53 correct relations, five abstentions, zero wrong answered relations; 25/29 pairs jointly correct**. It executed 184 model predictions, including 120 warm timing samples (p50 23.30 ms, p95 24.85 ms). Those timings exclude cold start, upload, network and queueing. Only the six previously tested photos use product-supported role combinations; the other 52 extend detector diagnostics. All 29 scene-check diagnostics remain uncertain on low-texture backgrounds. No positive physical admission claim follows. One archive-format failure before inference is retained, and both apps stopped with zero tasks. Post-run metered reads showed increases of $0.04 and then $0.02, with $0 billed at both reads; those estimates remain unsettled. [Cloud validation](MODAL_VALIDATION.md).

The original 36-trial feedback matrix and 14-trial follow-up completed against real PostgreSQL with synthetic service delays. All **100/100 committed captures** persisted and were observed; independent raw-trace recomputation matched **2,800 checks**. Stable-position FIFO performs comparably, with mixed results; no universal scheduler advantage is claimed. The measurement exposed and led to an adaptive UI polling fix. In the fixed-cadence snapshot-reader sensitivity, p95 fell from **1,594→475 ms** at 16 previews/s and **1,495→432 ms** at 60/s. Those reads exclude HTTP/rendering and do not model the entire adaptive browser state machine, whose transitions were tested separately. See [benchmark report](FEEDBACK_BENCHMARK.md).

The final Linux/arm64 HTTPS image exercised all nine new feature tables, actual OpenCV/NumPy execution, saved-evidence comparison and idempotency, unchanged original capture/release hashes, worker SIGKILL, queued-work recovery and complete service restart. Real Chromium on a non-localhost HTTPS hostname verified secure HttpOnly/Strict cookies, security headers, generated camera plumbing, the new comparison explanation and baseline checks, and a mobile ZIP download without errors or overflow. Only the ephemeral test certificate was trusted; global TLS errors were not ignored. Disposable resources and credentials were removed. [Final container evidence](../research/gap_deployment_probe.json). This is not a physical-camera trial or public certificate issuance.

Remaining external boundaries are unchanged: the user deferred physical-camera testing; private Roboflow camera export requires entitlement; hosted S3 compatibility, public hosting/TLS issuance and remote GitHub CI have not been executed. A local TLS test is distinct from a public certificate deployment.

---

# Historical pre-audit product verification — 2026-09-26

**Scope correction:** these executed tests cover the implemented product. A subsequent [audit against the original proposal](ORIGINAL_SCOPE_AUDIT.md) found remaining capabilities and infrastructure work; the passing counts below do not establish full original-scope completion.

The complete collection→capture/repair→review→release→offline-consumer workflow is implemented. The following are executed checks, not planned gates. Machine perception, synthetic tests and physical validation remain separate.

| Layer | Current executed result | Scope |
|---|---|---|
| Python/database/adapter/consumer suite | **752 passed, zero skipped**,42.22 s | Real PostgreSQL concurrency/faults, S3 HTTP emulator, actual backup/restore, security, recipes, export, archive integrity |
| Browser interaction suite | **17 passed**,15.3 s | Real Chromium, mocked APIs; camera/persistence/operator UI contracts |
| Full-stack capture + full-product browsers | Both recipes passed both scripts | Actual API/Postgres/worker; labeled fixture perception; separate collectors, repair/reload, incomplete-session inspection, review, release/download, mobile fit |
| Portable release consumer | Both actual browser downloads validated | Full frozen records, exact JSONL, source hashes, dimensions, split/review/recipe integrity; explicit fixture opt-in |
| Process-failure probe |8 collectors,2 campaigns;85 previews→8 pending→8 latest served | Actual worker deaths with synthetic observations;7 other collectors progressed;10 checkpointed frames recovered with zero extra HTTP calls/reservations |
| Build | Ruff, TypeScript/Vite, Python wheel, diff checks passed | Wheel includes both SQL schemas; remote CI not yet run |
| Actual container deployment | Locked image build, authenticated product flow, worker kill, full service restart and Chromium passed | Isolated 2-CPU/2-GiB VM; original archive hashes preserved; 390 px browser without errors; zero provider calls; disposable environment removed |
| Hosted tabletop diagnostic |6/6 source relations;3/3 invalid moved-reference pairs refused | Six preselected real photos via Roboflow; no threshold tuning or physical collector-quality claim |

[Machine-readable software record](../research/software_validation.json), [container evidence](../research/deployment_probe.json), [latest fault report](../research/product_fault_probe.json), [earlier fault run](../research/product_fault_probe.initial.json). The last two fault runs took3.367 s and3.017 s on the documented machine; these are bounded correctness probes, not throughput or CV benchmarks.

Final integration review reproduced and fixed: partial selected export admission, ZIP caps excluding the central directory/footer, disabled-live mode hiding an existing invitation receipt, paused uploads unnecessarily writing orphan media, stale optimistic reviews, and old publication intents that could otherwise export historical camera captures. Database restore preserved a complete pair, human-review record, immutable release, capability and usage ledger.

**Deferred by the user:** actual physical collector yield/time, positive live fixed-anchor pair quality and temporal camera quality. **External checks not executed:** camera dataset export to a real private project, hosted S3 compatibility, public TLS and remote CI. The current account's tested project is public and cannot receive automatic camera exports; local reviewed releases remain usable. No Modal compute or local model work was performed. The durable hosted image ledger remains8/512. The container gate used fixture perception and establishes deployment/recovery correctness, not camera accuracy.

The existing Starlette test-client deprecation and a deliberately malformed ZIP fixture warning are nonfailing; no tests were omitted.

---

# Initial checkpoint verification — 2026-09-26

| Layer | Executed result | What it establishes |
| --- | --- | --- |
| Python/database/provider-contract suite |254 passed, no skips|Rules, actual DB transactions/races, interrupted subprocess recovery, budgets, capabilities, HTTP adapters and export state-machine faults|
| Browser suite |9 passed|UI, native generated-camera sampling, interruption, persistent retry identity; APIs mocked|
| Full-stack browser |Spatial and temporal walkthroughs passed|Production UI, actual API/worker/Postgres, synthetic observations; repair, reload recovery, publication and ZIP|
| Build/lint |TypeScript/Vite, Ruff, wheel passed|Buildable source; SQL schema in wheel|
| Hosted RF-DETR |One failed wrong-route request, one corrected success|Key, route/auth/schema compatibility on traffic photo; not cup/bottle quality|
| Dataset transport |Generated card upload, empty prediction acknowledgement, eventual metadata reconciliation|Real account write/search behavior in public test-only project; not a real collected pair|
| Infrastructure replay |240 previews→12 pending→12 latest served; orphan lease recovered, old result refused|Bounded burst coalescing/recovery without provider calls; not sustained capacity or collector efficiency|

The single successful inference took7.349s including network/startup effects; not a latency benchmark. The local replay measured median claim27.963ms/max40.873ms under its tiny stated workload. Its12 sessions are synthetic traffic.

One upstream Starlette warning concerns future HTTPX test-client migration. Browser tooling emits NO_COLOR/FORCE_COLOR notices. No failing tests were omitted.

With API+worker running:

```
node scripts/browser_smoke.mjs
uv run python scripts/reliability_probe.py
```

`scripts/provider_smoke.py` invokes one real hosted inference only with live mode and a durable allowance configured; excluded from CI. Default automated tests never call models. Raw screenshots/probe reports are ignored under artifacts/; this summary is durable documentation. CI is configured, not yet executed remotely.

Missing: physical collector trials, threshold calibration on desk scenes, measured CV accuracy, real temporal quality, human-reviewed labels, real camera-pair export and public deployment validation. Separate gates from software/replay correctness.

## Actual tabletop-photo diagnostic — 2026-09-26

Executed `uv run python scripts/probe_real_photos.py` against six preselected original What's-up Controlled-B photos using hosted Roboflow `coco/39` through the production durable inference runtime. The selection, original SHA256s, expected relations, model identity and verifier thresholds were saved before dispatch. All six provider results, including full normalized detections and refusal evidence, are preserved in [real_photo_probe.json](../research/real_photo_probe.json).

| Check | Observed count | Scope |
|---|---:|---|
| Actual hosted observations | 6 / 6 photos | Every selected original image; no retries |
| Left/right relation agrees with published source caption | 6 / 6 photos | Book/cup, mug/book and mug/phone; explicit mug→cup and phone→cell phone ontology mapping |
| Moved reference prevents pair acceptance | 3 / 3 pairs | Both objects swap sides; verifier refused each with `reference_moved` |
| Durable image reservations | 6 new | Existing account ledger increased from 2 to 8 of the unchanged 512-image allowance |

The right-side photos yield `wrong_relation` when submitted to the **baseline** diagnostic, because the baseline asks for left; their recorded observed relation is nevertheless right. Full-pair evaluation uses the proper left reference/right counterpart and correctly refuses the moved reference. No failures were hidden and no thresholds or source photos were changed.

This adds real tabletop perception and control-refusal evidence. Six examples from one public source scene do not establish general accuracy, successful fixed-anchor collection, temporal verification, physical collection efficiency or independent human review. Original photos remain untracked local artifacts; [source provenance and suitability](../research/physical_sources.md) are recorded separately.

The offline independent-review evaluator passes **28 focused arithmetic/provenance tests**. It reports exact denominators, preserves uncertain/missing judgments, refuses fixture provenance and calibration leakage, suppresses recall/coverage for published-only input, and supports matched competent post-hoc decisions. These are software tests, not 28 physical trials. [Physical validation protocol](plans/PHYSICAL_VALIDATION.md) defines the remaining real collection study.

## CI toolchain correction

The workflow explicitly installs PostgreSQL17 client tools for the real backup/restore test. GitHub's current Ubuntu24.04 image ships PostgreSQL16, which must not be assumed compatible for dumping the newer test server. Sources: [runner image inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md), [official PostgreSQL Ubuntu packages](https://www.postgresql.org/download/linux/ubuntu/). The workflow is configured; remote CI has not been run.
