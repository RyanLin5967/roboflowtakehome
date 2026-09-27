# Complete-product acceptance contract

The user explicitly rejected treating the initial capture kernel as the full product on2026-09-26. Continue under BUILD_LOOP.json. Completion means a data operator can request a defined collection, collectors can perform and repair it, the operator can independently review and release useful paired data, and the system can recover without corrupting that data. More services or recipes alone do not satisfy this.

## Product journey

1. Operator creates a campaign with a frozen supported recipe/object-role configuration and target count. Catalogue explains what is checked and what remains uncertain.
2. Operator issues a bounded, expiring, revocable collector invitation. Phone/desktop join without sharing the Roboflow key. Repeated join is idempotent and assignment limits hold under concurrency.
3. Collector receives generated instructions; saved baseline, actual rejected evidence and a specific repair remain visible. Browser/network interruptions preserve local upload identity and server evidence. A provider failure retries saved evidence instead of forcing recollection.
4. Every session remains discoverable. Operator sees accepted, incomplete and failed attempts with progress and provenance; collector can recover earlier local sessions.
5. Human review is a distinct append-only decision over immutable machine evidence. Optimistic review versions prevent two reviewers silently overwriting each other. Blind review can hide machine decisions for independent evaluation.
6. Operator releases only explicitly selected approved pairs as a versioned dataset: original evidence, machine observations, human decisions, question/answer rows, shared pair/session splits and reproducible membership digest. Replay cannot pass as real collected data.
7. External export is explicit and destination-bound. Enabling a global adapter must not upload historical camera pairs. Each release records privacy/destination/receipts; partial or ambiguous effects remain visible and recoverable.

## Necessary infrastructure

- Per-frame hosted inference checkpoint/cache keyed by exact immutable bytes and detector configuration; bounded cross-process dispatch, budget reservation for every possible call, crash recovery reuses successful evidence and preserves ambiguous cost.
- Current pending preview policy and exact committed acceptance fences retained. Progress meaningful on long sequences. Tests address noisy collectors and work under stated admitted load rather than invented large-scale claims.
- Shared S3-compatible immutable object storage with actual emulator roundtrip/failure tests. Local filesystem adapter retained for simple demo.
- Operator authentication, secure cookie/origin/host policy, invitation admission, explicit public-mode configuration. No unauthenticated public creation capable of spending the model allowance.
- Bounded media/archive handling and retention/deletion of obsolete previews/orphans without breaking committed evidence or other sessions' shared assets. Backups/restores and schema migration path.
- Reproducible deployment artifact with persistent PostgreSQL/media, nonroot processes, health checks, TLS camera requirements and documented secret injection. State accurately which boot/deployment tests ran.

## Evidence gates

- Real DB concurrency/fault tests, provider protocol tests, actual browser journeys for operator+collector+review+release and phone layout.
- Actual Roboflow checks stay separate from mocks/replay. No local model compute.
- Frozen evaluation harness consumes independent labels across complete session history, including rejection/failure denominators. Published-only data cannot estimate recall/coverage.
- Real physical scene trial must be acquired before claiming collection quality or speed. A public image challenge is useful perception evidence, not a substitute for a real collector trial.
- Final demo leads with useful capture/repair and reviewed output; architecture and failure evidence support it.

## Boundaries

Not a general crowdsourcing marketplace, causal inference engine, new detector, or arbitrary natural-language verifier. Two well-tested parameterized recipes can satisfy the product if the complete collection/review/release path is real. We do not build a dozen weak demos to inflate scope. No public deployment, private dataset entitlement or physical observations are fabricated when external access is missing.
