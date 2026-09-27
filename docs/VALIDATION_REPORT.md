# Full-product verification — 2026-09-26

The complete collection→capture/repair→review→release→offline-consumer workflow is implemented. The following are executed checks, not planned gates. Machine perception, synthetic tests and physical validation remain separate.

| Layer | Current executed result | Scope |
|---|---|---|
| Python/database/adapter/consumer suite | **752 passed, zero skipped**,42.22 s | Real PostgreSQL concurrency/faults, S3 HTTP emulator, actual backup/restore, security, recipes, export, archive integrity |
| Browser interaction suite | **17 passed**,15.3 s | Real Chromium, mocked APIs; camera/persistence/operator UI contracts |
| Full-stack capture + full-product browsers | Both recipes passed both scripts | Actual API/Postgres/worker; labeled fixture perception; separate collectors, repair/reload, incomplete-session inspection, review, release/download, mobile fit |
| Portable release consumer | Both actual browser downloads validated | Full frozen records, exact JSONL, source hashes, dimensions, split/review/recipe integrity; explicit fixture opt-in |
| Process-failure probe |8 collectors,2 campaigns;85 previews→8 pending→8 latest served | Actual worker deaths with synthetic observations;7 other collectors progressed;10 checkpointed frames recovered with zero extra HTTP calls/reservations |
| Build | Ruff, TypeScript/Vite, Python wheel, diff checks passed | Wheel includes both SQL schemas; remote CI not yet run |
| Hosted tabletop diagnostic |6/6 source relations;3/3 invalid moved-reference pairs refused | Six preselected real photos via Roboflow; no threshold tuning or physical collector-quality claim |

[Machine-readable software record](../research/software_validation.json), [latest fault report](../research/product_fault_probe.json), [earlier fault run](../research/product_fault_probe.initial.json). The last two fault runs took3.367 s and3.017 s on the documented machine; these are bounded correctness probes, not throughput or CV benchmarks.

Final integration review reproduced and fixed: partial selected export admission, ZIP caps excluding the central directory/footer, disabled-live mode hiding an existing invitation receipt, paused uploads unnecessarily writing orphan media, stale optimistic reviews, and old publication intents that could otherwise export historical camera captures. Database restore preserved a complete pair, human-review record, immutable release, capability and usage ledger.

**Still unverified:** actual physical collector yield/time, positive live fixed-anchor pair quality, temporal camera quality, camera dataset export to a real private project, container boot, public TLS and remote CI. The current account's tested project is public and cannot receive automatic camera exports; local reviewed releases remain usable. No Modal compute or local model work was performed. The durable hosted image ledger remains8/512.

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
