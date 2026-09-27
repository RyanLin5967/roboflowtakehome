# Collection lifecycle and evidence contracts

Implemented in `src/counterpart/collections.py`, `collection_schema.sql`, and the capture repository. Operator authorization is enforced at the HTTP boundary; this repository is a trusted application component for one workspace, not a tenant-auth system.

## Collection → invitation → session

A collection freezes the exact versioned recipe, object roles, mode and target when created. The target counts **currently human-approved pairs**, not sessions, uploaded frames or machine acceptance. Published, pending-review, rejected and revision-requested counts remain separate. Existing sessions can finish after the target is met; the target is a collection goal, not a guarantee against concurrent in-progress work overshooting it.

Invitation bearer tokens are issued once and only SHA-256 hashes are persisted. Expiry is bounded to 30 days and each invite admits at most its configured session count. Admission locks the collection and invite, creates the session and quota receipt in the same transaction, and persists the exact recipe snapshot. Failed admission leaves neither a consumed quota nor an orphan session.

A duplicate join uses the same request key to recover the original session and capability. The capability is derived by HMAC from the secret invitation and created session ID; recovering a lost response does not require plaintext bearer-token storage. An expired or revoked invitation cannot create or recover credentials. Revocation controls the invitation; already issued session capabilities continue to work. Operator session browsing never returns collector secrets.

Pausing a collection prevents new joins and new capture work. Capture admission checks the collection row inside the same transaction as the session revision and queued job, so an API preflight race cannot admit work after the pause commits. Already admitted jobs may finish. Readback, duplicate receipts, review and release access remain available. Existing join receipts can still be recovered while paused. Resuming collection activity does not pretend the physical scene is unchanged; normal session recovery remains in force.

## Capture history and saved retry

Every new session stores a canonical frozen recipe snapshot. Historical sessions without one remain explicitly null; migrations never invent historical verifier parameters from current defaults.

Each counterpart, recovery and preview attempt records the baseline capture it referenced when submitted. Replacing the baseline does not erase or relabel earlier attempts. The complete evaluation export contains every saved attempt, its original assets, timing, result, completion time and reference. No current-state-only reconstruction is presented as historical truth. Older captures without a recorded reference remain unknown.

Only the latest failed saved committed observation can be retried. Retrying creates a new attempt/job and preserves the old failed record. The new attempt records `retry_of_capture_id`, uses the exact original frames and capture epoch, and receives a new revision fence. A baseline captured before a reconnect can finish verification, but the collector must re-ground the scene before taking the next observation. A rejected visual result is not silently converted into a transport retry.

## Machine verification, human review, and releases

Publication freezes the pair manifest and machine decisions. Human review is separate state plus an append-only audit. A review requires the expected version, a reviewer identity, a request key and a reason for rejection/revision. Concurrent decisions serialize on the pair: one succeeds, stale decisions return a conflict. Replaying an identical request returns its recorded decision. Changing its payload cannot overwrite history.

A dataset release selects currently approved pairs and freezes their manifests **and review revisions**. Its authoritative review is the release wrapper, not the historical pair's `provenance.human_reviewed=false`. That flag truthfully describes the original machine-publication event and is never rewritten. Later review changes affect future releases, not an existing release.

Release idempotency is scoped to collection/request key; selecting different pairs with the same key conflicts. Membership and review state are read in one database statement. The content digest includes that membership, exact recipes and review revisions but excludes random release ID and creation time, so the same membership has the same digest.

Members of each pair retain the recorded session-level split. If an exact saved image hash appears in different splits among selected pairs, release creation fails with `split_leakage`; it never silently changes immutable manifests. Identical assets within one split are permitted. This gate detects exact-byte leakage, not perceptual duplicates, shared scenes, or near-duplicate camera frames.

Replay releases require explicit opt-in and retain fixture provenance. They cannot be uploaded as real provider datasets.

## Explicit cloud export

Publishing a live pair produces `local_only` and a disabled export intent. Enabling a global integration does not retroactively upload saved camera captures. Export requires a named, currently approved live pair and a destination whose identity and private visibility were checked server-side. The intent freezes workspace/project/visibility and cannot be redirected on retry.

The database request method trusts the server-resolved destination; it must never receive a browser's asserted privacy claim. Provider privacy verification and an external upload cannot be one transaction, so administrator visibility changes during a network operation remain a documented provider-side race.

Export requests and workers lock the intent before the pair. Durable receipts describe confirmed and uncertain external effects; nothing promises exactly-once provider requests. Legacy intents lacking a destination are never claimed automatically and require reconciliation.

## Executed validation

`COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest tests/test_collections.py tests/test_repository.py -q` passed **70 tests** after these changes. Tests use actual PostgreSQL in isolated temporary schemas and generated observations, with no model execution or cloud calls. They cover admission/quota races, rollback, revocation, optimistic review, immutable/idempotent releases, split leakage, paused admission races, historical references and saved retries.

`uv run python scripts/product_fault_probe.py` exercises two collections and eight synthetic collectors through capture, reconnect, deliberate rejection, review and release. It kills actual Worker subprocesses before media access and after ten durable fake-HTTP observation checkpoints. The replacement worker reuses all ten checkpoints with zero additional fake-HTTP requests or reservations. The default report is ignored `artifacts/product_fault_probe.json`. This is bounded orchestration/failure evidence, not detector quality, real-human collection speed or sustained deployment capacity. Lease expiry is explicitly accelerated in the test database; the shared PostgreSQL server is never restarted.
