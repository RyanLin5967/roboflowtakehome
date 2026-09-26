# Independent adversarial review

2026-09-26. Review owner: `counterpart_architecture_review`. This is a review of the selected product and implementation, not another ideation round. The examples below are concrete acceptance criteria. A criterion is **unverified** until a named executable check or observed result is recorded. No cloud model call or human capture was performed by this reviewer.

## Product boundary that must remain explicit

The useful capability is repairing a paired collection task while the collector can still change the scene, and preserving both evidence and correctness across failures. Queues and pair manifests support that capability; they are established techniques, not a novelty claim. The current detector can check designated 2-D relationships, scale and observed arrival intervals. It cannot establish physical causality, object identity through arbitrary swaps, or that an entire scene stayed unchanged. In particular, cup position and size can remain constant after camera/object co-movement, and two same-class bottles can be exchanged. The UI, manifest and presentation must not upgrade the measured controls into stronger claims.

## Release-blocking failure cases

### R1 — Baseline replacement must invalidate dependent evidence

**Counterexample:** accept baseline A, accept counterpart B, then replace baseline with C. If the previous counterpart pointer or `ready` state survives, publishing can pair C with B even though B was verified against A. The same problem occurs when a baseline replacement is queued while publication races.

**Required behavior:** a committed replacement advances the revision and atomically clears dependent acceptance/publication eligibility, or replacement is explicitly forbidden after the first accepted baseline. Rejected counterpart attempts must preserve the accepted baseline. Published records are immutable; a replacement after publication needs a new session/record or is rejected. Store the exact baseline capture ID in counterpart verification provenance.

**Status:** fixed behavior confirmed by `test_new_baseline_never_reuses_old_counterpart`: queued replacement cannot publish; accepted replacement clears counterpart and readiness. Exact counterpart baseline-binding provenance still merits an explicit manifest field; current immutable session/revision transitions prevent replacement mixing.

### R2 — A heartbeat cannot resurrect an expired worker

**Counterexample:** worker A reserves/executes frame 1, stalls, lease expires, worker B claims, A resumes and schedules the remaining 47 frames. Fencing only `finish()` protects acceptance but leaves wasted spending and avoidable duplicate inference. If heartbeats extend an already expired lease, A may also regain authority before B claims.

**Required behavior:** heartbeat requires matching token **and an unexpired lease**. Losing heartbeat/claim authority stops scheduling new provider calls; already in-flight calls remain ambiguous and charged. Every potential invocation gets a distinct durable reservation, including retry after process death. Check claim eligibility again before beginning a new image, not only once at job start. No guarantee of canceling already dispatched remote work.

**Status:** `test_expired_lease_cannot_be_revived_even_before_reclaim` passes against PostgreSQL. Worker inspected: checks heartbeat before every frame and reserves the entire sequence under a unique lease key. It conservatively retains unused reservations when interrupted. A fully hung remote call can still outlive the lease; this is explicitly not exactly-once compute.

### R3 — Stale results must be inert in every transition

**Counterexample:** submit candidate A, resume to a new epoch or replace candidate with B, then return a successful result for A. A stale `finish` must not clear a recovery flag, overwrite feedback with an old frame, restore `ready`, or accept an earlier candidate. A late `fail` must not fail a newer attempt either.

**Required behavior:** eligibility is the conjunction of lease token/expiry, capture identity, applicable durable revision, operation state and (where scene-dependent) epoch. Stale results may leave an audit record but never mutate current acceptance. Committed immutable evidence should not be needlessly lost merely because a preview is superseded. Old expected epochs are retryable resume requests; future expected epochs must conflict.

**Status:** found and fixed a real edge case: resume while the *first* baseline was still running initially left recovery disabled when that old-epoch baseline finished. `test_resume_during_first_inference_preserves_baseline_but_requires_regrounding` now passes. `test_old_recovery_result_cannot_unlock_new_epoch` also passes. Immutable committed captures survive epoch changes; live scene-dependent recovery does not.

### R4 — Temporal labels must be supported by the observations

**Counterexample:** objects begin inside the target band, leave and reenter; alternatively both cross during the same unobserved gap; alternatively one enters, leaves and enters after the other. A routine that chooses the last or most convenient threshold crossing can manufacture an opposite-order label. A stalled camera can return different timestamps with identical pixels.

**Required behavior:** define the event as a qualified first observed arrival from a stable outside state, with a stable inside final state and explicit allowed trajectory. Require enough observations, bounded gaps, nonoverlapping event uncertainty intervals and sustained arrival; reject interruptions/ambiguous trajectories. Do not claim exact motion onset or continuous observation between samples. Server-generated hashes and observation timestamps must remain attached to every decision. Browser timestamps are collector evidence, not trusted capture attestation.

**Status:** independent synthetic observations pass tests for leave/reenter rejection, large-gap rejection, overlapping-arrival abstention and correctly opposed intervals. These are verifier semantics tests. Real detector/temporal quality remains a separate human-video validation gate.

### R5 — Reconnect must not make the temporal task impossible

**Counterexample:** baseline clip finishes with both objects inside the target band. After reconnect, the collector resets them outside to record the reverse-order clip. If recovery demands agreement with the baseline's final object boxes, it rejects the reset that the task requires. Conversely, blindly clearing `recovery_required` after any detection is not a continuity check.

**Required behavior:** state explicitly which controls the recovery observation checks and which movable-object changes are required by the recipe. A temporal starting arrangement should be compared with the intended initial reference when applicable. Do not claim camera invariance from movable objects alone. A completed pair needs no scene check merely to download existing evidence.

**Status:** verifier permits deliberate starting-position reset while checking scale and vertical lanes; `test_temporal_recovery_allows_deliberate_start_position_reset` passes. UI owner was asked to correct recovery copy that demanded keeping the cup at its last saved position. Actual physical continuity remains a measured-controls claim.

### R6 — Budget reservation and claim authority are different identities

**Counterexample:** a retried worker reuses `request_key=job_id:frame_index`; the ledger returns true because it was already reserved, then the provider is called again for free in the local accounting. Concurrent workers can exceed a cash-safe allowance despite apparently correct ledger totals.

**Required behavior:** reservation identity names an individual possible external invocation, not a logical frame. An idempotent replay of a reservation is not authorization to make another request unless a durable dispatch protocol proves no prior invocation was possible. Bound total image calls before dispatch; ambiguity stays counted. Tests must race reservations and simulate crash between reservation, HTTP response and DB completion. Provider-level hard stops still bound exposure from other clients/account activity.

**Status:** `test_budget_race_respects_global_limit_and_keeps_retries_charged` executes 24 concurrent reservations against a limit of five; exactly five succeed. Worker inspected: unique lease key reserves the whole sequence, including repeated calls after worker replacement. Mocked live end-to-end test confirms two submitted images use exactly two reservations and request retries do not invoke the provider again. Provider-level cash controls remain a separate operational requirement.

### R7 — Assets require session membership, not just a valid capability

**Counterexample:** collector A learns/guesses B's SHA-256 asset ID and requests `/sessions/A/assets/B_hash` using A's valid token. Global content-addressed storage makes the file available unless the route independently checks A's capture membership. ZIP/manifest endpoints can leak the same way if they accept arbitrary IDs or paths.

**Required behavior:** session auth plus relational asset membership before any read; reject malformed asset IDs; never accept filesystem paths/URLs from the browser. The same isolation applies to export and event routes. Test with two real sessions and an intentionally known foreign asset hash; the threat is authorization correctness, not hash unpredictability.

**Status:** API inspected: media reads require membership in the authenticated session's baseline/counterpart/latest capture or published manifest. Root reported passing cross-session media tests in `tests/test_api.py`; this reviewer did not duplicate that suite.

### R8 — External upload ambiguity cannot become silent duplication

**Counterexample:** Roboflow accepts the first image but its HTTP response times out; the worker crashes. Blind retry creates a duplicate, while treating the timeout as failure refunds imaginary budget. A second case marks the pair exported immediately after creating an upload intent, though only one side arrived.

**Required behavior:** durable per-asset export intents/receipts, distinct unknown outcome, bounded provider-specific reconciliation and explicit incomplete state. Mark remote completion only after every required receipt. Local complete publication remains usable while export is unresolved. Prevent deterministic replay fixtures from entering live export. Do not promise cross-service atomic publication or exactly-once provider uploads.

**Status:** adapter reviewed; timeouts are ambiguous and nonretryable for writes, reconciliation requires exact asset/hash metadata, duplicate uploads do not automatically confirm metadata. Review found unexpected mutation 201/202/204 responses were incorrectly non-ambiguous; provider owner corrected them and added tests. Durable exporter integration/recovery remains unverified here. If reconciliation cannot establish the receipt, retain uncertainty and do not auto-retry.

### R9 — Coalescing the queue alone does not prove fairness

**Counterexample:** one session continuously submits new previews; an oldest-job scheduler continually serves it while another session waits. Conversely, an endless stream of committed jobs can starve all previews under strict priority. Finished/superseded preview rows can grow without bound even when pending depth is one.

**Required behavior:** measure per-session service interval and useful feedback age under a stated admitted workload. Make global/per-session admission and the priority policy explicit. Disclose that finite capacity cannot guarantee feedback latency for unbounded offered load. Track retained history growth or bound its retention. Replay traffic is simulated load, not live collector count.

**Status:** review found expired/revision-stale previews were dispatched and rejected only after costly processing. Infra owner added claim-time pruning; `test_expired_preview_is_discarded_before_dispatch` and `test_preview_from_replaced_revision_does_not_dispatch` pass. `test_preview_results_remain_useful_when_newer_preview_is_pending` checks that continuous submission does not invalidate every in-flight result. Empirical scheduler comparison, latency distributions and retention limits remain separate work; no cloud-scale fairness result is claimed here.

### R10 — Test doubles cannot silently approve real evidence

**Counterexample:** a malformed live response triggers a fallback fixture; the UI shows a successful pair and exports it as Roboflow verified. Another case lets a browser post `fixture_detections` directly, preserving an authentic model ID while replacing its observations.

**Required behavior:** mode/provenance determined server-side and immutable per session; strict schemas disallow client predictions; no fallback from live failure to fixture success. Fixture UI and manifests explicitly identify deterministic simulation. Unit/provider-contract/real-provider/physical-capture/load results reported separately. The real-provider smoke test proves only the response observed for that image.

**Status:** strict HTTP input models forbid extra fields and fixture paths are server-only. Review found publication incorrectly set `physical_capture_validated=true` solely from live mode; infra owner removed that overclaim and added honest provenance. `test_live_mode_does_not_claim_independent_physical_validation` passes. Mocked live API/worker test binds exact uploaded bytes, provider output and publication without any fixture fallback.

## Scope extensions, not current release blockers

- Multi-tenant public deployment requires operator admission/auth, retention/deletion, deployment storage and upload rate policy. Localhost capability sessions can be a correct demonstrable first topology if exposure is stated accurately.
- S3/cloud object storage, Kubernetes, a general task compiler, same-class tracking, calibrated 3-D invariants and independently synchronized camera clocks are extensions. They should not delay the core valid-pair/recovery demo.
- Cryptographic capture authenticity is not supplied by a browser timestamp or image hash. Preserve collector-submitted provenance without pretending to prevent a malicious collector from replaying prerecorded material.
- A collection-efficiency claim needs actual collector trials against a competent ordinary capture workflow. Infrastructure correctness and deterministic scenario success do not establish human-effort savings.

## Additional defects found during execution

- New sessions initially failed before any workflow: PostgreSQL `'-infinity'` in `preview_served_at` could not decode to a Python datetime. Infra owner changed the default to a finite epoch and added a migration. Real-Postgres tests now run.
- A valid PNG initially raised `RuntimeError` from `Image.verify()`: reading PNG EXIF first had loaded/closed the image stream. Root separated verification and metadata/decode phases. The independent live pipeline test uses PNGs and now passes.
- Provider response length was checked only after HTTPX had eagerly read the entire body. Provider owner changed to a capped streaming read and tested early termination. This is a resource-bound fix, not a model-quality improvement.
- UI submitted paid live previews without rendering `session.feedback`. Sent UI owner the concrete issue: render a freshness-limited hint, otherwise keep preview submission off. Do not turn a preview hint into committed acceptance.
- A transient API/repository publication-shape mismatch noted during parallel edits was resolved by the agreed repository return contract. The endpoint returns `{pair, session}` and the independent end-to-end assertion passes.

## Executed evidence

Command: `uv run pytest -q tests/test_adversarial.py` — **18 passed**. `uv run ruff check tests/test_adversarial.py` — passed. All database tests use random isolated schemas in local PostgreSQL `counterpart_test`; cleanup drops only the created schema.

The suite covers nine durable-state/budget/freshness cases, four pure verification cases, three actual streamed ASGI body cases, one complete live-mode API/storage/worker/provider/publication case and one abrupt-process-exit recovery. The API case uses HTTPX MockTransport and synthetic PNGs, so it establishes implementation integration, **not real Roboflow inference or physical capture accuracy**. Missing or dishonest Content-Length cannot bypass the actual body-byte cap; bounded valid chunks are delivered unchanged.

The crash test starts a real child worker process. Its mocked provider completes, then the process exits immediately before acceptance. Original evidence remains stored and unaccepted. After expiring the abandoned lease, a replacement worker accepts the same capture once; both possible provider executions remain reserved, and the original fence cannot heartbeat. This validates actual process-crash recovery with a fake provider, without claiming exactly-once external execution.

No cloud call, model computation, real person capture, public deployment or collector-efficiency benchmark was executed by this reviewer. The current checks do not establish those claims.
