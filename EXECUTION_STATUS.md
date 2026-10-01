# Plain-background collection repaired and running — 2026-10-01

Live recipe/verifier 2.3.0 pins visual/appearance 4.0.0 and background 1.0.0.
The first image records a reference without demanding four textured image regions.
Subsequent captures compare visible background pixels, including plain walls/desks.
Dense comparison masks declared roles/protected objects, which retain independent
required geometry/appearance clauses; unrelated detected furniture remains evidence.
Reliable ORB camera-motion failures still block. Bounded exposure/shadow correction,
structural edges and color checks address ordinary lighting variation; a bounded
translation search handles small jitter when features cannot establish a transform.

Appearance 4 preserves silhouette area while resampling, fixing JPEG/gamma-induced
false rejections without changing the existing appearance tolerances. Partial
intermediate temporal movement now requires agreement of both whole-object and
interior coordinates; unknowns remain blocking. Historical 1.0–2.2 decisions and
contracts retain their exact previous semantics.

Standalone 2.0/2.1/2.2 rejected initial photos can automatically recheck into a
separate 2.3 session using their original bytes and recorded detections. Existing
pair recheck remains supported. Original history, split lineage, timestamps and
camera orientation survive; receipts handle concurrency/lost responses. Incomplete
derived pairs require a fresh physical-scene check. Frozen campaigns and human
baselines retain their contracts. No new provider call is needed for recheck.
The obsolete “Show more fixed background detail” / patterned-paper advice has been
removed from the served UI; genuine uncertainty displays its actual measured cause.

Executed evidence:
- 14/14 selected physical outcomes match expectations: seven prior valid pairs and
  two Oct 1 first photos pass; five known invalid/unclear attempts remain blocked.
- 13/13 altered-original negative controls are blocked; 5/5 benign rendering
  variants pass, including JPEG, noise, exposure and gamma. The two actual Oct 1
  baseline views also pass a same-scene recovery comparison.
- 75/75 saved historical live decisions reproduce exactly; all 12 previously
  machine-accepted observations remain accepted under 2.3. No historical human
  authority records were included in that sweep.
- Actual compiled UI/API/isolated PostgreSQL smoke on latest rejected baseline
  62460855 passes automatic recheck, original-byte/history preservation, mirrored
  orientation, reload and 390px layout; no old texture warning or browser errors.
  It makes zero camera/provider calls and leaves production data untouched.
- Full backend run: 1,370 passed with explicit PostgreSQL DSN, no skips. Ruff,
  TypeScript/Vite and diff checks pass. Browser capture/recheck suite: 35 passed.
  A broader browser run passed 67/68; its failed camera screenshot sampled the
  startup placeholder before the first video frame arrived. The test now waits
  for visible decoded video, with unchanged pixel/byte assertions. The corrected
  camera suite passes 12/12, including five repeated passes of that failure.

API and worker restarted after confirming no active jobs. Serving
http://127.0.0.1:8080 with index-D-sgdn7O.js; API PID91986 / exec81467,
worker PID92156 / exec91505. Health, served bundle and both live recipe versions
2.3.0 verified. Current ledger139/512 reserved; no new provider/Modal calls or local
ML compute in this repair. Read-only/replay probes use original saved Roboflow
observations; do not describe them as new detector evaluations.

Ignored evidence: artifacts/qa/background-repair (selected and75-history reports,
source hashes, initial failures/residuals, restart receipt), and
artifacts/qa/background-baseline-browser (actual compiled browser/API probe).
Reproducible selected probe: scripts/probe_background_repair.py; it fails if any
expected outcome, historical reproduction, rendering control or recovery mismatches.

Limits: no universal100% physical-camera guarantee. Physically different scenes
that produce indistinguishable pixels cannot be distinguished; changes hidden by
objects and indistinguishable smooth lighting changes can escape comparison.
Detection ambiguity, occlusion, excessive lighting changes or inadequate visible
background may still require a retake. Oct 1 sources are baseline photos, not a
newly captured opposite-side pair. There are no saved physical temporal sequences;
those regression tests use explicitly generated pixels/observations. Repair examples
are not a held-out collection-accuracy study.

---

# Collect another image set immediately — 2026-09-29

Saving now leads to **Add another image set**, with **Download complete pair**
retained as a secondary action. The collector no longer sees the post-save
“Awaiting operator review” banner. This does not rewrite historical review
records or waive the operator's dataset-release decisions.

POST /api/sessions/{id}/next creates a separate empty session using the same
recipe, object roles and protected objects. Campaign sessions retain their frozen
contract and original invitation admission/quota; standalone sessions use the
current recipe and configured detector. Continued sets conservatively keep their
scene split group. Completed pair bytes and decisions remain intact. Authenticated
receipts commit atomically with admission; concurrent retries and reload after a
lost response return the same session. The browser preserves both capabilities in
Your sessions before switching and keeps the original set on a failed request.

Executed checks: 16 new real-PostgreSQL/API tests, 87 existing API/collection/recheck
regressions, and 22 browser capture/continuation tests passed. TypeScript/Vite, Ruff
and diff checks passed. Actual compiled UI + API/PostgreSQL/worker replay completed
capture, save, ZIP download, next-set creation and library reload; original set
remained published, both sets remained accessible, no browser errors or 390px
overflow. Provider reservation total was unchanged. These use labeled replay or
protocol fixtures, not new physical camera/model validation. Ignored smoke evidence:
artifacts/qa/next-image-set. No provider calls, local ML or Modal compute.

API restarted at http://127.0.0.1:8080, PID 60289 / exec session 18013, serving
index-CNlR1CSR.js. Existing worker remains running (PID 37447); no migration needed.
User explicitly authorized publication to RyanLin5967/roboflowtakehome. The existing
public origin is the destination; credentials, runtime databases, private captures,
local build outputs and QA artifacts remain outside tracked content.

---

# New-capture false rejections corrected with background compositing — 2026-09-29

The previous claim of a repaired live flow was too broad: old saved pairs passed,
but fresh 2.1 captures exposed two remaining bugs. The transparent bottle inherited
a dark background, pushing raw mean color difference to .12484/.12929 above .12
while geometry, silhouette and chromaticity passed. A cup shift within accepted
geometry also misaligned fixed-coordinate patches (NCC .81895).

Live recipe/verifier 2.2.0 now pins visual/appearance 3.0.0. Five vertical bands test
whether measured foreground color changes are explained by exposed-background
changes using bounded scalar fits; the unexplained residual must pass. This replaces
the confounded raw foreground-mean gate instead of increasing its threshold.
Stationary objects compare corresponding object-box coordinates within unchanged
geometry limits. Corrected passes supersede raw-color disagreement; unresolved
checks still block. Fitted coefficients measure consistency, not physical opacity,
identity or complete texture invariance. Historical 1.0/2.0/2.1 results remain exact.

Actual new failures be27aa3b, 132b7aa3 and b86342de now each pass all 19 checks using
their original bytes and recorded Roboflow detections. The wrong-relation attempt
1fa24eac and scale/lane violation c538159a remain blocked. Across old and new examples,
14/14 object appearance checks in seven valid pairs pass. Ten manipulated-original
controls remain blocked, including changed caps, recoloring, inversion, shuffling,
replacement and partial texture erasure. These repeated-scene repair examples are
not held-out accuracy. Generated transparent light-to-dark cases also exercise
corrected acceptance when the old raw-color test strongly fails.

Recheck supports frozen 2.0 and 2.1 sources, targeting a separate 2.2 session.
Historical lost-response receipts recover the originally recorded child/version;
new keys target 2.2. Original captures/contracts, split lineage and camera orientation
remain preserved. No manual review or fresh camera/provider call is needed to recheck.

Executed actual compiled UI/API/isolated PostgreSQL on copies of the newest physical
pair: original 2.1 decisions reproduced exactly; automatic 2.2 recheck passed every
clause, Save/download/reload and independent PortableRelease validation passed.
No source mutation, new provider reservations, camera request, browser error or 390px
overflow. Test schema/server were removed. Separate ignored evidence is under
artifacts/qa/automatic-appearance-v22. This validates the latest saved pair; it does
not establish that every future physical scene will pass.

Executed full Python suite with explicit PostgreSQL DSN: 1,188 passed, no skips.
Further focused checks include 40 comparator tests, 65 version-contract tests and
25 recheck/API/archive tests, all passed. All 14 relevant browser recheck tests passed;
actual compiled browser flow also passed after the TypeScript build correction.
TypeScript/Vite, Ruff and diff checks passed. No local ML or Modal compute.

A separate 16-call hosted CLIP diagnostic used an atomic 16-unit reservation after
fresh free-plan verification. It corroborated the cup's appearance but exposed
cross-scene overlap between valid/altered bottle similarities, so a global embedding
cutoff was rejected. Ledger total after intervening user captures: 120/512 reserved
units; estimated diagnostic use .003 Roboflow credits. No production CLIP gate.
Ignored results remain in artifacts/qa/appearance-review/hosted-embedding-v2.
Compositing equation background reference (not a claim to implement its learned model):
https://openaccess.thecvf.com/content_CVPR_2020/papers/Sengupta_Background_Matting_The_World_Is_Your_Green_Screen_CVPR_2020_paper.pdf

Running http://127.0.0.1:8080 with bundle index-BmTCzx_J.js and live recipes 2.2.0.
API PID 38120 / exec session 46171; worker exec session 43890. Both restarted after
confirming no active jobs, then health and served bundle were verified. Refresh the
existing page to recheck the saved pair automatically. No remote push/public deployment.

---

# Automatic capture repaired with required appearance checks — 2026-09-29

This entry supersedes the review-detour entry below. The forced standalone review
route is removed. A proposed appearance-unknown warning waiver was rejected before
production deployment: changing admission policy was not an appearance fix.

New live recipe/verifier 2.1.0 pins visual/appearance comparator 2.0.0. Stationary
objects compare registered pixels after illumination correction estimated from shared
background. Separated moved objects compare silhouettes and foreground color against
the background exposed in the other photo. Strong prior pixel-change failures remain
blocking. Every unknown/fail clause still blocks; appearance is never silently waived.
Historical 1.0/2.0 contracts/results remain exact. Measurements establish bounded
shape/color consistency, not physical identity or exhaustive texture continuity.

Four saved valid physical attempts now pass both bottle and cup appearance checks.
The earlier moved-cup attempt remains blocked. Eight altered-original-image controls
(recoloring, replacement, inversion, shuffling, partial texture erasure) remain blocked.
These are repair/calibration evidence from repeated scenes, not held-out accuracy.
23 generated comparator tests passed, including transparent objects and refusal guards.

Saved standalone 2.0 pairs with appearance-only uncertainty automatically recheck
under 2.1 into a derived session using exact original media/model observations. The
source history is immutable; receipts are authenticated/idempotent and source commits
are revision/epoch fenced. Capture times, source links, split grouping and camera
orientation survive derivation/reload/retakes. No new perception call is needed.
Frozen campaigns and human-authorized sources retain their original contract.

Executed actual compiled UI + real API + isolated PostgreSQL with copies of the latest
user originals: strict 2.1 passed all 19 counterpart checks, then Save, ZIP download,
reload and independent PortableRelease validation passed. Original media/model
observations matched exactly. No camera/provider calls, new jobs/reservations,
production-session mutations, browser errors or 390px overflow. Isolated schema/server
were removed. Private screenshots/ZIP/report are ignored under
artifacts/qa/automatic-appearance; no session credentials were persisted there.

Full Python run with explicit isolated PostgreSQL DSN: 1,138 passed, no skips; includes
21 recheck race/capability/immutability/archive tests. Additional comparator tests
completed afterward: 23 passed. TypeScript/Vite, Ruff and diff checks passed.
All 61 browser tests passed, including automatic recheck, lost acknowledgements,
orientation preservation, both retakes, capture countdown/Space, and existing product flows.

Independent hosted Roboflow CLIP diagnostic used 29 successful embeddings and one
failed Perception Encoder request (actual endpoint 404 despite advertised schema).
40 units reserved atomically; total ledger 96/512, with unused units retained.
Header-only CLIP ViT-L-14 worked; actual bottle similarities .9443–.9641 exceeded the
synthetic altered-object controls (highest .9246). This small diagnostic corroborates
the repair investigation, not a calibrated identity gate. CLIP is not in the production
acceptance path. No local ML or Modal compute. Approximate successful-call usage
0.00544 Roboflow credits at published pricing. Private artifacts remain ignored under
artifacts/qa/appearance-review/hosted-embedding-v1. Official references:
https://docs.roboflow.com/deployment/roboflow-cloud/serverless-api/model-pricing
https://docs.opencv.org/4.x/d7/dff/tutorial_feature_homography.html

Running http://127.0.0.1:8080 with rebuilt bundle index-D4_EPW1O.js, API PID 54155 /
exec session 98226 and worker exec session 62703. Both processes restarted after
confirming no active jobs. Actual health advertises saved_pair_recheck=true and both
live recipe versions 2.1.0. Refresh the existing collector page to recheck saved photos.
No remote push or public deployment.

---

# Transparent-bottle appearance uncertainty: direct review path — 2026-09-29

Latest physical spatial pairs now pass geometry and background registration. They
remain unknown solely on appearance: bounding-box RGB histograms change with a clear
bottle's background/reflections, and small lighting shifts can also affect the cup.
Do not tell the user to keep fixing already-passed geometry or claim automatic
physical validation is complete.

Collector appearance-only uncertainty now offers Review these two photos as its main
action, with saved thumbnails marked Needs review and optional recapture. It opens
an authenticated standalone-session evidence route with the exact two captures
preselected. Labels, criteria and decision note require an actual reviewer; no
automatic approval or rewritten machine results. Historical campaign review still
works. The previously hidden review capability is now reachable for this user's
standalone collection.

Investigated hard-bin smoothing, continuous-bin histograms, Wasserstein color
comparison and spatial correlation on saved pixels and synthetic negative controls.
Smoothing still refused the newest transparent bottle; Wasserstein admitted texture
erasure/inversion; correlation rejected legitimate moved bottles. Experimental
verifier/version changes were fully removed. No thresholds/contracts/results were
changed to manufacture acceptance. Probes are ignored under
artifacts/qa/appearance-review. Relevant metric references: OpenCV histogram
comparison (docs.opencv.org/4.13.0/d8/dc8/tutorial_histogram_comparison.html) and SciPy
Wasserstein distance (docs.scipy.org/doc/scipy/reference/generated/scipy.stats.wasserstein_distance.html).

Executed: 20 product browser tests and 2 new collector uncertainty tests passed;
24 real PostgreSQL evidence/review attack tests passed. TypeScript/Vite and diff
checks passed. Compiled app against the actual API loaded both user originals,
confirmed the exact selected pair is eligible for review, required explicit labels/
criteria, and rendered without browser errors or 390px horizontal overflow. No
human decision was submitted on the user's behalf and no user evidence was mutated.
An initial product-run transport failure came from simultaneous Playwright runners
sharing a Vite server; the isolated full product rerun passed. Avoid concurrent
runners sharing port4173.

Running rebuilt UI at http://127.0.0.1:8080 ; existing API/worker unchanged. Refresh
and choose Review these two photos. This is an explicit human-review path, not a
claim that automatic appearance matching now passes. No model calls or remote push.

---

# Collector flow redesigned around the two required captures — 2026-09-29

The capture screen now shows two illustrated goal cards before opening the camera:
photo 1 bottle left of the cup, photo 2 bottle right. Saved evidence replaces each
illustration. One main action opens the camera, captures, or saves the pair. Current
instructions precede the camera on narrow screens; camera options and detailed
checks are collapsed. Typography is larger and redundant instruction blocks removed.
Temporal and custom-role collection use the same progress component.

Retake controls sit with each saved capture. Retaking the first starts the pair over;
retaking the second preserves the exact first capture, its authority and any unresolved
recovery requirement. Both invalidate stale work and retain historical evidence.
Independent review caught a lost second-reset acknowledgement hiding its retry button
after polling; the control now persists until the original request is acknowledged.

Executed: 64 real PostgreSQL reset/repository/reviewed-baseline regressions passed.
Browser run passed 47/48; the failure was an obsolete custom-role diagram selector.
After updating that selector, all three focused checks passed, including the new
second-reset lost-acknowledgement regression. All 49 current browser cases have passing
evidence across those runs. TypeScript/Vite, Ruff, diff and changed-file credential
checks passed. Actual compiled UI/API/PostgreSQL/worker replay passed second retake,
replacement capture, publication and reload. Desktop and 390px screenshots were
inspected; no horizontal overflow or browser errors. No model calls, local ML, user
session resets or changes to verifier thresholds. Physical validation remains open.

Running at http://127.0.0.1:8080 with the rebuilt bundle; API PID 55806 / exec session
28545. Existing worker remains running. Refresh the page. QA screenshots are ignored
under artifacts/qa/capture-controls. No remote push.

---

# Countdown, Space capture, and retaking the first image — 2026-09-29

Live photos, recovery photos and sequence starts now show a two-second 2→1
countdown. Frames are captured after it finishes; sequence timing starts then.
Cancel, hidden page, camera interruption, disconnect and navigation cancel pending
capture. Space starts capture and stops a running sequence; typing, focused native
controls, modifier keys and repeats keep their normal behavior. Retries reuse saved
pixels immediately. No model executes locally.

The collector flow explicitly asks for two photos: bottle left, then bottle right,
with cup/camera fixed. Retake first image clears the active pair and comparison
target in the same session; contracts, campaign and scene group remain unchanged.
The reset endpoint fences older work with revision/epoch and invalidated leases,
has a durable idempotent receipt, and refuses published pairs. Old attempts/media
remain in history. Ambiguous client acknowledgements retain the reset request;
browser storage cleanup failure cannot block an already successful reset.

Executed: 45 browser regressions passed (1.8 minutes), followed by three focused
reset regressions after the storage-failure refinement, including one new case.
Backend: 61 relevant regression tests passed, then 16 focused reset tests including
an added concurrent worker/reset race. Independent review resolved local-storage
failure handling; TypeScript/Vite, Ruff and diff checks passed. Actual production
UI/API/PostgreSQL/worker replay reset survived reload at 390px with no overflow;
screenshot is ignored under artifacts/qa/capture-controls. No repair/test model
calls and no production user-session reset were performed.

Physical trial now has an accepted first spatial photo. Later recovery attempts
remain uncertain: the original and latest photos visibly differ in background
framing and object orientation. This is still not a completed physical pair trial.

App http://127.0.0.1:8080 serves the rebuilt bundle. API restarted as PID 72304 /
exec session 44081; existing worker PID 26433 / session 83880 stays running. Refresh
the browser to use the new controls. No remote push.

---

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
