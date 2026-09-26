# Counterpart: Roboflow perception and export reuse

Research checked 2026-09-26. Provider adapters and 77 mocked transport tests are implemented. The parent completed one successful bounded hosted inference smoke after the initial route returned 404; details below. No physical cup/bottle capture validation is claimed. This agent made no model calls or provider mutations; later authenticated read-only dataset queries used the private credential file without printing credentials. The frozen architecture/protocol takes precedence over exploratory thresholds below.

## Recommended first integration

Use hosted RF-DETR Small for both interactive still-image feedback and the committed frames of temporal captures. The backend is the only holder of the Roboflow API key. Begin with one cup and one bottle, viewed from a fixed camera, rather than pens (not a stock COCO class) or two visually indistinguishable same-class objects. This makes Roboflow's detector essential to deciding whether the controlled observation was actually collected, without training a model merely to make the integration look substantial.

Official current documentation explicitly supports `rfdetr-small` through `https://serverless.roboflow.com`. The SDK alias is `coco/39`; store both the user-facing model name and pinned canonical request model ID. Do not record only a floating alias in immutable verification records.

```python
from inference_sdk import InferenceHTTPClient, InferenceConfiguration

client = InferenceHTTPClient(
    api_url="https://serverless.roboflow.com",
    api_key=private_key,
).configure(InferenceConfiguration(
    api_key_transport="header",
    confidence_threshold=0.25,
    max_concurrent_requests=1,
    max_batch_size=1,
))
# Calling this requires an authorized budget reservation.
result = client.infer(image_path, model_id="coco/39")
```

The low retrieval threshold above retains ambiguous candidates; it is not the acceptance threshold. Do not filter the provider response to only the desired classes before recording provenance. Retain the original response in private object storage and normalize geometry into the application's typed observation format.

The implemented synchronous `httpx` adapter reproduces the **actual SDK hosted V0 request shape**:

```http
POST https://serverless.roboflow.com/coco/39?confidence=0.25&disable_active_learning=true
Authorization: Bearer <server-side key>
Content-Type: application/json

<raw base64-encoded image bytes, not a JSON object>
```

Although the content-type looks unusual for raw base64, it follows the official SDK's `DEFAULT_HEADERS` and V0 data placement. `_determine_client_mode` selects V0 for all hosted Roboflow URLs, including `serverless.roboflow.com`. The alternative `/infer/object_detection` JSON route exists in self-hosted server source, but the parent's first hosted attempt returned **404**. Source availability did not establish gateway compatibility. That route was replaced; no silent fallback or additional retry was inserted into one reserved call.

The parent then confirmed the V0 request succeeds with header-only authentication. Do not install the full `inference` server, `inference-models`, PyTorch or model weights on the user's computer; the hosted adapter needs no local ML. The pinned request identity is `coco/39`; no upstream weight digest or resolved model hash was returned or invented.

Sources: [RF-DETR hosted usage](https://docs.roboflow.com/models/supported-models/rf-detr), [Serverless Cloud API](https://docs.roboflow.com/deployment/roboflow-cloud/serverless-api), [pinned aliases](https://github.com/roboflow/inference/blob/209766604d986353892bd10e64e28e476adedbca/inference_sdk/http/utils/aliases.py), [request construction](https://github.com/roboflow/inference/blob/209766604d986353892bd10e64e28e476adedbca/inference_sdk/http/client.py), [base64 payload construction](https://github.com/roboflow/inference/blob/209766604d986353892bd10e64e28e476adedbca/inference_sdk/http/utils/requests.py).

## Reuse decisions and pins

| Existing implementation | Reuse in Counterpart | Avoid / reason |
| --- | --- | --- |
| `inference-sdk` 1.7.2, core Apache-2.0 | Hosted API protocol, aliases, prediction schema; optionally its async client | Hidden retry behavior and default credential transport should not control budget/reliability implicitly. Configure or wrap explicitly. |
| `roboflow` 1.5.1, Apache-2.0 | Existing upload, annotation and reconciliation API behavior; optional isolated export adapter | SDK uses API keys in query URLs for dataset endpoints; never persist full request URLs or exception reprs. No default retry loop around ambiguous writes. |
| `supervision` 0.30.5, MIT | `Detections.from_inference`, annotation/rendering and established COCO/YOLO export if those materially reduce code | Do not add it solely as a branding dependency. SVG overlays can use already normalized boxes without OpenCV in the API. |
| Roboflow Workflows SAM2 Video Tracker | Future arbitrary-instance tracking when a demonstrated use case requires it | Current temporal demo does not need process-local SAM state, persistent GPU stream sessions or segmentation masks. |
| Roboflow `trackers` / ByteTrack | Future same-class multi-object recipes after measuring identity requirements | A tracker ID is not proof of physical identity; it may switch under occlusion. Do not infer reliable identities from sparse unrelated HTTP frames. |
| Roboflow dataset upload Workflow block | Reference for existing tags/batches/prediction-upload behavior | It does not make two captures and an external app transaction atomic. Counterpart still owns paired publication. |

Source repositories inspected at inference `209766604d986353892bd10e64e28e476adedbca` and roboflow-python `c39f99e6645b9039188f16665a5e17b2d83655d1`. PyPI versions are current lookup results, not installed/tested project pins. Freeze actual dependency versions in the lockfile. The inference repository LICENSE delegates core files to Apache-2.0 and lists separately licensed model/enterprise directories; do not characterize the entire repository or every hosted model as identically licensed.

Sources: [inference licensing](https://github.com/roboflow/inference/blob/209766604d986353892bd10e64e28e476adedbca/LICENSE), [SDK license](https://github.com/roboflow/roboflow-python/blob/c39f99e6645b9039188f16665a5e17b2d83655d1/LICENSE), [Supervision FAQ](https://supervision.roboflow.com/latest/faq/), [SAM2 video tracker](https://docs.roboflow.com/workflows/blocks/blocks/video-processing/sam2-video-tracker).

## Cost and dispatch controls

Current live model-pricing documentation says RF-DETR Small/Base/Medium/Large cost **0.1875 Roboflow credits per 1,000 images** at the first volume band; Nano is 0.125. This differs from the older time-based pricing returned by the Exa cache. Use current provider documentation and verified account entitlement, not cached snippets. The 500 execution seconds/credit rule currently applies to VLMs and custom Python blocks, not ordinary detector image calls.

Examples for sizing only: 600 Small previews cost 0.1125 credits; a pair of 8-second clips sampled at 5 frames/second totals 80 images and 0.015 credits. These are arithmetic estimates at the documented rate, not measured account charges. Each retry must count as another potentially billable request until the provider confirms otherwise. Current docs say 402/408/409/423/429/5xx are not billed, while 401/403/404 are billed. Local reservation accounting should conservatively debit uncertain outcomes rather than depend on this to stay within limits.

Use a durable per-provider image allowance and per-session preview quota. Reserve before dispatch, record attempts, fail closed when exhausted, and never let SDK retry fanout exceed the reservation. Bounded verification clips are retained and processed; disposable preview frames may be superseded. Disable live inference automatically when the page is hidden, camera is paused, or session has no recent owner heartbeat. Cloud budgets cannot rely exclusively on browser cooperation.

**Modal is unnecessary for the initial detector path.** The user's $3.39 remaining Modal balance can remain available for a bounded later task. Current Roboflow remaining credits and spend/flex controls still need verification by the root before invoking hosted inference.

Sources: [current model rates](https://docs.roboflow.com/deployment/roboflow-cloud/serverless-api/model-pricing), [billing semantics](https://docs.roboflow.com/deployment/roboflow-cloud/serverless-api/pricing), [usage dashboard](https://docs.roboflow.com/platform/billing-and-plans/credits/view-credit-usage).

## Observation contract

Every normalized observation should include session ID, task revision, capture ID, evidence revision, frame index, capture-relative monotonic timestamp, server receipt timestamp, media SHA-256, orientation-corrected width/height, provider attempt ID, pinned requested model ID, alias-resolution provenance, upstream identity only if actually returned, inference parameters, detector boxes/confidences/classes, and normalization/verifier versions. Detection class numbers should not be assumed consistent across models; use provider labels plus a versioned class mapping. Reject invalid boxes, nonfinite numbers, mismatched image dimensions, out-of-range timestamps and malformed provider responses.

The detector's confidence is a score, not a calibrated probability that the object is correct. Multiple frames do not provide independent errors; multiplying confidence or error rates is unjustified. Detector boxes describe projected geometry, not physical 3D relations. A missed object is unknown, not evidence of absence. Human confirmation, if used, is a separate recorded review event; it must not rewrite automated model evidence.

Keep preview feedback separate from acceptance. A stale preview may draw a dimmed result with its age but cannot approve a different capture or advance the durable session. A committed capture is verified against its exact bytes and revision, irrespective of what the live camera currently shows.

## Spatial recipe: flip a relation, keep the anchor

First recipe: move the bottle from clearly left of the cup to clearly right of it. Keep the cup and camera fixed. The question and answer change; the two named objects remain the same observed classes. This is an image-plane relation, not a claim about world coordinates.

Suggested initial verifier configuration, **not calibrated thresholds**:

1. Require exactly one sufficiently confident cup and bottle. Start acceptance at 0.50 with additional same-class candidates above 0.25 producing ambiguity, rather than simply choosing the highest score. A two-threshold policy must be tuned on actual target props.
2. Require boxes entirely inside an inset safety border and a minimum pixel/normalized area. Reject tiny or cropped targets. Avoid overlapping target boxes for this simple recipe.
3. Establish the relation only if the bottle's right edge is left of the cup's left edge by at least 3% of image width, and the reverse for the counterpart. This is stricter and easier to explain than a center-only comparison near the boundary.
4. Anchor center displacement below 3% of image diagonal and box-scale change below an initial 20% tolerance are measurable controls; report values rather than a blanket “everything else unchanged.” Require compatible dimensions/orientation. A small background-change heuristic may flag camera/exposure changes, but is not a proof of scene identity.
5. Cup/bottle class consistency does not prove the same physical instance survived a disconnected session. Re-observe and require explicit user reconfirmation when identity cannot be established. Store this limitation and review status.

Named output checks should have `pass | fail | uncertain` with machine-readable reason, measured value, threshold and evidence-frame references. Examples: “reference cup moved,” “bottle too close to cup,” “second bottle makes identity ambiguous,” “cannot see the bottle.” Do not bury the helpful recapture instruction in aggregate scores.

## Temporal recipe: opposite arrival order, matched endpoints

The frozen architecture deliberately uses **arrival order into a visible target band**, not unseen exact movement onset. Two sampled sequences begin with matched cup/bottle placement outside the target and end with both inside it. In A the bottle arrives, there is a visible pause, then the cup arrives. In B the order reverses. A fixed camera and sufficiently separated actions make the evidence interpretable. The app verifies observations, not whether the collector was shown instructions.

Keep indexed original JPEG evidence with capture-relative monotonic timestamps. The frozen API accepts 8–48 frames over at most 12 seconds. The optional encoded video is for playback, not the source of timing truth. These are sampled-observation claims: if samples cannot establish order, ask for a slower recapture with a pause rather than filling gaps with invented detections.

Proposed checks, with exact constants owned by the versioned verifier:

- Require strictly increasing timestamps, a bounded gap, stable observed start and end segments, both unique classes, and sufficient usable frames. Require compatible initial and final normalized placements across both sequences.
- For each object, record the last confirmed outside-target observation and the first sustained inside-target observation. This is an arrival uncertainty interval. Occlusion/missing samples widen uncertainty; they do not mean an object remained outside.
- Decide order only if the arrival intervals are disjoint with the configured safety margin. Overlap is unclear order, not a tie broken by frame index or confidence score.
- Require a visible pause between arrivals for the coached recipe. Reject observed exit/reentry, ambiguous duplicates, camera motion, interruption, missing endpoint evidence or simultaneous arrivals. Keep intermediate evidence frames for the label.
- Store named controls and measurements; same final frame alone does not establish temporal order. Matching class labels still does not prove same physical instance after substitution.

Earlier research considered estimating movement onset. That is not the implemented recipe because sparse detections/hand occlusion make exact onset needlessly fragile. No accuracy result is claimed for arrival verification until a separate real-capture set is collected. Freeze thresholds before held-out evaluation; do not tune exclusively to presentation takes and report those same takes as unbiased validation.

## Roboflow paired export

Official `Project.upload`/`upload_image` accepts `batch_name`, `tag_names`, dataset `split`, metadata, and optional `sequence_number`/`sequence_size`. `single_upload` separates image and annotation writes. The underlying image API is `POST https://api.roboflow.com/dataset/{project}/upload`; local file upload uses multipart fields `name`, `split`, `file` and serialized `metadata` with batch/tags query parameters. Current SDK sends its key in query parameters here; redact before logging any error or transport URL.

Attach namespaced metadata: `counterpart_pair_id`, `counterpart_capture_id`, `counterpart_session_id`, `counterpart_manifest_version`, `counterpart_media_sha256`, `counterpart_role`, `counterpart_verifier_version`, plus task/review status. Keep paired/session-related samples in one split chosen once by stable session-group hashing. Store rich paired temporal semantics in Counterpart's manifest: the detection dataset is not a native atomic two-video reasoning-example database.

Model-generated boxes should be uploaded as predictions (`is_prediction=True`) unless human review genuinely promotes them. Metadata about a verified relation does not make every detector box human ground truth.

The upload adapter considers `success` **or `duplicate`** a valid result and returns provider image `id`. Duplicate detection exists, but no general caller-supplied idempotency-key contract was found. Do not promise exactly-once upload based on content hashing. A lost response is an **uncertain external outcome**.

Counterpart's outbox should write one row per required media export; record status, attempts and returned image IDs transactionally. On ambiguous outcomes, reconcile by durable tag/metadata and provider image lookup before reuploading. `Project.search(tag=..., fields=['id','name','tags','user_metadata','split'])` and `Project.image(image_id)` exist. Detect zero/one/multiple candidates and verify metadata/hash; multiple matches require explicit resolution. Only publish the authoritative complete manifest when every required artifact has confirmed receipts. One image being visible in Roboflow before another cannot be hidden by a local transaction.

**Duplicate metadata caveat:** duplicate response success does not establish that new tags/metadata were applied. Do not overwrite an existing image's pair ID to represent a second pair; manifests should reference existing assets without pretending one mutable provider metadata field is an immutable many-to-many relation. Test actual receipt/search behavior before advertising reconciliation completeness.

Sources: [pinned project implementation](https://github.com/roboflow/roboflow-python/blob/c39f99e6645b9039188f16665a5e17b2d83655d1/roboflow/core/project.py), [pinned upload adapter](https://github.com/roboflow/roboflow-python/blob/c39f99e6645b9039188f16665a5e17b2d83655d1/roboflow/adapters/rfapi.py), [official upload documentation](https://docs.roboflow.com/adding-data/object-detection). Plan/privacy wording in older upload docs may be stale: verify current account settings before creating a project.

## Required evidence before claiming the product works

1. Protocol tests with synthetic detections: reversed relation, anchor movement, ambiguity, confidence edge, malformed geometry, all-occluded frames, simultaneous moves, reordered timestamps, gaps, late results and changed capture revisions.
2. Provider contract tests with captured public response shapes and mocked transport failures; no real predictions invented in these fixtures. Enforce secret redaction and attempt budgets.
3. One bounded actual hosted inference smoke test, then a small real cup/bottle test set from permitted public/own imagery; record every outcome including misses.
4. Real capture set with positive and deliberately bad takes: moved anchor, camera bump, duplicate props, hand occlusion, fast motion, simultaneous motion, same order twice, mismatched end states. Separate threshold-tuning takes from final validation takes.
5. At least one actual hosted export and duplicate-response/reconciliation check in a dedicated new project, only after project privacy/credits are established. Fault tests cover lost upload response and partial pair export separately.
6. Report acceptance precision and uncertainty/recapture rate on the tested captures with counts. Report end-to-end latency, feedback age and inference count separately. Detector COCO AP from vendor docs is not Counterpart's pair-label accuracy.

## Implemented adapter boundary

`src/counterpart/providers.py` uses synchronous `httpx.Client` because inference runs in a separate worker, not in the API event loop. `RoboflowDetector.detect` makes one header-authenticated V0 raw-base64 request. `RoboflowExporter.upload`, `reconcile` and `annotate` each make one dataset REST request. The dataset API is a **narrow documented query-auth exception**: it places `api_key` on the outbound provider URL, applies an HTTPX logging redaction filter, and never includes provider URLs, body text or chained network errors in `ProviderError`. It does not pretend the dataset API supports unverified header auth.

Export annotations use the previously verified per-image VOC format, with one-based origin conversion, and remain `prediction=true`, `overwrite=false`, `addToDataset=false`. COCO is a dataset-level import format and is not assumed accepted by this per-image endpoint. A 409 annotation conflict is uncertain, not proof that the intended annotation was stored. An actual generated-card upload and prediction-annotation transport check subsequently succeeded; see the separate evidence below. This does not establish physical paired-data quality.

Validation performed: `uv run pytest -q tests/test_providers.py` → 77 passed; `uv run ruff check src/counterpart/providers.py tests/test_providers.py` → passed. These test exact request shape, redaction, malformed geometry/dimensions/model IDs, redirects/auth/rate limits/timeouts, no hidden retries, duplicate metadata uncertainty, exact reconciliation and prediction-only VOC. They are mocked protocol tests, not CV quality measurements. Additional tests cover streaming response limits, unexpected success statuses after writes, and rejection of duplicate assets associated with another pair, session or split.

Separate actual provider evidence, read from the parent's artifacts:

- `artifacts/provider_smoke.json`: initial `/infer/object_detection` request failed with HTTP 404; one image reserved.
- `artifacts/provider_smoke_v0.json`: corrected `/coco/39` request succeeded on a public 1280×827 traffic image; 52 detections across backpack, bus, car, motorcycle, person and truck; one call took 7.349 seconds wall time.
- Two total image attempts were reserved. The documented-price estimate totals 0.000375 credits; this is not a dashboard-confirmed charge. No Modal compute was used for these calls.
- This demonstrates actual hosted request/response compatibility and normalization on one image. It does not establish desk-scene accuracy, temporal verification quality or representative latency.

Separate actual dataset transport evidence:

- The parent created `counterpart-integration-test` as a public MIT project after private creation was rejected (422 on this free workspace). Only an explicitly generated transport-test card was uploaded, never a user camera photo.
- `artifacts/export_transport_probe.json` records successful image upload and empty VOC prediction-annotation upload. The immediate search returned no match, so the first outcome stayed reconciliation-pending.
- This agent later made authenticated **read-only** search and image-detail checks. The unchanged adapter search returned exactly one match, with the expected tag, `user_metadata`, `split=test`, media hash, pair ID and session ID. Calling the real adapter's `reconcile` with all expected fields returned confirmed receipt `zPVO7BrQ2fNBrSvo9uSS`.
- Search rows use `user_metadata`; image-detail GET uses `metadata`. The adapter's search field name and `tag` payload are correct. Adding `batch=true` or `in_dataset=false` was unnecessary: all tested variants found the same test card. The initial miss followed by success is consistent with delayed indexing; the exact indexing delay was not measured.
- An empty search after an ambiguous write remains **no observed match**, not proof no upload occurred. The worker must retry read-only reconciliation with a bound and backoff, never blindly reupload or report a complete remote export.
- This establishes real image upload, prediction-only annotation request and metadata-based eventual reconciliation transport. It does not establish actual paired-camera export, populated box geometry round-trip, dataset training quality, or private project availability. Automatic camera export remains disabled.

The crucial product claim is narrower and useful: Counterpart checks specified relations and controls, preserves auditable evidence, and prevents system races or partial exports from turning incomplete observations into apparently complete paired examples.
