# Counterpart full-product completeness review

2026-09-26. Independent source audit by `full_product_audit`, after the user explicitly requested continuing beyond the first implementation. This is a release definition and issue inventory, not a claim that these additions already exist. Paths/line references describe the first implementation inspected at the start of this audit; parallel implementation may change them.

Read together with `ARCHITECTURE.md`, `REVIEW.md`, the execution record, and the original selected proposal in `../roboflow-labs-takehome-research/round6/lanes/collection.md` and `COUNTERPART_INFRA.md`. The original product is an operator asking for a useful controlled example, a collector making and repairing it, and the operator receiving auditable paired data. Infrastructure supports that complete trip. The mandatory staff-complexity gate was removed by the user; inventing extra distributed systems is not a completion criterion.

## Verdict and bounded definition of complete

The initial implementation is a substantial, tested **capture and verification kernel**, but not the complete collection product. It handles two recipes, state recovery, fencing, originals, and individual pair downloads. It does not yet give an operator a collection request, multiple collector sessions, reviewable records, and a deliberately released dataset. It also has unresolved actual-camera quality and latency evidence. Passing hundreds of kernel tests does not close those gaps.

A complete release for this project means one continuous workflow:

1. An operator creates a versioned collection campaign specifying the supported recipe, objects, named controls, destination policy and target number of reviewed pairs.
2. A collector opens a scoped task link on a phone or laptop, understands the setup, captures and repairs a pair, and can leave/reconnect without losing the accepted baseline or confirmed inference work.
3. The operator sees multiple sessions and pairs, opens original evidence, understands machine checks and uncertain cases, records human review and correction requests, and selects eligible records.
4. The operator releases an immutable dataset version containing complete reviewed pairs, explicit labels and provenance, grouped splits, original media and an independently readable index. Optional Roboflow export refers to this deliberate release, with a fixed destination and visible receipts.
5. Another person/process can consume the downloaded dataset without knowing the UI or database schema. Restart, partial failures, repeated submissions, retries and normal overload do not alter the dataset's meaning.
6. The presentation demonstrates a real incorrect attempt, a useful repair and a resulting dataset record. The physical demonstration is separately validated; replay remains a labeled backup.

This does not require a general natural-language task compiler, same-class identity tracking, frontier training, a marketplace, Kubernetes, arbitrary region reasoning, or claims about all physical scene properties. Those are different projects, not hidden conditions for this one.

## P0 — release-blocking product gaps

### C1. Operator requests and durable multi-session collection

**Observed:** `models.py:13–17` accepts only the recipe/mode plus fixed cup and bottle. `api.py` has no campaign/task lifecycle or session listing. `web/src/api.ts:46–55` keeps one capability under one local-storage key; `App.tsx`'s `leave()` deletes it. Selecting New collection makes prior sessions inaccessible through the UI, although their DB/media survive. Server durability is not usable recovery if the product discards the only client credential and has no operator index.

**Required:** a small campaign contract and an operator library, not a general DSL. Campaign records should freeze recipe/model/verifier configuration, meaningful instructions, collection target and review/export policy. Collector links grant only the intended campaign/task scope. Starting a new collection must retain discoverability of prior work; operator views must recover it even after collector browser state is lost. An operator can pause a campaign without deleting its evidence.

**Acceptance:** create one spatial and one temporal campaign; start two sessions on separate browser contexts; complete one and leave the other partial; reload and start another collection; the operator still finds both, the collector resumes its own partial session, and neither collector can inspect the other. Pausing prevents new collection work while preserving accepted evidence and downloads. Concurrent task creation/retries cannot inflate target counts.

### C2. Actual review and release, with separate machine/human meaning

**Observed:** `repository.py:611–677` publishes a pair after machine acceptance; it has no review state. `dataset.py:55` always sets `human_reviewed=False`. The machine verifier reasonably abstains when ambiguous, but a real data operator has no review inbox, annotations, reject reason, correction request or audit history. A downstream user cannot choose a reviewed subset.

**Required:** append-only review decisions tied to the exact immutable pair/version, with reviewer identity, time, reason and clear approve/reject/needs-recapture semantics. Machine acceptance remains a separate fact. A changed pair or replaced observation invalidates prior review eligibility. A correction should create a new attempt or successor record, never mutate released evidence. Rejected/uncertain machine attempts remain inspectable; human approval cannot invent unseen temporal order.

**Acceptance:** inspect all originals and relevant checks; reject a visually plausible but incorrect pair; approve another; concurrent reviewers receive a revision conflict rather than silently overwriting; dataset release contains only policy-eligible approved records. Old release content/hash remains stable after a later review or correction. Replay records can be reviewed as examples but never become real captured training data.

### C3. Real repair feedback must be visually anchored

**Observed:** saved-frame detections render in `CaptureMedia`, but the live capture branch displays the camera plus an accepted-baseline thumbnail. The latest rejected capture is displayed as full evidence only in replay. The real collector sees a textual failure without a clear frozen view showing the wrong object against the kept baseline. This misses the selected proposal's strongest interaction: retain A, identify precisely what to fix in B, and repair while the scene exists.

**Required:** after rejection, show the submitted frame/sequence and the saved baseline together, highlight the measured failed control, and offer an explicit route back to the current camera. Use recorded boxes/intervals, not invented model masks. A ghost reference/anchor is valuable when its coordinate meaning is clear. Show actual displacement/tolerance in details; user-facing instruction should say what to move. For temporal failures, navigate to the relevant observed interval; do not claim exact motion onset between samples.

**Acceptance:** real-mode mocked-provider browser test deliberately moves the cup; UI shows the rejected pixels and baseline, highlights the reference discrepancy and preserves A; next valid attempt replaces only B. The live-camera view must not be mistaken for the image that was just rejected. On a 390px viewport, evidence and primary repair action remain readable. Later repeat with actual camera/Roboflow evidence.

### C4. Dataset versions that an external consumer can use

**Observed:** the existing VQA JSONL and pair archive are useful, but there is only a per-session ZIP. `dataset.py` derives labels correctly from evidence and keeps a pair in one split. There is no campaign dataset release, eligibility filter, review provenance, aggregate index, version inventory or export reader example.

**Required:** immutable release membership; pair and observation IDs; recipe/model/verifier/settings identity; media hashes and timestamps; Q/A rows with explicit labels and evidence/review status; a data card describing recipe/control limitations and collection cohort. Preserve original record provenance; derive a release wrapper rather than silently rewriting original manifests. Group related sessions from the same scene/collector when those relationships are declared, so new sessions in an unchanged scene cannot be advertised as independent holdouts. Exact duplicates across releases/splits should be detected and reported.

**Acceptance:** build a release from at least two approved sessions; download; a standalone reader verifies checksums, follows all paths and reads both answer variants; no pair member is missing or cross-split. Regenerating the same release returns stable membership and content identity. Adding/rejecting later records does not alter an earlier release. Synthetic provenance is searchable and excluded from a live release by policy.

### C5. Preserve already completed inference; retry evidence without recollecting

**Observed:** `worker.py:44–80` reserves a whole clip per lease, runs every image serially, accumulates observations only in memory, then persists at finish. Crash after frame 47 loses all 47 confirmed observations and retries the entire sequence. At the one recorded 7.349s hosted call, a 48-frame clip would take minutes if that latency persisted; this extrapolation is a risk, not a measured clip result. There is no explicit product action to retry verification of stored failed captures after automatic retries exhaust.

**Required:** durable per-frame success identity tied to immutable asset + pinned model/config; only missing/ambiguous invocations consume new reservations. Never reuse a response from a different model/version/recipe configuration. Bounded provider concurrency must remain global across workers, preserve temporal frame ordering during verification and stop new dispatch after lease loss. Show real completed-frame progress and service failure. A collector/operator can retry stored evidence under current authority without taking another photo merely because a provider failed.

**Acceptance:** kill an actual child worker after several mocked provider responses are durably stored; replacement resumes the remaining work, original bytes/times/order are unchanged, confirmed images are not reinferred, ambiguous calls remain accounted. Reject stale frame receipts and cross-model cache hits. Saturate multiple clips and previews with deterministic latency: progress advances, concurrency stays bounded and one collector cannot own all service indefinitely. A fail-then-retry browser flow uses the same stored frames and produces one accepted capture.

## P0 — release-blocking exposure/data-integrity issues

### S1. Export intents must not reinterpret historical camera consent

**Observed:** `repository.py:664–671` inserts a pending export intent for **every live pair** even when runtime export is disabled. `ExportWorker` later uses the globally configured project. Enabling export can therefore drain historical camera pairs into whatever destination is configured later. The existing public integration project was explicitly not approved as a camera destination. This is a semantic export bug even if the current worker never ran.

**Required:** local publication and export request are distinct. An explicit authorized export/release action freezes destination workspace/project, privacy policy and exact selected records. Existing local-only records remain local-only. Destination changes create a new explicit export operation; they must not change the meaning of queued receipts or reinterpret old intents. Validate the configured destination before camera export. Expose disabled/pending/uploaded/uncertain/complete honestly.

**Acceptance:** publish live record while export is disabled; later enable an exporter pointing at the public test project; zero upload calls occur. Explicitly request export to an authorized destination and confirm only named assets are sent. Change default destination mid-export: the original operation remains fixed. Replay and rejected records cannot enter live export. Wrong destination/privacy state fails before a write.

### S2. Operator admission and camera-compatible deployment

**Observed:** `POST /api/sessions` is unauthenticated and creates durable live/replay state. A capability protects subsequent individual reads, but anyone who can reach the service can create sessions, consume its global model allowance and grow DB/media. `GET /api/health` exposes global metrics and checks database state, not actual worker readiness. Camera permission works on localhost; a phone on plain LAN HTTP will not work.

**Required:** an operator admission boundary and scoped collector invitations for any nonlocal exposure; revocation/expiry for collection authority while preserving historical evidence; bounded creation/upload work; configured trusted hosts/origins and standard browser security headers. Provider and operator credentials never enter a collector link. Establish HTTPS deployment instructions with persistent DB/media and a reproducible API/worker start path. Readiness distinguishes missing worker/provider-disabled/budget-exhausted conditions from a healthy idle DB.

**Acceptance:** unauthorized caller cannot create billed work or access operator lists; collector cannot change review/destination/campaign policy; revocation stops new submissions. Two different collector identities cannot access each other's known media hashes. Deployment smoke starts API + worker + storage, restarts worker during capture, and downloads same released record. HTTPS/mobile setup is documented and its actual validation status recorded. No public deployment itself is required without user authorization.

### S3. Retention, orphan cleanup and bounded downloads

**Observed:** `api.py:205–246` persists media before `repo.submit()` checks admission/current state/idempotency. Rejected/stale/full-queue submissions can leave unreferenced files. Preview supersession bounds pending jobs, not durable media/capture/event growth. There is no retention/delete/cleanup command. `api.py:293–307` builds the complete ZIP in a `BytesIO`, then materializes it again for a response; concurrent temporal downloads multiply memory use.

**Required:** bounded staging/admission before expensive writes where possible, plus safe mark-and-sweep/retention for orphan and disposable preview evidence. Keep accepted/review/release-referenced originals until explicit retention policy permits deletion. A shared hash must not be deleted while another session/release references it. Persisted budget accounting must survive cleanup. Stream or spool bounded archives rather than accumulate every byte per request in RAM; bound concurrent export construction.

**Acceptance:** upload immutable media then simulate failed DB submit, run cleanup after a grace period, and verify only orphans disappear. Concurrent publication and cleanup cannot erase newly referenced evidence. Shared assets survive deleting one referencing session. Preview stress has bounded retained growth under the configured policy. A maximal pair download is complete, has valid checksums and does not make memory proportional to all concurrent archive sizes.

### S4. Freeze task/evidence versions and migrate safely

**Observed:** thresholds and verifier version appear in results, but session creation does not freeze execution settings. Workers read `config.roboflow_model` at startup, so restarting with another model changes remaining work in an existing collection. The paired manifest has no explicit exact baseline dependency in counterpart provenance. Initial schema setup is idempotent SQL with additive ad hoc updates, not an upgrade test for the new product schema.

**Required:** immutable campaign/task specification and a digest binding requested relation, named controls, verifier settings and model identity. Record exact baseline capture ID used for counterpart decision. Re-verification under new configuration is a distinct evidence/review version; never silently mix it into the original claim. New schema changes preserve existing sessions, assets, budget reservations and external write receipts.

**Acceptance:** create baseline under config A; switch worker to B; continuation uses pinned A or explicitly stops with a migration/reverification action. It must not silently claim a homogeneous run. Upgrade a populated pre-extension schema; resume partial collection, read old publication, verify budget totals and ambiguous export receipts unchanged. Repeated migration is harmless.

## P1 — required evidence before calling the physical product ready

These cannot be satisfied by adding code or rewriting synthetic tests. They should run alongside implementation as soon as owned camera evidence is available, with the existing cloud budget controls. Do not call them passed until observed.

| Evidence | Smallest meaningful execution | Why it matters |
| --- | --- | --- |
| Actual spatial repair | Several valid and invalid cup/bottle attempts, including reference movement, camera shift, low contrast, occlusion and duplicate object | The existing hosted traffic-photo smoke does not establish desk-object perception or useful repair. |
| Actual temporal pair | Two fresh opposite-order clips plus same-order, simultaneous arrival, interrupted capture and temporary occlusion attempts | Requiring both objects confidently in every sampled frame can cause high rejection in real hand-motion scenes; detector mistakes can also manufacture intervals. |
| Independent review | Human assesses held-out attempts from originals without seeing machine decision first; record reviewer identity/relationship | Machine checks cannot validate their own accepted-pair precision. Builder-only review must be disclosed if no second reviewer is available. |
| Feedback and completion latency | Capture/upload/provider/queue/verification/publish timestamps on actual sessions; report sample count and distributions | One successful 7.349s inference is neither a latency benchmark nor evidence of live coaching. |
| Useful data consumption | Load one real reviewed release with a separate reader, display both media/Q&A variants, verify hashes | The final artifact must be usable outside the attractive UI. |
| Comparison supporting a value claim | Equal recipe/workload ordinary shot list + post-hoc review vs assisted capture; held-out physical sessions | More tests do not prove saved collection/review effort. Without this comparison, show functionality and limits rather than claiming productivity gains. |

For infrastructure claims, run both the coalescing/fairness path and a competent bounded FIFO/sampling baseline on the same disclosed simulated arrival/service trace. Report feedback age, durable completion time, service fairness, discarded previews and accounted calls. Synthetic replay can establish those protocol properties; it does not measure Roboflow capacity or human collection speed.

No arbitrary target accuracy is a release escape hatch. Preserve failures, classify them, and change a hypothesis once. Freeze verification settings before held-out physical review. Broadening acceptance simply to pass the staged demonstration undermines the data product.

## P2 — useful extensions only after the complete path works

- More supported object pairs/recipes: choose based on observed detector quality and useful resulting questions. A general object selector is not useful if the detector cannot support the selected class.
- Background registration or a fixed fiducial can strengthen camera-control checks. It is optional until actual failures show anchor-only checks are insufficient; do not claim universal camera invariance in the meantime.
- S3/object-store backend is useful for separately hosted workers and large media. A single-host persistent shared volume is a valid release topology if restart/backup/restore is tested and limitations are explicit.
- Dataset consumer/evaluation runner can report pair-level correctness, not merely average individual accuracy, under a separately authorized cloud-model budget. Training a frontier model is unnecessary to establish this workflow.
- More contributors, collector quotas, diversity scheduling, adjudication and assignment locks can follow actual collection needs. They are not mandatory staffing theater.
- OAuth organizations, payment, marketplaces, arbitrary task language, long-running stateful SAM tracking and full multitenancy are outside this release.

## Demo that proves the whole product

The strongest ten-minute sequence is: operator requests a small controlled-pair campaign → collector opens task on phone → keep first capture → deliberately shift reference and see the exact repair → reconnect without losing it → fix only the second capture → operator reviews evidence and releases the pair → open the independent data consumer and actual Roboflow receipt if a destination is safely configured. A recorded real temporal pair then demonstrates why the final image alone does not answer the question.

Keep worker-recovery and load evidence one click away as engineering detail. One visible interruption proves a user benefit; a long fault-injection ceremony should not replace the collection story. The operator library and final consumer distinguish an instrument for acquiring usable data from a two-photo toy. No giant dashboard, leaderboard or model-training button is needed to make that point.

## Completion ledger and stop rule

Implementation should use a durable checklist with `not_started`, `building`, `tested`, `externally_blocked` states and named evidence. A passing batch is a checkpoint, not completion. Continue through all P0 software items and the executable P1 tests available under current credentials/budget; pursue one specific unresolved failure at a time. If owned physical capture/reviewer participation is missing, keep its status explicit, prepare the exact runnable collection protocol, and continue independent software/operational work. Do not claim an endless loop or continuation after the session ends.

The product may be called **software complete for the declared deployment** only after the end-to-end operator→collector→review→release→consumer path, recovery/admission/export/retention invariants, frontend flows and fresh-install/upgrade checks pass. It may be called **physically validated** only after recorded real-capture checks. It may be called **more efficient** only after the actual comparative collection study. These are different completion statements.

## Review provenance

Read current source, existing test files/reports and selected historical proposal. No model compute, cloud provider write, physical capture, new test execution or benchmark was performed by this auditor. Reported existing test totals come from the execution record, not an independently rerun suite. High-priority findings were sent to the root implementation agent while this document was prepared. This file is the only artifact owned/edited by this audit task.

## Follow-up implementation checks

The root assigned this auditor ownership of `tests/test_operator_api.py` after the initial review. The initial provenance paragraph above describes the initial audit only. The auditor subsequently executed real PostgreSQL and HTTP integration checks against the new implementation, using isolated schemas and deterministic images/detector responses. No cloud model, provider write or physical capture was performed.

Command: `COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest -q tests/test_operator_api.py --tb=short` — **44 passed**, one upstream Starlette test-client deprecation warning. `uv run ruff check tests/test_operator_api.py` — passed.

The checked product boundary now includes:

- Public mode refuses anonymous standalone work and local bootstrap even for a loopback proxy peer. Signed, expiring, server-only operator cookies; login/logout and rate-limit behavior; cross-site write rejection; trusted hosts; explicit fail-closed loopback-HTTP exception configuration.
- Collector invitation joins preserve idempotency, enforce quotas in a 12-request concurrent HTTP test and reject expiry/revocation. Invitation revocation is explicitly **new-admission only**: existing session capabilities remain usable to preserve unfinished work. It must not be described as revoking all issued collector access.
- Actual API + PostgreSQL + worker spatial and temporal replay journeys reach operator review and immutable dataset release. The downloaded ZIP contains both answer variants, preserved provenance, human-review wrappers and correct original-image hashes. Rejection after release changes current eligibility but not the earlier release.
- Client-supplied reviewer identity is rejected. Stale review cannot overwrite a recorded decision. Pair review, media membership and release selection enforce exact collection boundaries. Operator inspection does not return a collector capability or stored hash.
- Saved failed evidence can be retried with its own session capability, unchanged pixels and a new fenced attempt. Repeating the retry is idempotent; another session's capability cannot initiate it.
- A live-mode HTTP journey uses an explicitly synthetic detector and generated image bytes. Its publication remains `local_only` and unclaimable by export workers until approval plus explicit export. Server-side destination privacy checks run fresh at admission. Changing the configured destination cannot reinterpret the frozen operation. A browser asserting its own privacy/project fields receives schema rejection. Fixture replay cannot become a real export.

These executions close specific source defects from the initial review; they do not certify all remaining P0/P1 requirements, actual physical accuracy, independent human review, production deployment or global capacity. The root's full build ledger remains authoritative for remaining work and broader verification.

### Final targeted integration pass

The expanded `tests/test_operator_api.py` suite now has **53 passing tests** against isolated PostgreSQL schemas. Command unchanged; completed in 3.85 seconds on this run, with the same upstream test-client warning. Ruff passes. This is integration-test execution time, not a product latency benchmark.

Three concrete issues found in this final pass were fixed by the root and retained as regressions:

1. A batch export could queue earlier records, then return a failure for a later destination conflict. Batch admission now uses one transaction; a later conflict leaves previously unrequested records unqueued.
2. ZIP byte limits were checked before close, excluding the central directory/footer. The final complete size is now bounded before returning a successful archive response.
3. Disabling hosted inference hid an already admitted invitation-join receipt behind the new-work check. Atomic join admission now returns existing idempotent receipts while refusing new live sessions, preserving recovery after a lost response without dispatching model work.

Additional HTTP regressions cover a real PostgreSQL maintenance barrier returning sanitized retryable 503 before writing media; archive declared-size refusal before storage reads; sanitized storage-outage 503; pause preventing new media writes and new joins while preserving in-flight completion, old receipts and accepted evidence; successful resume; collector-visible review feedback separate from the immutable original manifest; and operator-only measured worker-registry status. Existing receipt replay after a pause remains safe because the authoritative transaction still checks request identity/fingerprint. These tests use deterministic evidence and synthetic detector responses where needed, never a real model call or provider write.

No further release-blocking source defect was found within this targeted pass. This statement covers only these inspected integration boundaries; the previously stated physical-validation and deployed-system evidence limits remain.
