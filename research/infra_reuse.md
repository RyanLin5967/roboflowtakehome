# Counterpart infrastructure: targeted reuse and implementation recommendation

Research date: 2026-09-26. Scope is the selected Counterpart build, not another idea search. No implementation, cloud compute, performance measurement, or product-quality claim is established by this document. Existing notes at `../roboflow-labs-takehome-research/round6/COUNTERPART_INFRA.md` informed the requirements. Primary docs and package registry metadata were checked today; Exa was also used to find relevant implementations. Exa raw response is temporary research material at `/tmp/counterpart-infra-exa.json`, without credentials.

## Decision

Use one transactional Postgres database, a Python API, stateless cloud perception workers, and immutable media storage. Implement a deliberately small product-specific queue with fenced leases for committed verification/export. Keep disposable preview slots separate. Reuse mature HTTP, database, validation, and testing libraries. Do not build an orchestration framework, message broker, event-sourced everything, or model-serving stack.

This choice makes the actual hard parts visible and testable: stale perception cannot accept the wrong scene, committed evidence survives interruptions, repeated requests do not count a pair twice, previews remain useful under overload, and a published pair is complete. The queue is established infrastructure, not the claimed invention.

The alternative is not inherently inferior: DBOS is a strong option if the durable pipeline gains many steps, durable timers, branching retries, or long human waits. Today the core durable graph is small: verify committed evidence, finalize a local pair, export its assets, reconcile receipts. A workflow library would still need the complete physical-scene revision protocol and custom preview admission policy. Keeping both systems would increase integration surface before it removes much product code. Revisit this decision if custom workflow control grows beyond a few explicit states.

## Existing implementations compared

| Option | Reuse value | Counterpart decision |
| --- | --- | --- |
| Postgres row locks, constraints, transaction | Claim jobs without holding transactions during inference; atomically write domain state plus queued work | Use directly. `SKIP LOCKED` is explicitly intended for queue-like consumers, not a consistent analytical snapshot. [Postgres SELECT](https://www.postgresql.org/docs/current/sql-select.html) |
| DBOS 3.1.0, MIT | Embedded durable workflows; transactional enqueue; concurrency, rate limits, partitions, deduplication; no separate orchestration server | Credible next option, not mandatory initially. Do not imply DBOS cannot do fair queues or transactional enqueue. [Queues](https://docs.dbos.dev/python/tutorials/queue-tutorial), [architecture](https://docs.dbos.dev/architecture), [source](https://github.com/dbos-inc/dbos-transact-py) |
| Temporal Python 1.33.0, MIT SDK | Proven durable orchestration and retry/heartbeat lifecycle | Operationally excessive for this small graph unless already provided by deployment. Its activities still need idempotency; it cannot make Roboflow HTTP uploads atomically commit with our database. [Activities](https://docs.temporal.io/activities), [self-hosted deployment](https://docs.temporal.io/self-hosted-guide/deployment) |
| Procrastinate 3.10.0, MIT | Python/Postgres task queue; asyncio, retries, locking, periodic work | Reasonable generic job reuse but would coexist with bespoke disposable previews and domain fencing. Prefer one explicit mechanism initially. [Source and license](https://github.com/procrastinate-org/procrastinate), [worker integration](https://github.com/procrastinate-org/procrastinate/blob/main/docs/howto/basics/worker.md) |
| `LISTEN`/`NOTIFY` | Reduce idle polling latency | Optional wakeup hint, never durable evidence or sole dispatch source. Notifications arrive only to listening clients; workers must reconcile persistent rows after startup and periodically. [Postgres NOTIFY](https://www.postgresql.org/docs/current/sql-notify.html) |

DBOS specifically documents that transactional database steps can execute exactly once while external steps may execute at least once. It also exposes enqueue inside the same Postgres transaction as business writes. Counterpart should borrow that separation, even without adopting its runtime. [DBOS outbox example](https://docs.dbos.dev/python/examples/outbox)

Temporal can be run as local binaries or a development server; do not claim Kubernetes or Elasticsearch is intrinsically required. The reason to defer it is another persistence/control service and workflow operational surface, not an impossible resource requirement. Its official deployment guidance distinguishes local development from sustained workloads. [Temporal deployment](https://docs.temporal.io/self-hosted-guide/deployment)

## Concrete package pins

These are current versions verified through each package's official PyPI JSON endpoint today, not a claim that this combination has already passed installation or tests. Root should resolve and lock the complete dependency graph once and record any necessary compatibility adjustment.

| Dependency | Proposed pin | License from package metadata | Purpose |
| --- | --- | --- | --- |
| Python | 3.12, pin installed patch in toolchain | PSF | Already compatible with project family; do not broaden Python support without checks |
| PostgreSQL | 17.11 locally | PostgreSQL License | Local binary confirmed; supported major, no upgrade needed for queue semantics |
| FastAPI | 0.141.1 | MIT | Typed HTTP contract, generated OpenAPI |
| Uvicorn | 0.54.0 | BSD-3-Clause | ASGI serving |
| Psycopg | 3.3.6 | LGPL-3.0-only | Bound parameters, explicit transactions |
| psycopg-pool | 3.3.3 | LGPL-3.0-only | Bounded async connections |
| Pydantic | 2.13.5 | MIT | Validate all externally supplied data |
| pydantic-settings | 2.15.0 | MIT | Explicit settings, external secrets |
| HTTPX | 0.28.1 | BSD-3-Clause | Bounded provider requests, reusable connections |
| pytest | 9.1.1 | MIT | Unit and real-Postgres integration checks |
| pytest-asyncio | 1.4.0 | Apache-2.0 | Async API/worker tests |
| Hypothesis | 6.168.1 | MPL-2.0 | State-machine fault sequence exploration |
| RESPX | 0.23.1 | BSD-3-Clause | Provider HTTP failure fixtures |

Package records: [FastAPI](https://pypi.org/project/fastapi/0.141.1/), [Uvicorn](https://pypi.org/project/uvicorn/0.54.0/), [Psycopg](https://pypi.org/project/psycopg/3.3.6/), [pool](https://pypi.org/project/psycopg-pool/3.3.3/), [Pydantic](https://pypi.org/project/pydantic/2.13.5/), [settings](https://pypi.org/project/pydantic-settings/2.15.0/), [HTTPX](https://pypi.org/project/httpx/0.28.1/), [pytest](https://pypi.org/project/pytest/9.1.1/), [asyncio](https://pypi.org/project/pytest-asyncio/1.4.0/), [Hypothesis](https://pypi.org/project/hypothesis/6.168.1/), [RESPX](https://pypi.org/project/respx/0.23.1/). PostgreSQL [version policy](https://www.postgresql.org/support/versioning/) and [license](https://www.postgresql.org/about/licence/).

Use unmodified packages and retain their notices; no source copying is needed. Do not describe every dependency as permissively licensed: Psycopg is LGPL and Hypothesis MPL. This is provenance metadata, not a legal compatibility opinion.

## Minimal durable contract

This section is an original implementation proposal, not a description of existing library behavior.

### Identity and invariants

Each session has an opaque authenticated capability, task revision, recipe/verifier version, scene epoch, and monotonically increasing state revision. Each committed capture has an immutable capture ID, content digest, media metadata, client sequence identity, and server acceptance time. Each inference attempt records the exact capture identity, provider/model version, verification parameters, job attempt number, and output digest. Never allow a client to submit trusted detections as real provider evidence.

Use request idempotency keys scoped to actor/session/operation, storing both a canonical request hash and the response. Same key plus same request returns the saved result; same key plus different request is a conflict. A uniqueness constraint on the successful pair identity prevents duplicate counting independently of HTTP retries. Completed pair revisions are immutable; a changed verifier creates another decision revision rather than silently replacing the historical claim.

Preview correctness is different from capture correctness. A preview result can be shown if it is from the current scene epoch, newer than the last shown result, and within a stated age bound. It need not equal the newest submitted preview: with continuous capture faster than inference, that rule would discard every answer. Display observation age and clear actionable approval when too old. A preview cannot authoritatively accept a committed capture. Acceptance requires exact immutable capture/digest, applicable revision/version, current session transition preconditions, and current worker fence.

### Transactions and worker leases

Keep transactions short and never hold a connection lock across HTTP inference. The capture metadata write, verification-job insert, state event, and budget reservation (if reservation occurs on enqueue rather than dispatch) must be atomic. Media must already be durably finalized before its metadata is eligible for a job.

Claim a pending or expired job using `FOR UPDATE SKIP LOCKED`, update lease owner/token and DB-clock expiry, increment attempt, and commit. After remote work, lock the job/session in a consistent order, require the same lease token and eligible session/capture revision, persist immutable inference evidence, and conditionally apply the result. An expired worker may record diagnostic evidence if safely keyed, but may not mutate authoritative state or renew a lease it no longer owns. Heartbeat and renewal also require the current token. Every mutation follows the same lock ordering to limit deadlocks; bounded retries handle serialization/deadlock errors where safe.

A lease prevents concurrent current owners, not overlapping external calls: a paused worker may still finish a cloud call after its lease expires. The claim is at-most-once local acceptance, with retryable remote execution. Count possible duplicate remote attempts in cost accounting. An HTTP request timeout or cancelled coroutine does not prove provider execution stopped.

Use explicit finite job states (`pending`, `running`, `retry_wait`, `succeeded`, `failed`, `uncertain`) and separate domain acceptance state. Classify permanent schema/auth/unsupported-model failures; do not retry every exception. Exponential retry with jitter and a maximum attempt/age budget is adequate. Non-idempotent ambiguous exports enter `uncertain` for reconciliation, rather than immediately uploading another copy.

### Preview scheduling and overload

Allow one active preview and one replaceable pending preview per session. Pending preview replacement is safe only for stateless observation. Store latest payload identity and sequence in one slot; never create an unbounded history of preview jobs. Committed stills and all frames needed to verify a committed temporal clip use a separate durable path and cannot be evicted by preview coalescing.

Among eligible sessions, dispatch those least recently served; rate-limit each collector. Bound global in-flight inference and preserve some capacity for committed verification. Export cannot starve live feedback, and continuous previews cannot starve the collector's committed result. A simple configurable weighted service policy is sufficient; do not invent a scheduling research claim.

If load exceeds admitted capacity, lower requested preview FPS or refuse new live admission explicitly. Never silently accept endless work. Use DB-coordinated provider slots if running multiple workers; a per-process semaphore is not a global limit. Slot expiry must account for uncertain still-running remote calls and must not falsely promise a hard provider concurrency cap under arbitrary network partitions.

A temporal model/tracker needs its own ordering contract. This initial architecture favors independent object detection of committed frames, followed by sequence verification. If future tracking uses persistent memory, key it by immutable clip/epoch and reset on gaps; latest-frame dropping is not compatible with assumed continuity.

### Media, reconnect, and publication

Store originals by server-computed digest. For a local adapter, stream to a temporary file with size limits, validate media, finalize atomically in the same filesystem, then reference it in a DB transaction. For S3 use conditional creation and confirm content identity; conditional writes can reject overwrite races. Interrupted writes may leave harmless unreferenced objects, so garbage collection needs a grace period and a reference check, not immediate deletion. [S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)

A resumed browser restores durable captures but cannot restore the physical scene. Increment/rebind the scene epoch and require a fresh observed grounding before continuing scene-dependent guidance. If the first committed capture remains valid, preserve it; do not force total restart. Old-epoch messages become visibly stale and cannot approve the new attempt.

After verified evidence is complete, finalize one immutable local manifest that binds both sides, intended change, named controls, artifacts, timestamps, model/verifier/config versions, and split group. Keep all related captures/pairs from a session in one split. Local pair validity and Roboflow export status are separate states. For provider publication, write an outbox row in the same transaction as the export request, retain deterministic external identity, save each asset receipt, reconcile ambiguous responses through actual supported provider reads, and only mark export complete after all required receipts exist. Roboflow may show individual uploads earlier; our manifest alone is the complete-pair authority. If the provider lacks lookup/idempotency support, expose unresolved export status rather than claim exactly-once uploads.

### Budget and observability

Reserve an upper-bound cost/allowed-call allowance transactionally before dispatch. Concurrent workers must not independently read the same remaining balance and each spend it. Retain reservations for ambiguous provider execution until reconciled, and charge retries separately. Provider-enforced hard stops remain necessary because local accounting cannot cover other account usage, delayed invoices, or a stale rate schedule. With $3.39 Modal left, long-lived idle GPU serving is unjustified without a proven bounded deployment.

Log structured IDs and outcome reasons, never request authorization, image bytes, signed URLs, or provider keys. Persist immutable events that make the demo explainable: request submitted, preview superseded, job leased, worker expired, result stale, capture accepted, pair complete, export uncertain/reconciled. Measure feedback age, queue delay, provider duration, accepted/rejected/uncertain evidence, stale-result count, bounded queue depth, and per-session service—not just API response time.

## Required meaningful tests

| Test | Failure injected | Required result |
| --- | --- | --- |
| Concurrent duplicate commit | Two processes submit same idempotency key | One capture/job and same response |
| Conflicting retry | Same key, different bytes/recipe | Conflict, no silent substitution |
| Lease theft | Pause old worker; expire/reclaim; release old worker | Only new fence can apply state |
| Scene change | Old valid result returns after scene epoch advances | No acceptance in new scene |
| Continuous previews | Capture faster than inference | Monotone bounded-age feedback still appears; queue stays bounded |
| Queue fairness | One collector floods while others submit | Others make measurable progress; no unbounded memory |
| Committed evidence priority | Flood previews during committed clip verification | Committed frames retained and verification completes |
| Crash after cloud response | Kill worker before DB receipt | Retry/uncertain path preserves correctness and accounts possible duplicate cost |
| Crash after first export asset | Kill process before second upload | No complete export claim; receipt resumes/reconciles |
| Transaction rollback | Fail after capture insert before job insert | Neither authoritative write survives |
| Object-store interruption | Partial upload or failed DB commit | No referenced partial object; orphan cleanup eventually safe |
| Browser reconnect | Reload/offline while first capture accepted | First evidence retained; physical scene re-grounded |
| Malformed provider data | Missing classes, nonfinite coordinates, malformed JSON, wrong dimensions | Typed error/uncertainty, never acceptance |
| Budget race | Many workers reserve against small balance | Total reserved allowance never exceeds configured cap |
| Session isolation | Wrong capability reads or mutates another session | Denied; no media/state leakage |
| State-machine property test | Generate retries, disconnects, cancellation, revision changes | No incomplete published pair, duplicate accepted pair, or stale approval |

Run locking/race tests against a real isolated Postgres instance, not SQLite or mocks. Use a separately launched worker subprocess for at least one abrupt termination and recovery test. Fake-provider tests demonstrate protocol behavior only; actual Roboflow responses and actual human captures are distinct evidence gates.

For a replay load experiment, compare against a competent bounded FIFO/rate-limited policy under identical offered sessions, preview timestamps, provider latency trace, and global budget. Report p50/p95 observation age, drop/supersede rate, committed completion time, and worst-session service interval. Label replay and injected delay clearly. Do not invent performance targets or cloud-scale claims before measurements.

## Build order

1. Freeze API identity, domain states, and provider evidence schemas. Write invariant tests before splitting module ownership.
2. Implement immutable media and transaction-backed session/capture/job state; test concurrent requests and lease fencing.
3. Implement replaceable preview scheduling and bounded provider adapter; test latency, fairness, and budget failures with a fake transport.
4. Integrate actual provider after current credits/controls are verified, capture real evidence, and record unresolved CV limits honestly.
5. Add outbox export with receipt reconciliation only for capabilities verified in Roboflow API/source.
6. Add explicit failure demo and measured replay; verify browser recovery and physical-scene re-grounding on the real capture flow.

No test in this table has been run by this research agent. Root owns final architecture decisions and implementation evidence.
