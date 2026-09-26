# Validation plan and evidence boundaries

Run commands and observed results belong in EXECUTION_STATUS.md and docs/VALIDATION_REPORT.md. This file defines checks; it does not claim they passed.

## Real database and API

Use a separate `counterpart_test` database, random schema per test, real Postgres transactions. No mocked SQL for consistency claims. Run spatial and temporal replay end to end through HTTP, queue claim, verification, state transition, publish, ZIP download, SHA-256 integrity. Prove a failed candidate preserves its accepted baseline. Retry the exact same request, then try reusing its key with different evidence. Race concurrent reservations and publications. Test invalidating an accepted candidate or baseline, leased work after restart, expired-heartbeat failure, old connection epochs, reconnect while the initial baseline is still being verified, and admission pressure.

## Domain evidence

Parameterized and property tests cover nonfinite confidence/boxes/timestamps; clipped/invalid boxes; multiple objects of a target class; insufficient detector confidence; missing observations; unchanged/ambiguous spatial relation; reference drift and scale changes; temporal gaps, overlapping arrival intervals, leave/reenter trajectories, different initial/final arrangements and interrupted recording. A fixture generating a positive example alone is insufficient; perturb individual controls to demonstrate refusal. These tests establish rule behavior, not real detector accuracy. Conservative thresholds remain uncalibrated until actual desk captures exist.

## Provider contract and actual provider smoke

Mock HTTP tests validate supported outbound protocol, header-only inference auth, one request per attempt, bounded response size, finite dimensions/geometry, timeout/error sanitation, duplicate/uncertain export handling and no raw keys in logs. A separately budgeted live test calls the real hosted detector and saves a sanitized report. A successful traffic-photo inference establishes endpoint/access/schema compatibility only; it cannot validate cup/bottle collection quality or temporal accuracy.

Dataset export requires an explicitly configured existing Roboflow project. Mocked export tests prove state-machine behavior, not external account entitlement. No claim of verified live export until both image/annotation receipts have actually been observed. No public project auto-creation.

## Browser and human capture

Build TypeScript and production assets. Playwright exercises the actual UI plus running API/worker: new replay, reject scene drift, successful pair, reload recovery, download, camera-denied error and small-screen layout. Use screenshots to inspect layout and readable text; don't rely solely on DOM assertions. Browser fake-camera tests, if used, must be labeled. Manual human desk capture is its own gate: a real cup/bottle scene, deliberate wrong relation and camera drift, restored baseline, live pair; then temporal opposite arrival order with honest rejection on occlusion. The agent cannot claim this human collection has happened without the physical scene.

## Infrastructure measurements

An explicitly labeled deterministic traffic replay can establish bounded pending previews, no starvation within stated admitted load, stale-response fencing and transactional recovery. Record offered workload, machine/Postgres versions, admission/rejection counts and actual measured timings. Model delay injected by a test is a workload parameter, never Roboflow latency. No unsupported claim of production scale or collector-efficiency gain.

## Security/budget

Session capability required for state, assets and downloads. Try a known foreign-session asset hash. Reject oversized/chunked multipart bodies, decompression bombs, EXIF rotations, wrong field names and client-supplied predictions. Secret scan all tracked candidates and web bundle. Verify cloud budget reservation races; exhausted budget performs zero model calls; replay performs zero model calls; lease loss stops remaining frame dispatch. No browser or test can turn a live provider failure into a synthetic success.
