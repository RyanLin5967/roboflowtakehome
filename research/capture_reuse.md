# Counterpart capture: reuse, product, and media protocol

2026-09-26. Research/design recommendation, not implementation evidence. Read with the root architecture contract once frozen. No provider compute, browser-camera validation, or performance measurement was performed for this note.

## Recommendation

Build a camera-first collection instrument with two related observations as its unit of work. The interface should make the desired change obvious, preserve the good first capture, show a precise reason to redo the second, and finish with the actual paired record. Its infrastructure should be visible through preserved work, current feedback, and a reliable complete result.

Use native browser capture primitives with a small explicit controller. Send bounded JPEG previews to the server, where Roboflow supplies object evidence. Keep committed photographs and temporal evidence on a durable upload path. Browser detections, recipe instructions, and client status are never acceptance evidence.

The first physical demo can use a cup, bottle, and book on a stationary desk. Confirm the chosen Roboflow model recognizes the specific objects before polishing thresholds. Distinct common detector classes simplify identity. No delivery apparatus, special sensor, or local ML is needed.

## Existing implementations to reuse

| Component | Source and reuse | Boundary / reason |
|---|---|---|
| Camera and recording lifecycle | [WebRTC recording sample](https://webrtc.github.io/samples/src/content/getusermedia/record/), [source](https://github.com/webrtc/samples/tree/gh-pages/src/content/getusermedia/record) | Adapt stream acquisition, recording, stop/error handling, and format probing. No peer connection is needed merely because the examples live in WebRTC samples. |
| Sample licensing | [WebRTC samples license](https://github.com/webrtc/samples/blob/gh-pages/LICENSE.md) | BSD-style three-clause license; copied source must retain its notice. Research only has not copied their implementation. Pin the adapted revision in attribution if code is taken. |
| Fresh frame callbacks | [requestVideoFrameCallback](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback) | Use callback metadata for presentation time and missed-frame visibility. The callback is not a guaranteed hardware exposure clock and can run late. |
| Camera setup | [getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) | HTTPS or localhost, explicit user camera permission, environment-facing camera preference, actual track settings inspection. Browser camera permission is intrinsic to the app, distinct from agent command approvals. |
| Recording portability | [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder), [isTypeSupported](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static) | Probe available WebM/MP4 types; record actual MIME, codec, dimensions, and duration. Do not hard-code Chrome output for Safari. |
| Local retry spool | [idb](https://github.com/jakearchibald/idb) | Optional small promise-based IndexedDB wrapper for unsent committed blobs and command identities. Browser storage is best-effort unless persistence is granted; it is not the authoritative capture store. Pin package through the lockfile if adopted. |
| Large resumable uploads | [tus protocol](https://tus.io/protocols/resumable-upload), [tus-js-client](https://github.com/tus/tus-js-client) | Established offset reconciliation and retry semantics. Adopt a maintained server/client if longer recordings require it. For bounded small stills/short clips, idempotent whole-object PUT is a defensible simpler first implementation. Do not invent a partial tus clone. |
| Guided acquisition prior art | [BiGuide](https://github.com/BiGuideCollection/BiGuide) | Prior art for guidance during object-detection data collection; do not claim that interactive guidance is new. Its mobile model execution is not the project implementation because the user forbids local ML. |
| Controlled temporal examples | [Minimal Video Pairs](https://github.com/facebookresearch/minimal_video_pairs) | Relevant prior art for minimal paired video QA. Reuse the experimental framing, not claims of novel paired data. Verify any data/code license before copying assets. |

Targeted Exa research was used in addition to primary documentation. Its strongest findings were the browser API references above. An archived one-star recorder app and a tutorial containing a placeholder repository URL were not reliable implementation foundations.

## Product experience

### One visual path, not a model dashboard

The initial view should answer three questions immediately: what pair am I collecting, what should I move, and what is saved?

Desktop composition:

1. Quiet header: Counterpart, a short recipe title, and connection status.
2. Main area: large live camera with current object overlays, alongside the accepted first capture once it exists.
3. One prominent instruction and one primary action directly below the camera.
4. Small explicit checks: target observed, reference observed, reference position retained, requested relation observed.
5. A completed-pair drawer contains both images/clips, the changed answer, evidence, and export status.

On phones, camera and instruction stay above the fold; the accepted first image is a small persistent thumbnail that expands on tap. Primary action has a large touch target in the safe area. The operator should not have to navigate through jobs, models, or raw JSON to collect an example.

Use large readable type and restrained styling: a practical camera instrument with the media as the visual centerpiece. Avoid gradients, decorative graphs, six-step marketing cards, tiny statuses, or a sidebar full of future features. State must be conveyed in text and shape, not color alone. Keep the live video DOM node mounted between status changes so updates do not flash or restart the stream.

### Spatial recipe: the first complete vertical slice

Setup: a book remains fixed as a named reference; a cup starts to its left in image coordinates. A bottle can be an additional declared control when the model recognizes all three reliably.

Flow:

1. “Place the cup to the left of the book. Keep the camera still.”
2. Preview checks help positioning, but a click creates a distinct committed image and server verification.
3. Once accepted: first thumbnail gets “Saved”; copy becomes “Move only the cup to the right of the book.”
4. Intentionally move the book too. A failed second capture gets a visible book highlight and “The book moved. Put it back; your first photo is saved.”
5. Correct the book and capture again. Show the pair and its grounded QA: “Which side of the book is the cup on?” → left / right.

The relation is image-plane left/right. Do not label physical contact, containment, distance, or “only one thing changed” from boxes. Controls are specific measured object position/size bounds, not a proof that every pixel except the target is identical.

### Temporal recipe: the memorable second vertical slice

Use cup and bottle, a stationary camera, and clearly defined destination areas. In clip A, move cup into its destination, pause, then bottle. In clip B, reset the same initial arrangement and reverse the order. Initial and final arrangements should agree within declared tolerances.

Prefer the question “Which object reached its destination first?” to “Which object started moving first?” when detection is sampled. Arrival with a visible stable pause is observable more reliably than an onset concealed by a hand. Record per-object last-outside/first-inside observation intervals; accept order only when the intervals are separated and the required dwell evidence exists. Ambiguous or overlapping evidence produces recapture/review, never an invented timestamp.

Show synchronized paired clips and a two-row event strip. The viewer can see the same ending and different event order. Only show verified events. Do not animate a requested action as if the system observed it.

The temporal verifier must independently require stable starts, object identity, sequential event evidence, stable ends, and sufficient observations. A prompt saying “move the cup first” is not the label source.

## Browser controller and authority

### Suggested separation

`camera controller` owns permission, selected device, stream lifecycle, frame callbacks, and bounded encoding. `capture controller` owns the accepted backend session revision, local attempt identity, retry spool, and candidate lifecycle. React renders state from these controllers; renders do not recreate camera streams.

The server owns task definitions, accepted capture IDs, active session generation, inference job identity, verification decisions, and publication. The browser only proposes a capture/command with exact expected identity.

### Identity envelope

Recommended fields for a submitted frame or artifact, reconciled with the root API contract:

| Field | Meaning |
|---|---|
| `session_id` | Persistent collection session |
| `session_revision` | Expected backend revision for state-changing commands |
| `stream_epoch` | Fresh identity whenever camera stream or capture continuity restarts |
| `attempt_id` | Stable identity for one committed candidate; survives network retries |
| `frame_sequence` | Increasing sequence within a stream epoch |
| `media_time_ms` | Presented media timestamp within this stream's timeline |
| `client_monotonic_ms` | Client elapsed-time metadata, not a server-clock timestamp |
| `width`, `height`, `mime_type` | Actual encoded image/media properties |
| `sha256` | Hash of exact bytes, checked by server |
| `source_kind` | Server-enforced fixture/camera/import distinction |

Client timestamps are useful diagnostics and sequencing inputs but untrusted. Server wall times measure queue/processing intervals. Do not subtract browser `performance.now()` from server wall time to claim end-to-end latency. For real preview freshness, the browser can compare its own capture and result-receipt monotonic times using echoed frame identity. Cross-device temporal synchronization is outside the initial contract.

## Preview protocol

1. A user explicitly starts the camera. Ask for video only; audio is unnecessary for these recipes.
2. Await nonzero video dimensions and a presented frame before enabling Capture.
3. Prefer a rear camera with ideal constraints, not an exact resolution or device requirement. Read and display actionable errors for denied, missing, busy, and unsupported devices.
4. Draw the actual video orientation to canvas; no CSS mirroring may silently invert left/right. If a selfie preview is mirrored for familiarity, transformations and exported media must agree explicitly. Simplest initial policy: no mirrored capture.
5. Sample at a bounded configurable rate, initially approximately one preview per second, and cap dimensions. This is a cost/interaction starting point, not a latency promise.
6. Allow at most one encoding operation and one unsent latest frame per session. Do not accumulate an unbounded canvas-encoding queue.
7. Server may replace obsolete queued previews. It cannot replace committed evidence. The UI never turns an old preview success into candidate acceptance.
8. Display freshness honestly; stale overlays fade and show “Checking current view…” instead of green approval. If previews are paused to preserve credits, a committed capture can still be verified directly.

Destroying an AbortController prevents client consumption, not necessarily server work. Correctness requires backend identity/revision checks even when browser requests are cancelled.

## Commit and upload protocol

1. Freeze candidate identity and pixels on click; freeze them before asynchronous encoding begins. Disable duplicate capture transitions while this candidate is being submitted, but do not disable camera viewing.
2. Store committed bytes and their stable command identity in the retry spool when supported. Preview bytes need no persistence.
3. Upload to an immutable artifact identity. Repeating the same identity and digest returns the original receipt. Reusing it for different bytes is a conflict.
4. An upload receipt means bytes persisted, not scene accepted. Show “Saved, checking…” until the exact server decision arrives.
5. Retry transient transport failures with capped exponential backoff and jitter. Authentication, validation, stale revision, oversized media, and exhausted budget are explicit non-retry states.
6. After a timeout, query current receipt/session state before deciding another logical capture is needed. An HTTP timeout does not prove the server failed.
7. Purge local acknowledged blobs after server confirmation. Cap the unsent spool by count and bytes; quota failure must not be described as successful local preservation.

Do not guarantee survival of arbitrary browser storage eviction. [Browser quota/eviction rules](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria) make the server-acknowledged capture the durable boundary. Ask for persistent storage if appropriate, but continue with an honest degraded state if unavailable.

## Disconnect and physical continuity

Transport reconnect alone is insufficient to resume physical verification. On reload/reconnect:

1. Fetch authoritative accepted captures and session revision.
2. Reconcile pending local attempts against server receipts.
3. Restart camera in a fresh stream epoch.
4. Restore the accepted first thumbnail immediately.
5. Show “First capture saved. Check the scene to continue.”
6. Observe and verify the named controls before allowing the second candidate to be accepted.

If another tab or device advanced the session, render the newer backend state rather than merging two speculative local state machines. A BroadcastChannel can improve same-browser UX but is not a concurrency authority.

Camera track `mute`, `ended`, device changes, orientation/resolution changes, or hidden-page state suspend current feedback. For an in-progress temporal clip, mark continuity interrupted and require a new clip; never append the resumed recording and claim uninterrupted observation. [Track mute events](https://developer.mozilla.org/en-US/docs/Web/API/MediaStreamTrack/mute_event) indicate temporarily unavailable source media, and the [Page Visibility API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API) supports explicit backgrounding handling.

## Recording and temporal evidence

Keep original short video for human review and a bounded timestamped observation sequence for machine verification. Decide in the root contract whether samples come from the live stream or server decoding of the stored original. Stored-video decoding gives a clearer single evidence source; live samples give timely guidance. If both are used, retain provenance rather than claiming the sampled JPEGs are exact encoded-video frames without checking.

[MediaRecorder `dataavailable`](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event) chunks are delivery events, not an accurate clip clock. Chunk delays can grow while screens lock or camera delivery is paused. Do not derive duration or action ordering from chunk count multiplied by requested `timeslice`.

Probe supported codecs. Bound recording duration, artifact size, evidence frame count, and maximum observation gap. Wait for the final `dataavailable` and `stop` lifecycle before declaring the file complete. A mid-recording failure preserves an incomplete artifact if possible but cannot yield an accepted temporal pair.

If using sampled stills, the verifier must reject evidence gaps near the event boundary that make order uncertain. It should distinguish “event did not happen” from “camera did not observe enough to know.” Exact thresholds must be calibrated against real annotated recordings; no threshold in this design note is a validated metric.

## Honest demo and proof

The default entry can offer “Start a real capture” and a quieter “Explore a recorded example.” Fixtures carry a persistent “Simulation — deterministic test data” badge. A prerecorded human capture should instead say “Recorded capture” and retain actual provider provenance. Fixture media must never enter the real export path as if verified by Roboflow.

Opening demo: capture a valid first image, disturb the named reference while moving the target, receive the concrete correction, fix it, and produce the pair. Then reload or disconnect: good work remains, and the live scene is checked again. Infrastructure evidence is a small expandable trace attached to that pair, not a wall of unrelated service charts.

A separately labeled fault/replay view can inject delayed old results, duplicate commands, worker interruption, and many simulated collectors. It should show actual receipts/events and explicitly identify replay traffic. A late successful response for old candidate A must not approve newer candidate B.

## Required validation

| Test class | What it establishes |
|---|---|
| Unit/controller | Camera cleanup, bounded encoding, MIME selection, retry classification, stale UI result rejection |
| API integration | Immutable media identity, duplicate-command receipts, revision conflicts, complete-pair publication |
| Browser with deterministic camera fixture | UI transitions, actual video element lifecycle, still capture, recording stop lifecycle, reload/retry UX; no CV quality claim |
| Chromium and WebKit browser QA | Layout, camera API capability handling, codec fallback, touch-size controls; desktop WebKit is not proof of iPhone Safari camera behavior |
| Real phone + cloud Roboflow | Actual object grounding, orientation, camera continuity, correction usefulness, cloud response handling |
| Independently annotated real temporal clips | Arrival-order acceptance and abstention accuracy, occlusion/gap handling |
| Failure/load replay | Stale result fencing, duplicate prevention, bounded memory/backlog and fairness at a stated offered workload |

Specific cases: camera denied; never-resolving permission prompt; zero dimensions; track ends during encoding; orientation rotates during clip; repeated frame timestamps; response order B then A; failed upload after successful server persistence; quota exhausted; two tabs mutate same session; backend restart after first capture; app backgrounded during temporal event; local fixture mode attempts real export; unrecognized or duplicate class objects; camera moves while target remains positioned; imported clip with unsupported codec.

## Decisions to freeze before implementation

- Exact supported recipes and truthful relation wording.
- One source of temporal evidence and its declared continuity/gap policy.
- Committed capture API, immutable IDs, idempotency key behavior, and stale revision response.
- Browser/server authority for stream generations and cross-tab replacement.
- Local retry-spool scope and media size limits.
- Server-enforced fixture/real provenance and export eligibility.
- Budget cap per collection session and preview admission when credits are low.

These decisions should become executable tests. No independent services or browser model runtime is necessary to make the capture experience and its infrastructure substantial.
