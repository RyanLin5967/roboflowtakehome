# Camera orientation and first physical capture feedback — 2026-09-29

The user began the real-camera trial. The first spatial attempts had the bottle on
the right in the actual saved pixels; subsequent attempts correctly show it on the
left but remain uncertain under v2 background registration. For the inspected
left-side attempt, hosted observations include a high-confidence person box over
about 93% of the frame. The verifier excludes detected-object boxes from background
matching; its remaining 90 matches occupy only 0.0077 support area / two grid cells.
This is an observed usability limitation, not a completed positive physical trial.
No original image, detector result, recipe or threshold was changed.

The frontend now explains background repair using the corresponding saved frame's
detections. First-image advice can suggest reframing; counterpart/recovery advice
keeps the saved camera position. Original uncertainty and measurements remain in
the detailed checks. The setup screen also asks for visible fixed background detail.

New collections mirror front/unspecified webcams in BOTH preview and encoded
uploads; explicit rear cameras remain unmirrored. A Mirror camera control is
available before the first capture. Orientation is retained after any durable first
attempt, including a rejected/failed one that can later be retried or authorized.
Legacy attempts retain raw orientation. Pending-upload hydration precedes camera
startup; retry bytes and temporal sample orientation stay consistent. Metadata is
stored per session in this browser, not a cross-browser camera calibration contract.

All **40 browser tests passed** (1.0 minute), including nine dedicated camera tests
and the background-advice regression. TypeScript/Vite and diff checks passed. These
tests inspect generated asymmetric preview pixels and actual multipart JPEGs, not
model accuracy. Independent review found and resolved failed-attempt orientation
locking and contradictory counterpart framing advice. No new model calls were
made by the repair work; the user's captures remain in the existing durable ledger.

App still runs at http://127.0.0.1:8080 ; API PID 25954 / session 63806 and worker
PID 26433 / session 83880 were verified alive. The new production bundle is served;
refresh to load it. Existing captures keep their orientation; use New collection
to choose a different orientation. No remote push. Physical positive spatial and
temporal validation remain unfinished.

---

# Original-scope software closure completed — 2026-09-27 UTC

All seven audited software gaps are implemented and independently reviewed: frozen model/configuration contracts; append-only saved-evidence comparisons; immutable shared scene groups; measured bounded feedback scheduling; v2 registration/appearance/protected-object clauses; uncertainty review with separate baseline authority and human pair adjudication; Roboflow annotation review and hosted dataset-version receipts. The final comparison diagnostic preserves strict verifier output and exposes uncertain reference checks without inventing acceptance.

## Final executed evidence

- **1,043 Python tests passed**, zero skipped, 82.01 s. **30 browser tests passed**, 31.9 s, plus focused final API-field alignment. Ruff, TypeScript/Vite, wheel (all four SQL resources), diff checks and credential scans passed.
- Actual compiled UI/API/PostgreSQL/worker capture/product/evidence workflows and independent downloaded ZIP consumers passed. Original refusals remain unchanged; human-origin pairs require separate release approval; related sessions share a split.
- **50 real PostgreSQL load trials**, 100/100 committed captures observed, 2,800 independently recomputed checks. Stable-position FIFO is comparable; no universal scheduler win. Snapshot-reader p95 improved 1,594→475ms and1,495→432 ms with the active 250 ms condition; actual adaptive browser behavior is separately tested, not conflated with rendered latency.
- Actual worker death: 10 checkpointed frame observations reused with zero additional HTTP calls/reservations; seven other collectors progressed. Old harness was correctly refused for fake-provider pin mismatch, then repaired without weakening production checks. Historical migration and real backup/restore preserve all nine new feature tables.
- **Final rebuilt HTTPS container passed**: CA-verified TLS, secure cookies/security headers, actual v2 OpenCV/NumPy, revision idempotency, unchanged old evidence, worker kill/restart, full restart persistence,390 px Chromium comparison explanation and ZIP download. Disposable VM/containers/volumes/credentials removed; existing profile/native app preserved. `research/gap_deployment_probe.json` binds actual image and source hashes.
- **Modal actually used:** 184 GPU predictions over 58 public photos including 120 warm timings. 53/58 relations correct, 5 abstentions, 0 wrong answered; 25/29 joint pairs. Warm GPU p50/p95 23.30/24.85 ms excludes network/queue/coldstart. All 29 visual checks remained uncertain on low-texture backgrounds. One pre-inference archive-format failure is preserved; both apps stopped with zero tasks. Billing reads showed $0 billed; unsettled usage increment varied $0.02–$0.04. No local ML.
- **Roboflow actual transport:** corrected nonempty annotation readback, one reserved version-generation write, ready version 1 and genuine official COCO download verified. Ambiguous acknowledgement retained; no duplicate generation. Generated-card fixture only; production private-camera guards unchanged.

## Remaining boundaries

The user explicitly deferred physical-camera/temporal quality and collection-efficiency testing. Visual appearance checks do not prove physical identity; flat backgrounds can require human review. Current workspace still refuses private project creation with 422, so private camera export requires entitlement. Public hosting/certificate issuance, hosted S3-provider compatibility and remote GitHub CI are not executed. These are not secretly marked passed. No remote push was authorized for this new repository.

## Resume / running app

Work only in `/Users/idide/projects/roboflowtakehome`; this is Counterpart, separate from CoverageCV. App http://127.0.0.1:8080 ; workspace http://127.0.0.1:8080/#/workspace . Final native health 200; no pending jobs. API session 90628 / PID 69883, worker session 95114 / PID 96360. Verify liveness before starting duplicates. Durable hosted inference ledger 8/512; do not reset it. Provider credentials remain outside the repository.

`BUILD_LOOP.json` is complete for independently executable software gates. The unrelated native CoverageCV goal remains paused; do not alter it. Do not resume old experiments or resubmit saved provider calls. Read `docs/VALIDATION_REPORT.md`, `docs/ORIGINAL_SCOPE_AUDIT.md`, and the exact provider/load/deployment reports for evidence. Files persist; uninterrupted execution after session closure is not promised.

---

# Historical start of gap-closure loop — 2026-09-26

User explicitly requested fixing all original-scope gaps thoroughly, with independent attacks/review. Implementation is active across model pinning, scene grouping, immutable evidence revisions, uncertainty adjudication, bounded classical visual checks, comparative feedback benchmark, Roboflow annotation/version integration and UI. First integrated Python pass: 1,006 passed; 28 browser interaction tests and actual capture/product/new-gap browser flows passed. Final expanded suite, load matrix and HTTPS container validation are underway. Physical-camera trial remains deferred. No local ML. User explicitly reaffirmed plentiful Roboflow credits and authorized the remaining $3.39 Modal credits; a bounded $0.80 cloud-only GPU probe is now reserved/running. Current Modal API shows $26.61 metered and $0 billed; the prior user-set $0 spend limit is not readable from the Starter API. Real Roboflow generated-card annotation/readback and one hosted dataset version with verified official COCO download passed, preserving the ambiguous generation acknowledgement without a second write. See BUILD_LOOP.json; native goal remains unrelated paused CoverageCV.

---

# Correction: original-scope audit found remaining work — 2026-09-26

The user challenged the claim that everything from the original description was done. Root and two independent agents checked the original descriptions against source and evidence. **That completion claim was too broad.** The substantial tested build below is real, but does not cover every original capability. [Original-scope audit](docs/ORIGINAL_SCOPE_AUDIT.md) is the current authority for remaining work.

Concrete infrastructure gaps: pin model/configuration to the collection/session contract (current workers can change it), append new decision revisions when saved evidence is re-verified, group related sessions in dataset splits, and measure feedback age/p95 versus a bounded-queue baseline under increasing load. Broader camera registration/object-identity/distractor checks and Roboflow annotation/version integration were narrowed or omitted. The manual physical trial remains deferred by the user. No application fixes or new provider calls occurred during this audit. Preserve existing completed test evidence; do not reinterpret these gaps as failed execution of tests that passed.

---

# Previous completion checkpoint (scope claim superseded above)

The user requested the complete product, then explicitly instructed us to do everything except their manual physical-camera trial. The operator→collector→repair→review→dataset-release→offline-consumer product and its reliability/security/storage/deployment work are implemented and tested. `BUILD_LOOP.json` records completion of all independently executable gates. Do not restart ideation or resume the unrelated paused CoverageCV goal.

## Executed final checks

- **752 Python tests passed**, zero skipped, with the real PostgreSQL test DSN.
- **17 browser interaction tests passed**; production TypeScript/Vite build, Ruff, Python wheel and diff checks passed.
- Actual API/Postgres/worker browser scripts passed both spatial and temporal recipes: separate collectors, incomplete-session inspection, rejection/repair/reload, human review, release/download and 390 px layout. Both downloaded fixture releases passed the independent offline consumer.
- Actual worker-death probe: 8 synthetic collectors/2 campaigns; 85 previews coalesced to 8 pending, all latest frames served. Seven other collectors progressed while the dead worker's lease remained. Ten durable frame observations survived process death with zero additional fake HTTP calls/reservations.
- S3 HTTP emulator concurrency/corruption/lost-response checks and real pg_dump/pg_restore of a complete pair, review, release, capability and usage ledger passed.
- **Actual isolated container deployment passed**: locked image built, PostgreSQL/API/worker booted, authenticated collection/review/release worked, worker SIGKILL and full service restart preserved evidence and queued work. Real Chromium at 390 px rendered the compiled app and downloaded the 71,123-byte dataset without errors or horizontal overflow. No provider keys or model calls in this test. See `research/deployment_probe.json` for exact image/source identities.
- Disposable `counterpart-test` containers, volumes, VM and generated credentials/tools were removed. Existing stopped `dbprobe` profile and native app on port 8080 were left untouched.
- Actual hosted six-photo diagnostic: 6/6 published source spatial relations, 3/3 invalid moved-reference pairs refused. The durable dev budget is 8/512 image reservations. This is limited perception evidence, not a physical collection study. No local ML or Modal used.

## Deferred and external checks

1. **Physical camera trial explicitly deferred by the user.** Positive fixed-reference and temporal collection quality remain unmeasured. Do not infer them from fixture tests or manufacture evidence. The frozen independent evaluation protocol and harness are ready for later.
2. Private Roboflow camera export: the existing workspace is public-only. The generated-card integration project is test-only, and automatic camera export stays disabled. Reviewed local ZIP releases work; the private exporter and reconciliation path are implemented and contract-tested.
3. Public hosting/TLS issuance and remote GitHub CI have not run. No push/public deployment was requested for this new repository. Hosted S3-provider compatibility is untested; the actual local HTTP emulator passed. Deployment/operations runbooks are delivered.

## Resume and live processes

Repository `/Users/idide/projects/roboflowtakehome`, origin `RyanLin5967/roboflowtakehome`. Current app: http://127.0.0.1:8080 ; operator workspace: http://127.0.0.1:8080/#/workspace . API exec session 10118 and worker session 15270 were running at the final checkpoint. Verify actual liveness before starting duplicates; process IDs/session handles can expire. `uv run counterpart serve` and `uv run counterpart worker` restart the services. Provider credentials remain outside the repository; nonsecret development overrides are at `~/.config/counterpart/runtime.json`.

The full implementation checkpoint is `129d527`. The following checkpoint adds actual container evidence and current completion records, without application behavior changes. All agent ownership has returned to root. Current architecture/protocol, operations/deployment, dataset format and a seven-minute demonstration plus Q&A are documented. Sanitized evidence is under `research/`; raw browser artifacts are ignored under `artifacts/`.

Native goal creation was refused because an unrelated CoverageCV goal remains paused. The separate Counterpart loop is now complete within the user's updated scope. Do not alter the old goal or promise execution/process survival after the session closes.

---

## Historical initial kernel record (superseded by the full-product checkpoint above)


# Counterpart execution status

2026-09-26 — working implementation built and verified. This is the selected NEW Roboflow Labs takehome, separate from CoverageCV in ../coolcvproject. User authorized reuse research, planning, implementation/testing, and choosing/creating the Roboflow destination. Do not resume CoverageCV training or earlier rejected ideation.

## Implemented

- React/TypeScript desktop/mobile app: spatial and opposite-arrival temporal recipes, live/replay modes, readable repair feedback, protected evidence and detection overlays, prefetched timestamp-aware playback, durable browser retry payloads.
- FastAPI and PostgreSQL: immutable media, capabilities, idempotency, revision/epoch fences, scene re-grounding, latest pending previews, four current capture leases globally, bounded retries and cloud-call reservations.
- Separate worker, hosted RF-DETR Small (`coco/39`), conservative versioned verifier, original-frame archive, VQA JSONL and pair manifest with session-level split.
- Atomic local publication and low-priority Roboflow export: persisted phases, fenced receipts, bounded read-only reconciliation (2/10/30/120s) for delayed search visibility. Ambiguous annotations stop for reconciliation.
- Research, architecture/protocol/validation/review records, reproducible probes and CI configuration. No remote CI run claimed before push.

## Executed evidence

- `COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest -q --tb=short`: **254 passed**, no skips; one upstream Starlette HTTPX deprecation warning. Real DB races, generated faults, actual abruptly exited worker subprocesses, mocked provider transport, PNG/security/budget/export tests.
- `npm test` in web: **9 passed** with mocked API and native generated camera input. No model calls. Covers repair/recovery/download, mobile layout, camera refusal, immutable lost-upload retries, temporal sampling/interruption and stale-epoch retake.
- TypeScript/Vite build, Ruff and Python wheel passed; wheel includes schema.sql.
- Actual browser + running API + separate worker walkthroughs passed for spatial/temporal with synthetic observations: deliberate rejection, reload/recovery, complete pair, download, playback; no page errors/390 px overflow. Reproduce with `node scripts/browser_smoke.mjs`; artifacts are ignored.
- Hosted provider: first wrong-route request failed404; official SDK V0 `/coco/39` with header auth/raw base64 passed on a traffic photo (52 detections,1280x827). **Two images reserved total**, including failed attempt. Not desk-scene accuracy or general latency evidence.
- Infrastructure replay:240 previews across12 sessions coalesced to12 pending; all12 latest previews served; expired-owner result refused, replacement accepted original capture. No real-collector/model/capacity claim. Reproduce via `scripts/reliability_probe.py` in a temporary test schema.
- Actual public generated-card upload, empty VOC prediction acknowledgement and eventual exact metadata/search reconciliation passed. Transport probe only, not a captured pair or independent annotation readback.

## Providers and budget

Credentials stay in `~/.config/coveragecv/credentials.json` or environment. Authenticated workspace has no pro/enterprise/trial flag and over_quota=false. Usage API is unavailable (folder billing required). Current official docs say free tier excludes Flex billing. No paid upgrade made.

Private nonsecret runtime override `~/.config/counterpart/runtime.json` enables hosted inference with **512 total image reservations** (about0.096 Roboflow credits at documented0.1875/1000). Portable defaults remain live=false/128. Reservations persist in counterpart_dev; never reset DB to bypass allowance. Ambiguous attempts stay counted. Modal user-reported balance$3.39; current spend-limit reconfirmation was not supplied and no Modal work was started. Hard constraints remain $0 out of pocket and no local ML.

Private project creation refused422: workspace only permits public projects. User delegated setup, so created **Counterpart Integration Test**, slug `counterpart-integration-test`, MIT, solely for an explicitly marked generated card. URL: https://app.roboflow.com/ryan-lin-khj4s/counterpart-integration-test . **Do not enable automatic camera export to this project.** Camera export stays disabled; private destination needs provider entitlement or a deliberate future public-demo decision. Live inference and local pair downloads work.

## Run / restart

Workdir `/Users/idide/projects/roboflowtakehome`; origin is RyanLin5967/roboflowtakehome. No remote push authorized/performed in this build turn.

Postgres17.11 service already existed. Created only counterpart_dev and counterpart_test; tests use temporary schemas. Commands:

```
uv sync --locked
npm --prefix web ci
npm --prefix web run build
uv run counterpart init
uv run counterpart serve
# second terminal:
uv run counterpart worker
```

App: http://127.0.0.1:8080 . Latest API PID43165/exec session62484; worker exec session93750. Identifiers may expire: check liveness before restart and never promise process survival after session closure.

## Remaining evidence / limits

**Human desk capture validation is required.** No real paired cup/bottle images or temporal clips have been collected by the user in this session. Thresholds are engineering defaults, not calibrated accuracy. No CV benchmark, collection-speed gain or proof of all physical controls is claimed. Browser media is not cryptographic capture attestation.

Localhost demo topology, shared durable media directory and capability access; not a hardened public multi-tenant deployment. Hosted deployment needs actual shared storage, admission/auth and retention policy. No personal camera export or paid upgrade occurred.

Next useful action: use the real camera with a cup and bottle; validate positive/negative spatial scenes, then temporal clips. Preserve raw failures and do not loosen thresholds solely to make the demo pass. Build/replay demonstration is runnable now.
