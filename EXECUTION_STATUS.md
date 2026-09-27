# Counterpart full product: software verified; external gates open — 2026-09-26

User explicitly required the whole product and a persistent loop. The initial kernel was not completion. This expansion now implements the complete operator→collector→repair→review→dataset-release→offline-consumer path plus the necessary reliability/security/storage/operations. Follow `BUILD_LOOP.json` for the remaining gates; do not restart ideation or confuse the old CoverageCV goal with Counterpart.

## Executed final checks

- **752** Python tests passed with COUNTERPART_TEST_DSN set; zero skipped.
- **17** browser interaction tests passed; production TypeScript/Vite build, Ruff, wheel and diff checks passed.
- Actual API/Postgres/worker browser scripts passed both spatial and temporal: separate collector contexts, incomplete session retained, rejection/repair/reload, human-review action, release/download,390 px layout. Both downloaded fixture releases passed independent offline integrity validation.
- Actual worker-death probe:8 synthetic collectors/2 campaigns,85 previews coalesced to8 pending;7 others progress while deadworker lease lives;10 durable frame results recovered with zero extra fake HTTP requests/reservations.
- S3 HTTP emulator concurrency/corruption/lost-response tests and actual pg_dump/pg_restore of a complete pair/review/release passed. Docker configuration was parsed; daemon unavailable, so build/boot/TLS not claimed.
- Actual hosted six-photo probe:6/6 published source spatial relations,3/3 invalid moved-reference pairs rejected. Dev budget ledger 8/512. This is not general accuracy or a physical collection trial. No local ML or Modal used.

## Remaining external evidence/access

1. Real physical collector trial/positive fixed-reference and temporal quality: user has been asked to try the running camera flow. Keep all attempts and use frozen independent protocol; do not manufacture evidence or infer quality from fixtures.
2. Private Roboflow camera export: this workspace is public-only. Public generated-card integration project remains test-only; automatic camera export stays disabled. Local reviewed ZIPs work.
3. Actual container boot/publicTLS needs Docker daemon/hosting; no public deployment or GitHub push authorized for this new repo. Remote CI configured, not run.

## Resume and live processes

Repository `/Users/idide/projects/roboflowtakehome`, origin `RyanLin5967/roboflowtakehome`. API at http://127.0.0.1:8080, workspace /#/workspace. API/worker were running at this checkpoint; verify before starting duplicate processes. `uv run counterpart serve` / `uv run counterpart worker` restart them. Credentials remain outside repository in the existing private JSON, nonsecret runtime settings at ~/.config/counterpart/runtime.json.

All agents' file ownership has returned to root after integration. Full current architecture/protocol, operations/deployment, dataset format and seven-minute demonstration plus Q&A are documented. Sanitized machine-readable evidence is under research/. Raw browser media/reports in ignored artifacts/.

Native goal creation was refused because an unrelated CoverageCV goal remains paused. BUILD_LOOP.json tracks this Counterpart loop; it cannot guarantee execution after the session closes. Never falsely finish/resume the old goal.

---


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
