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
