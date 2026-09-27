# Historical migration and recovery evidence

Executed during the 2026-09-26 original-scope gap closure. This is an independent
integration test of persisted old evidence, migration failure, and recovery. It
does not establish model quality, physical-camera accuracy, or provider behavior.

`tests/test_gap_migrations.py`: **7 passed in 7.41 seconds**, using Python 3.12.13
and actual PostgreSQL 17.11. Ruff passed. Run:

```sh
COUNTERPART_TEST_DSN=postgresql:///counterpart_test uv run pytest -q tests/test_gap_migrations.py
```

The test suite needs the two historical commits below in local Git history.
CI must fetch history rather than checking out a single shallow commit. Missing
history fails the test; it is not silently replaced with a hand-written schema.
Only uniquely named `cp_gap_migration_*` schemas are created and dropped in the
test database. The running application database and provider credentials are not
read or changed. No ML, hosted inference, or dataset upload executes.

## Historical data sources

The tests use `git archive` to copy actual historical Python and SQL into private
temporary directories, then execute that historical package in a subprocess.
The current implementation cannot inadvertently manufacture the old fixture.

| Historical implementation | Commit | Populated evidence |
|---|---|---|
| Initial capture kernel | `a51fa31ebe6f840bdf5bb9042d26328d9bdef699` | Published generated pair, media, capability, job history, queued live capture, budget ledger, synthetic uncertain export receipt |
| Complete pre-gap-closure product | `eb58c78934aa02ca89ed32bc065ab4e6f73a7e84` | The above plus campaign, invitation, lost-response join receipt, human review event and immutable dataset release |

The historical verifier evaluates generated deterministic image observations to
create the completed fixture pair. No detection output is presented as a real
model measurement. The uncertain export receipt is explicitly synthetic and is
used only to check JSON preservation.

## Verified invariants

1. **Upgrade preserves evidence.** The current initializer runs twice. Every
   preexisting column and row in every historical table remains exactly equal
   to its pre-upgrade value. Added nullable fields remain null for old sessions;
   neither a model pin, scene group nor baseline authorization is retroactively
   invented. Old captures retain no authorization and false unresolved-recovery
   flags; their canonical evidence records do not acquire new claimed history.
2. **Historical releases remain usable.** The prior complete-product release is
   fetched without changes, packed into a ZIP with its original bytes, and read
   by the current independent portable consumer: one pair, two assets, two
   examples. This tests an actual old release, not a reconstructed new release.
3. **Capabilities and admission survive safely.** Old capabilities authenticate.
   An idempotent invitation join recovers its original capability. A new join
   into the legacy campaign refuses collection until its contract is addressed.
4. **Queued work fails before new compute.** Retrying the old submission returns
   its original capture ID. A current worker refuses the missing execution
   contract before provider inference or cache initialization. The initial
   kernel can instead refuse its missing frozen recipe; both fail closed. The
   ledger retains exactly 17 preexisting units and one reservation. Historical
   export receipts remain unchanged.
5. **Failed migration is atomic.** A deliberate failure in the later feature
   schema rolls back preceding column additions, tables and triggers. The full
   old column inventory and all rows remain unchanged, and a subsequent normal
   initialization succeeds.
6. **Actual restore includes the new features.** After migration, real feature
   methods create a scene group and grouped session, explicitly adopt a model
   contract for untouched queued work, append an old-evidence verifier revision
   and attempt review, and human-adjudicate a generated ambiguous pair. A real
   v2 verifier separately rejects a generated geometry-valid baseline because
   its background is textureless. The actual operator authorization method
   permits collecting its counterpart without changing that original rejection.
   Reconnecting before the counterpart preserves an explicit unresolved-recovery
   flag and copies immutable authority to its queued capture. The backup fixture
   also populates all four annotation/delivery tables with
   clearly synthetic storage-only records, including an uncertain external
   effect and its event. Every new table is nonempty before backup.
7. **Restore preserves bytes and guards.** `pg_dump` creates the backup; only the
   test's random schema is dropped; actual `pg_restore` restores it. Every table,
   column and row equals the pre-backup snapshot. Initializing twice again makes
   no changes. Original assets round-trip byte-for-byte, capabilities and the old
   release work, and both execution-contract and scene-group mutation triggers
   still refuse changes. The restored baseline authority refuses updates and
   deletion; session and capture copies refuse divergent content. Its queued
   counterpart survives an idempotent retry and actual offline worker execution
   with the same authority and unresolved-recovery flag. Both observations
   remain refused by machine verification, human review stays required, ordinary
   publication is blocked, and the prior 17-unit budget remains untouched.

## Boundaries

The annotation/delivery portion proves schema and persisted-effect recovery,
not Roboflow's remote API semantics; that feature has separate transport and
state-machine tests. The generated adjudication exercises the real software
path but is not evidence of an actual human camera trial. The authorization
fixture explicitly freezes v2 on an internal replay session to exercise the real
classical image checks without hosted perception; production replay still uses
v1 and this test does not claim a production v2 replay feature. The oldest kernel
predates immutable collection releases, so only its existing pair is preserved;
no historical release is fabricated for it.

This test restores a recent internally consistent snapshot. It does not make
restoring an old spending ledger safe for renewed cloud execution: provider
usage must still be independently reconciled before live workloads resume.
