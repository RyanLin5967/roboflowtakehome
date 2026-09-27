# Evidence operations and recovery

This runbook covers application state and original camera bytes together. A database dump without the referenced media is not a complete Counterpart backup. Credentials, camera data and database archives stay outside the source checkout.

## Routine maintenance

The CLI defaults to reporting candidates without deleting anything:

```bash
uv run counterpart maintenance --limit 500
```

Inspect both reports. When the selection is appropriate, the same command with `--apply` removes only eligible disposable previews and old unreferenced media:

```bash
uv run counterpart maintenance --apply --limit 500
```

Defaults retain preview records for at least 24 hours and protect media modified within the last 24 hours. The underlying functions refuse a grace/retention interval shorter than one hour. Each invocation is bounded to 500 preview rows and 500 inventory entries by default; the maximum supported batch is 1,000. An inventory page may contain only referenced or recent files and delete nothing. If `media.has_more` is true, continue with the returned `media.next_cursor`:

```bash
uv run counterpart maintenance --apply --limit 500 --after-asset-id RETURNED_DIGEST
```

Reset the cursor for the next complete sweep. Do not retain a cursor forever: an object skipped as recent during one sweep may become eligible later. The storage iterator still may need to traverse an earlier listing prefix to reach the cursor; this bounds deletion batches and in-memory candidate sets, not the backend's total enumeration work.

Preview pruning requires a terminal capture and no active/retryable job. It preserves every baseline, counterpart and recovery attempt, including rejected examples, failures and superseded committed attempts. It also preserves preview rows still pointed to by a session. Jobs are deleted in the same transaction as eligible preview rows, and a per-session `previews_pruned` event records the affected IDs. Budget reservations are never reduced by retention.

Media remains protected when its hash appears anywhere in original frames, observed frames, saved verification evidence, pair manifests or immutable release manifests. A shared image survives as long as any of those references remains. Files with unexpected names, symlinks, directories and temporary upload paths are not treated as ordinary media. The current inference cache is opportunistic detector output, not a separate promise to retain otherwise unreferenced original images indefinitely.

`delete_failed_assets` reports provider/storage failures without provider diagnostics or credentials. A partial run can safely be inspected and retried. Successful storage deletion is not a multi-object transaction; a crash can leave part of a candidate batch for the next run. Never use bucket-wide expiration on the shared media prefix: old committed evidence is still needed.

## Excluding the upload/reference race

An old orphan is not necessarily safe to delete merely because its timestamp is old. A new upload can deduplicate to the same bytes before its database reference is committed.

The API therefore holds `media_reference_guard` from the first durable media write until capture submission commits. It is a shared PostgreSQL advisory lock scoped to the application's database schema. Maintenance takes the corresponding exclusive lock with a nonblocking attempt. Active uploads or another maintenance owner produce `MaintenanceBusy`; retry during an idle interval. New uploads during exclusive maintenance also fail promptly with a retryable response, preserving their saved upload rather than occupying a request thread until the backup finishes. The lock connection closes on process exit, including a crash. Concurrent collectors can hold shared guards together.

All application instances that share a media namespace must use the same database/schema and write guard. A bucket prefix or filesystem directory must belong to that one application database/schema. The barrier cannot coordinate direct object-store writers, shell copies, another database or unrelated bucket-lifecycle rules. Stop those writers before destructive maintenance. Keep arbitrary write/delete access away from the runtime identity.

With versioned S3 storage, `delete` inserts a delete marker for the current object. Historical versions can remain stored and billed. This maintenance process deliberately does not purge old object versions; coordinate version retention with backup and recovery policy separately. Use a scoped maintenance identity with list/delete rights; the normal collection service need not have delete permission.

## Creating a complete backup

Use a new private directory outside every Git checkout. The parent directory must already exist. `pg_dump` must be installed and compatible with the running PostgreSQL server.

```bash
mkdir -p "$HOME/counterpart-backups"
chmod 700 "$HOME/counterpart-backups"
uv run counterpart backup --destination "$HOME/counterpart-backups/before-upgrade-2026-09-26"
```

The command refuses an existing destination, acquires the exclusive media barrier, archives the exact current application schema with `pg_dump --format=custom`, then copies integrity-checked media. It includes original assets even if they are not yet referenced. No upload can introduce new referenced bytes while the barrier is held, and another coordinated GC cannot remove existing bytes. Worker decisions may continue during the database snapshot; their frame bytes already exist and are included. Use a lower-traffic interval so collectors rarely encounter retryable maintenance responses.

The output contains `database.dump`, `assets/<sha256>` and `backup.json`. Directories use owner-only access and files use mode `0600`. The manifest records the archive hash and each asset's hash and size. A temporary private libpq service file passes the DSN to PostgreSQL tools; database passwords never appear in command arguments or the durable bundle. The temporary file is deleted after use.

An `.incomplete` marker remains until the database and every asset have been copied and synced. Missing referenced media or an integrity failure prevents a complete manifest. Treat any bundle with `.incomplete` as incomplete, even if some files exist. Do not automatically remove that directory and overwrite it; investigate the failure and create a new backup destination.

The helper copies one application schema and its media, not roles, global PostgreSQL settings, other schemas, credentials or deployment secrets. Preserve the deployed code revision, runtime settings without secret values, and an independent secrets recovery method. Store an encrypted copy off the application host; a local copy on the same disk is not disaster recovery.

## Restore into isolation first

1. Keep live inference and external export disabled on the restore target. Stop old API/worker instances before any eventual traffic switch, so they cannot continue external effects against stale state.
2. Verify that `.incomplete` is absent. Parse `backup.json`; check the archive SHA-256 and every named asset's byte hash/size. Do not trust filenames alone. If a check fails, preserve the failed bundle and use another verified backup.
3. Create a new isolated PostgreSQL database with no application traffic. Restore the schema archive there using `pg_restore --exit-on-error --no-owner --no-acl`. An existing default schema may need to be removed or restored with `--clean --if-exists` **only in this newly created isolated database**. Do not aim a rehearsal at the live database.
4. Restore the assets into a separate media directory or bucket prefix. Keep their original digest identifiers. For S3, conditional writes and readback should establish that every referenced byte matches its digest before the target serves traffic.
5. Run the same application revision first. Set `COUNTERPART_DATABASE_URL` and storage settings to the restored targets, `COUNTERPART_LIVE_ENABLED=false` and `COUNTERPART_EXPORT_ENABLED=false`. Preserve operator authentication requirements. Reopen an existing session and release, inspect recorded evidence and download a complete pair. Then run a clearly labeled replay through collection, review and release.
6. Compare restored counts, pair/release digests and review history against the backup. A running job captured in the archive is historical work, not a proof that its old worker still exists. Let leases expire before starting replacement workers; do not remove fencing or make remote outcomes appear known.
7. Reconcile provider usage and export receipts independently before re-enabling external effects. A database rollback can roll back reservations even though cloud spend and uploads happened after the backup. **Never treat the restored reservation count as refunded credits.** Preserve/reconstruct the higher consumed allowance and reconcile ambiguous uploads with the provider. If that evidence is unavailable, leave cloud execution/export disabled.
8. Switch traffic only after this rehearsal passes, keep the previous deployment stopped, and preserve both pre-restore state and the verified backup until the new deployment has been checked.

There is no automatic destructive restore command. The restore target, secrets and external-state reconciliation require deliberate operator choices. The local test below validates the core archive/restore mechanism; it does not claim a production disaster-recovery drill or remote provider reconciliation.

## Upgrade and rollback

Current initialization applies additive `CREATE ... IF NOT EXISTS` / `ALTER ... ADD COLUMN IF NOT EXISTS` statements under a PostgreSQL advisory migration lock. It does not provide arbitrary schema downgrade support. Before an upgrade, create a verified backup, retain the old application revision and test the new revision against an isolated restored copy.

Recipe snapshots and released manifests are immutable historical contracts. A deployment with incompatible verifier rules should refuse that contract rather than rewrite it. Rolling back application code alone is safe only if it supports the already-applied schema and stored contracts; otherwise restore the paired database/media backup and reconcile external state as above. Never use volume deletion as a migration shortcut.

## Executed evidence

`COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest tests/test_lifecycle.py -q` exercises real PostgreSQL transactions in disposable randomly named schemas. The restore test constructs a complete deterministic spatial pair, approves it, persists a release, reserves a nonzero provider allowance, runs real `pg_dump`, destroys only that test schema, and runs real `pg_restore`. It compares the restored session, immutable release, review history, capability authentication, reservation ledger and original byte hashes.

Additional tests cover the duplicate-old-hash upload race, concurrent shared guards, rollback when preview deletion fails, bounded cursor progress beyond referenced files, every media-reference root, shared evidence across sessions, current session pointers, recent objects, invalid parameters, sanitized storage failures, private temporary connection credentials and refusal to back up into a source checkout. A real localhost HTTP S3 emulator verifies reference-aware deletion and the retained-version/delete-marker distinction. None of these tests uses a model or cloud account, and none measures real collection accuracy.
