# Frozen perception contract

Every new campaign and session records a canonical detector contract before any
capture is dispatched. Campaign invitations copy the campaign's contract; they
do not read a joining collector's or the current worker's preferred model.
Standalone creation records the API's configured detector. The immutable pair
manifest carries the same contract.

The digest covers the provider, versioned model identifier, endpoint, adapter
namespace/version, normalization version, confidence threshold and disabled
active learning. Credentials, queue concurrency, network timeout and budget are
not perception semantics and are excluded. A wire-level fake-HTTP test compares
the actual built-in request path and parameters with the recorded contract.

A worker compares its actual runtime identity with the saved session before
entering the inference cache or reserving any provider usage. A restart with a
different model or adapter therefore refuses that capture with
`execution_contract_mismatch`; it cannot silently change the later half of a
pair. Returning to a compatible worker permits an explicit saved-capture retry.
The runtime also detects mutation of its adapter's identity and returns detached
configuration dictionaries so observation consumers cannot change its cache
namespace accidentally.

Fresh provider responses and cache hits must name the expected provider/model.
Existing baseline and counterpart observations must also carry the expected
configuration digest and consistent optional normalization/threshold metadata.
The archive consumer shares that observation validator. Cache identity includes
the complete configuration and image dimensions as well as exact asset bytes.
Remote errors and ambiguous attempts retain their usage reservations.

Operator authorization of an uncertain baseline does not waive the model pin.
The original refused decision and model observations stay unchanged. The
collection authorization is copied into each admitted counterpart/recovery
capture and checked against the separately immutable authorization record. A
replacement baseline clears only the active authorization, and saved retries
retain the original capture authorization and epoch. Such evidence always needs
the human-reviewed publication path, even if a later machine check passes.
Unresolved recovery stays recorded rather than becoming an implied scene check.

A PostgreSQL trigger rejects changes or removal of a non-null session contract.
The campaign contract is immutable too. Session/campaign creation and invitation
admission use their existing transactions, so no first worker wins an implicit
model-selection race.

## Historical evidence

Schema initialization leaves old contracts null; it never fills them from
current settings or rewrites old pair/release manifests. A legacy session refuses
inference until explicitly migrated. A session whose recipe is also unsupported
may refuse earlier with the recipe error; both paths avoid provider calls.

`Repository.migrate_execution_contract` takes the session's expected revision,
a named operator and a reason. It locks the session, refuses an active capture
lease, and considers every recorded observation plus completed per-frame calls
left behind by a crashed worker. It adopts a contract only if all recorded
configurations establish one consistent identity. Unknown or conflicting
provenance refuses migration. An untouched legacy session can receive an explicit
server-side contract declaration through the repository API; the operator HTTP
operation only derives from evidence and does not accept arbitrary model JSON.
A concurrent competing declaration loses once the first immutable pin commits.

One exact historical mapping is supported: the original built-in
`counterpart.providers.RoboflowDetector`, `serverless-v0-v1`, `rf-xyxy-v1`,
confidence 0.25 and disabled active learning had a fixed
`https://serverless.roboflow.com` endpoint in source, but omitted the endpoint from
its cache configuration. Explicit migration may add this known endpoint to the
new contract while retaining the original observation digest and bytes. The
audit event records that mapping. Unknown adapters or versions cannot use it.

Migration adds an audit event and the previously absent session pin; it does not
reinterpret, overwrite or republish old captures, decisions, pairs or releases.

## Evidence and limits

Real temporary-PostgreSQL tests exercise a model A baseline followed by a model B
worker (zero extra calls/reservations), competing legacy declarations,
immutability, old endpoint mapping, incomplete provenance refusal, partial-call
recovery and preservation of historical observation bytes. Independent adversarial
tests poison a compatible cache entry with another returned model identity and
verify refusal on both fresh and cached paths. All detector traffic in these
tests uses fake local HTTP transport; no cloud model execution is claimed.

This pins the versioned provider identifier and Counterpart's request semantics.
It cannot cryptographically prove that a hosted provider never changes weights
behind its versioned endpoint. The saved contract and original observations make
that dependency explicit; they are not remote weight attestation.
