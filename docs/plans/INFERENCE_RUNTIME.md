# Durable inference runtime

Counterpart's accepted evidence is a sequence of immutable assets. Losing a worker halfway through a sequence must not discard successful perception or charge again for every frame. The runtime checkpoints each stateless detector observation independently, while capture verification remains one fenced transaction in the existing repository.

## Implemented boundary

`InferenceRuntime(repo, media, config, detector)` owns six small PostgreSQL tables plus the existing shared Roboflow budget ledger. `observe_frames(frames, session_id=..., job_key=..., heartbeat=...)` returns observations in original input order. It keeps no local model, sends only persisted integrity-checked image bytes to the existing hosted adapter, and never accepts a capture itself.

The worker checks the frozen recipe snapshot before loading evidence or reserving cloud work. It invokes the runtime only for live captures, then runs the deterministic verifier and the existing capture/epoch/lease-fenced acceptance transaction. Replay remains a separate path and cannot populate the real detector cache.

`progress(frames)` reports completed perception frames and distinct assets for the runtime's exact configuration. It does not imply verification passed. `cache_stats()` exposes operator-only aggregate cache reuse, dispatch status and waiting counts. API readers can construct the runtime with `detector=None`; this selects the same production cache namespace without credentials or provider calls. Initialize once, then use these read methods.

## Cache identity and provenance

A cache key hashes canonical JSON containing the immutable asset SHA-256, width, height and the complete known detector configuration:

- Provider and versioned model route, currently hosted `coco/39`.
- Adapter namespace and wire-contract version.
- Normalizer version, confidence threshold and active-learning setting.

Credentials, capture timestamps, session and recipe are excluded. The first two would leak secrets or unnecessarily repeat a deterministic observation; recipes consume the same detections differently. Every returned frame keeps its own timestamp and immutable asset identity. Changes to known model/adapter/threshold/normalizer semantics require a new configuration identity.

The hosted route identifies a provider version, not a independently verified weight digest. A provider silently changing weights behind a fixed route is outside this guarantee. Stateful video tracking is also outside this cache contract: it cannot reuse frame observations without identifying the complete prior tracker state. Only this stateless object-detection adapter is wired here.

Returned observations include `inference.source` (`provider` or `cache`), the original observation ID/time, cache/configuration digests, original `provider_latency_ms`, and the current `lookup_and_dispatch_ms`. A cache hit is never relabeled as new model latency. The original provider duration is moved under provenance rather than retained as a misleading top-level latency field. Image bytes are integrity-checked even on hits.

## Scheduling and limits

Each worker schedules at most two frames from a sequence locally. A short PostgreSQL advisory transaction serializes the deployment-wide decision to acquire capacity, lease an asset and reserve budget. Cloud I/O never happens while a row transaction or that advisory transaction lock is held. Runtime database connects, lock waits and statements are bounded to 5, 3 and 5 seconds respectively; operational database failures are retryable capture failures. Distributed lease timestamps come from the database clock, so worker wall-clock skew does not decide ownership.

The default durable policy permits four current frame dispatches globally and two per collection session. The policy is stored in the database; differently configured workers fail closed instead of each asserting its own incompatible global limit. Changing these limits is an operator migration, not a per-request option.

Waiters are ordered by the session's least-recent dispatch time, then FIFO within that session. A newly waiting session gets a chance before a busy one keeps refilling all slots. Waiters for a currently leased identical asset do not block unrelated assets, and expired abandoned waiters are removed. These are work-conserving dispatch turns, not a claim of fixed latency under arbitrary provider duration or unlimited admission. Existing capture-level admission remains bounded upstream.

A successful cache observation is shared across workers and sessions. Concurrent attempts for identical bytes/configuration normally join one paid call. Failed dispatches are retryable only through the enclosing capture policy; the adapter/runtime does not silently retry its HTTP request.

## Crash and budget semantics

Each dispatch commits an immutable attempt UUID, asset lease and **one image reservation** together before making the remote call. It uses the existing `roboflow` budget account, so earlier probes and work are included in the allowance. Reservation counts are conservative potential spending, not claims about a provider invoice. A crash between reservation and the HTTP request still consumes the reservation. Timeouts, unknown responses and failures are never refunded. A retry gets a distinct attempt/reservation.

A frame lease is renewed in the background during provider I/O. A separate heartbeat renews the enclosing capture lease while frames run concurrently. If the capture lease is lost, no further frames are queued; already running successful calls may checkpoint immutable observations, but the enclosing capture cannot be accepted by the stale owner. If a frame lease expires, a replacement may acquire it; the old owner cannot overwrite the replacement's cache entry.

After one frame fails, other already running calls are drained and their successes checkpointed. Undispatched frames stop. A subsequent authorized retry reuses completed observations and pays only for missing work. Partial budget exhaustion similarly preserves completed progress; it fails the capture with an explicit budget error instead of pretending the sequence was fully verified. Increasing a budget requires an operator decision; the ledger is never reset.

The global limit bounds **current database dispatch leases**, not unknowable remote activity after a process crash or network partition. An upstream service may continue computing a request after the client disappears. Another attempt after lease expiry can therefore overlap or repeat remote work; both are reserved and the stale result is fenced. Exactly-once remote execution and guaranteed provider cancellation are not claimed.

## Worker presence and retention

A durable worker registry distinguishes a running background consumer from an API
whose database is reachable but whose jobs have nobody to claim them. `Worker.run`
registers a process generation and renews it every five seconds on a separate
thread, including while idle or inside a slow job. The operator-only
`worker_status()` returns `active_workers`, `last_seen`, and
`stale_after_seconds` (15). Graceful shutdown immediately marks the generation
stopped; an abrupt exit ages out of active status. An old process cannot renew or
stop a replacement that reused its name. This is process liveness, not proof that
the provider is reachable, funded, or accurately detecting objects. One-shot
`Worker.once()` calls deliberately do not advertise standing capacity.

Registration prunes at most 100 registry rows older than 24 hours. It never deletes
spending, attempts or observation history. Model observations currently remain in
the cache after unreferenced original images are collected; derived-data retention
is distinct from media retention. These cache entries do not keep unused media
alive forever, and they cannot bypass the requirement to possess and
integrity-check the original bytes before using an observation. Explicit privacy
purges must consider both media and derived data; accounting reservations must
remain durable so deleting assets cannot reset the spending allowance.

## Failure checks executed locally

All detector outputs in these checks are explicit test doubles or mocked HTTP responses. No local ML and no cloud requests were used.

The runtime tests cover:

- Same-asset requests from independent workers joining one observation and reservation.
- Parallel completion preserving original frame order/timestamps.
- Partial sequence failure and missing-frame-only restart, including another in-flight success.
- A real child process exiting inside its mocked provider call, followed by actual lease expiry and replacement dispatch; both attempts remain counted.
- Explicit stale-owner result rejection and model/normalizer identity separation.
- Global/per-session concurrency, fair next-session dispatch, and a six-worker budget race.
- Heartbeats during I/O longer than a frame lease, lost capture ownership and abandoned waiters.
- Media corruption rejection even when an observation is already cached.
- Zero budget, partial budget, conflicting worker policies and safe persisted error codes.
- Progress counting repeated images without manufacturing additional model calls.
- Worker liveness while idle and occupied, graceful stop, stale age-out, generation fencing and bounded registry cleanup.

Worker integration checks cover mocked hosted transport through API, immutable storage, worker, verifier and publication; incompatible frozen recipes fail before provider work; transient shared-storage failures retry without cloud spending. The concurrency suite found an initialization/dispatch DDL lock race; initialization now takes the same dispatch barrier before schema changes.

Reproduce:

```sh
COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest \
  tests/test_inference_runtime.py tests/test_adversarial.py -q
```

Tests create random schemas and remove them. They do not truncate or reset the development budget account. Physical detector accuracy and capture usability still require real desk captures; these tests establish infrastructure behavior, not vision quality.
