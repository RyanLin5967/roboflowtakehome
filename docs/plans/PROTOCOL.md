# API and module contract v1

All session routes use `Authorization: Bearer <session capability>` except session creation. JSON errors use `{detail: {code, message}}` where feasible. JSON dates are ISO UTC. UUIDs are strings. Coordinates are normalized xyxy in [0,1]. Frame timestamps are finite monotonic milliseconds from the local capture start; they are not synchronized wall clock.

## Domain module contract (verification.py)

`verify(recipe: str, slot: str, frames: list[dict], baseline: dict | None = None, *, object_a='bottle', object_b='cup', interrupted=False) -> dict`

Frame: `{asset_id, t_ms, width, height, detections: [{label, confidence, box: [x1,y1,x2,y2]}], model: {provider, model_id, ...}}`. All fields are immutable recorded evidence.

Result: `{accepted: bool, code: str, message: str, checks: [{name, passed, detail}], evidence: dict, verifier_version: str}`. `evidence` contains relation/arrival intervals/controls as relevant and original observations or enough data to compare to `baseline`. `baseline` is the complete accepted baseline result plus `frames` supplied by the worker. Recipes: `spatial`, `temporal`. Slots: `baseline`, `counterpart`, `recovery`, `preview`. Preview/recovery receive one image even for temporal recipes. They can advise/re-ground but never complete a pair. Domain module is pure Python, no provider/network/DB calls.

## HTTP

- `GET /api/health`: DB liveness and configured provider mode, no secrets.
- `GET /api/recipes`: recipe instructions and limits.
- `POST /api/sessions`: `{recipe:'spatial'|'temporal',mode:'live'|'replay',object_a:'bottle',object_b:'cup'}` → `{token,session}`.
- `GET /api/sessions/{id}` → session snapshot with `id,recipe,mode,object_a,object_b,revision,epoch,state,recovery_required,baseline,counterpart,latest_capture,feedback,pair,events` (captures may be null). States `collecting`, `baseline_ready`, `ready`, `published`.
- `POST /api/sessions/{id}/resume`: `{expected_epoch}` → snapshot. Only compare-and-increment when epoch matches; retry of old expected epoch returns existing snapshot. Partial pair requires re-grounding. Frontend calls after page reload/network discontinuity.
- `POST /api/sessions/{id}/captures`: multipart `metadata` JSON and repeated `frames` image uploads. Metadata `{slot,expected_revision,epoch,client_seq,idempotency_key,timestamps_ms,interrupted}`. Spatial still/preview/recovery 1 frame. Temporal committed 8–48 frames, max 12 seconds. `timestamps_ms` count matches files. → `{capture,session}`. 409 stale request, 429 admission, 503 disabled live provider. Raw model predictions never accepted from browser.
- `POST /api/sessions/{id}/replay`: `{scenario:'baseline'|'counterpart'|'wrong_relation'|'camera_moved'|'ambiguous'|'recovery',expected_revision,epoch,idempotency_key}` → `{capture,session}`. Replay sessions only. Server fixtures define image bytes and detections. Same pipeline/DB worker, zero model calls, always labeled in UI/export.
- `POST /api/sessions/{id}/publish`: `{expected_revision}` → `{pair,session}`. Existing publication returned for retries; requires complete accepted pair and no unfinished replacement. Local publication is distinct from Roboflow export state.
- `GET /api/sessions/{id}/manifest`: full published immutable manifest JSON.
- `GET /api/sessions/{id}/archive`: ZIP with manifest+original assets.
- `GET /api/sessions/{id}/assets/{asset_id}`: capability-protected raster. Browser fetches blob using auth header; no secrets in query URLs.

Capture view: `{id,slot,status,revision,epoch,created_at,frames:[{asset_id,t_ms,width,height}],result:VerificationResult|null,error:null|string}`. Status queued/running/accepted/rejected/superseded/failed. Frontend polls snapshot while work pending; event list provides understandable activity, not raw SQL internals.

## Repository module contract (repository.py)

Implement `Repository(dsn)` with `initialize()`; each method uses short independent PostgreSQL transaction. No external IO under DB locks.

- `create_session(recipe, mode, object_a, object_b) -> {token,session}`; generate capability and hash only in DB.
- `authenticate(session_id,token) -> bool`.
- `snapshot(session_id) -> dict` includes captures/events/pair; no token hash.
- `resume(session_id,expected_epoch) -> dict`.
- `submit(session_id, metadata:dict, frames:list[dict], fingerprint:str) -> dict` returns `{capture,session}`. Each frame includes `asset_id,t_ms,width,height` and optional server-only `fixture_detections` for replay. Root enforces mode/shape/file validation before calling; repo enforces state/idempotency/revision/admission atomically.
- `claim(worker_id,lease_seconds=90) -> dict|None`: `{id,lease_token,capture,session,attempt}`. Claims/reclaims job, fences with fresh token, bounded attempts, latest pending preview per session; fairness among preview sessions. Capture returned includes internal fixture evidence.
- `heartbeat(job_id,lease_token,lease_seconds=90) -> bool`.
- `finish(job_id,lease_token,result:dict,frames:list[dict]) -> bool`: atomically apply only current exact committed revision or monotonic fresh preview epoch; store observations and result, preserve stale audit. For baseline accepted set baseline_ready; counterpart accepted ready; recovery accepted clears flag. Preview never changes durable acceptance. Missing current lease/expired lease cannot apply.
- `fail(job_id,lease_token,code:str,retryable:bool=False) -> bool`: bounded retries, sanitized failure only.
- `publish(session_id,expected_revision) -> dict`: one immutable manifest/pair and separate export intent, no HTTP call.
- `reserve_budget(provider,request_key,units:int,limit:int) -> bool`: atomic provider-wide reservation; same key replay doesn't double reserve. Retries with potentially new external work require new keys. Keep ambiguous usage counted.
- `metrics() -> dict`: factual counters job states/stale drops/reservations; no invented latency metrics.

Repository raises `Conflict(code,message)`, `NotFound`, `CapacityExceeded` (define in module) for API mapping. It owns schema SQL and repository tests. Root owns API/worker/storage/config/CLI and tests of those. CV agent owns verification and fixture generator/tests. Provider agent owns providers module/tests. UI agent owns web/.

## Provider module contract (providers.py)

`RoboflowDetector(api_key:str,model_id='coco/39',timeout=20)` with `detect(image_bytes:bytes,width:int,height:int) -> dict`: `{detections:[{label,confidence,box}],model:{provider:'roboflow',model_id,...},latency_ms}`. Header-only auth; one HTTP attempt per invocation, no hidden retries. Parse and validate normalized boxes and returned image dimensions; invalid output fails sanitized `ProviderError(code,retryable=False)`. Credentials never appear in exception text.

Implemented synchronous `RoboflowExporter(api_key,workspace,project,timeout=30,client=None)` exposes `upload(image_bytes,asset_id,pair_id,session_id,split,media_type)`, `reconcile(asset_id,media_sha256,expected_pair_id,expected_session_id,expected_split)` and `annotate(image_id,detections,width,height)` using keyword parameters as defined in source. Annotations are VOC predictions, never implicitly human ground truth. Dataset query-key auth is a documented narrow exception with redacted logs and sanitized errors. No default project creation or camera export.

`ExportWorker.once()` uses `export_claim`, fenced `export_record_asset`, `export_heartbeat` and `export_finish(...,delay_seconds=...)`. One external operation per iteration; missing search results receive persisted bounded read-only backoff, incompatible results stop as uncertain. Local manifests now include a deterministic session-level split, and downloaded ZIPs also contain `examples.jsonl` with machine-verified VQA rows derived from recorded decisions. Actual setup/evidence is in EXECUTION_STATUS.md.
