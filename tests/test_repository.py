"""Real PostgreSQL fault and concurrency tests; never invoke model compute."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.repository import Conflict, Repository

pytestmark = pytest.mark.postgres


@pytest.fixture
def repository():
    dsn = os.environ.get("COUNTERPART_TEST_DSN")
    if not dsn:
        pytest.skip("Set COUNTERPART_TEST_DSN to an isolated PostgreSQL test database")
    schema = "cp_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    instance = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    instance.initialize()
    try:
        yield instance
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def session(repository, mode="replay"):
    return repository.create_session("spatial", mode, "bottle", "cup")["session"]


def submit(repository, saved, slot="baseline", key=None, seq=0):
    key = key or uuid4().hex
    frames = [
        {
            "asset_id": hashlib.sha256(key.encode()).hexdigest(),
            "t_ms": 0,
            "width": 640,
            "height": 480,
            "fixture_detections": [{"label": "bottle", "confidence": 0.99, "box": [0.1, 0.1, 0.2, 0.2]}],
        }
    ]
    metadata = {
        "slot": slot,
        "idempotency_key": key,
        "expected_revision": saved["revision"],
        "epoch": saved["epoch"],
        "client_seq": seq,
    }
    result = repository.submit(saved["id"], metadata, frames, key)
    return result, metadata, frames


def accepted(repository, saved, slot="baseline"):
    result, _, _ = submit(repository, saved, slot)
    job = repository.claim("worker")
    assert job["capture"]["id"] == result["capture"]["id"]
    assert repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    return repository.snapshot(saved["id"])


def verdict(accept=True):
    return {
        "accepted": accept,
        "code": "valid" if accept else "ambiguous",
        "message": "Fixture decision",
        "checks": [],
        "evidence": {},
        "verifier_version": "test-v1",
    }


def expire(repository, job_id):
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (job_id,),
        )


def test_capability_is_hashed_and_never_in_snapshot(repository):
    created = repository.create_session("spatial", "replay", "bottle", "cup")
    saved, token = created["session"], created["token"]
    assert repository.authenticate(saved["id"], token)
    assert not repository.authenticate(saved["id"], "bad")
    assert not repository.authenticate("bad", token)
    assert token not in str(repository.snapshot(saved["id"]))
    with psycopg.connect(repository.dsn) as connection:
        digest = connection.execute("SELECT capability_hash FROM counterpart_sessions").fetchone()[0]
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert digest not in str(saved)


def test_initialize_concurrently_is_safe(repository):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: repository.initialize(), range(8)))


def test_concurrent_retry_is_one_capture_and_mismatch_conflicts(repository):
    saved = session(repository)
    result, metadata, frames = submit(repository, saved, key="same")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: repository.submit(saved["id"], metadata, frames, "same"), range(16))
        )
    assert {r["capture"]["id"] for r in results} == {result["capture"]["id"]}
    assert repository.metrics()["jobs"] == {"pending": 1}
    with pytest.raises(Conflict, match="different capture"):
        repository.submit(saved["id"], metadata, frames, "different")
    # Retries return original capture even though its expected revision is now stale.
    assert repository.snapshot(saved["id"])["revision"] == 1


def test_concurrent_distinct_commits_cannot_both_win_revision(repository):
    saved = session(repository)

    def attempt(index):
        try:
            submit(repository, saved, key=f"attempt-{index}")
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(16))) == 1
    assert repository.metrics()["jobs"] == {"pending": 1}


def test_lease_reclaim_fences_old_heartbeat_and_result(repository):
    saved = session(repository)
    submit(repository, saved)
    first = repository.claim("first")
    expire(repository, first["id"])
    assert not repository.heartbeat(first["id"], first["lease_token"])
    second = repository.claim("second")
    assert second["id"] == first["id"] and second["attempt"] == 2
    assert second["lease_token"] != first["lease_token"]
    assert not repository.finish(first["id"], first["lease_token"], verdict(), first["capture"]["frames"])
    assert repository.finish(second["id"], second["lease_token"], verdict(), second["capture"]["frames"])
    assert not repository.finish(second["id"], second["lease_token"], verdict(), second["capture"]["frames"])
    assert repository.snapshot(saved["id"])["state"] == "baseline_ready"
    assert repository.metrics()["stale_results"] == 2


def test_crash_retries_are_bounded(repository):
    saved = session(repository)
    submit(repository, saved)
    for attempt in range(1, 4):
        job = repository.claim("crashing-worker")
        assert job["attempt"] == attempt
        expire(repository, job["id"])
    assert repository.claim("fourth") is None
    assert repository.snapshot(saved["id"])["latest_capture"]["status"] == "failed"
    assert repository.metrics()["jobs"] == {"failed": 1}


def test_explicit_fail_retries_bounded_and_error_sanitized(repository):
    saved = session(repository)
    submit(repository, saved)
    for attempt in range(1, 4):
        job = repository.claim("worker")
        assert job["attempt"] == attempt
        assert repository.fail(job["id"], job["lease_token"], "https://secret-token", retryable=True)
        with psycopg.connect(repository.dsn) as connection:
            connection.execute(
                "UPDATE counterpart_jobs SET available_at=clock_timestamp()-interval '1 second'"
            )
    assert repository.claim("worker") is None
    assert repository.snapshot(saved["id"])["latest_capture"]["error"] == "worker_error"


def test_resume_preserves_baseline_and_requires_recovery(repository):
    saved = accepted(repository, session(repository))
    original_id = saved["baseline"]["id"]
    resumed = repository.resume(saved["id"], saved["epoch"])
    assert resumed["recovery_required"] and resumed["baseline"]["id"] == original_id
    assert repository.resume(saved["id"], saved["epoch"])["epoch"] == resumed["epoch"]
    with pytest.raises(Conflict, match="current session"):
        repository.resume(saved["id"], resumed["epoch"] + 1)
    with pytest.raises(Conflict, match="Check the scene"):
        submit(repository, resumed, "counterpart")
    recovered = accepted(repository, resumed, "recovery")
    assert not recovered["recovery_required"]
    assert recovered["baseline"]["id"] == original_id
    assert accepted(repository, recovered, "counterpart")["state"] == "ready"


def test_resume_during_first_baseline_retains_result_but_requires_regrounding(repository):
    saved = session(repository)
    submit(repository, saved)
    job = repository.claim("worker")
    repository.resume(saved["id"], saved["epoch"])
    assert repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    resumed = repository.snapshot(saved["id"])
    assert resumed["baseline"] and resumed["recovery_required"]
    with pytest.raises(Conflict, match="Check the scene"):
        submit(repository, resumed, "counterpart")


def test_old_recovery_cannot_approve_new_epoch(repository):
    saved = accepted(repository, session(repository))
    saved = repository.resume(saved["id"], saved["epoch"])
    submit(repository, saved, "recovery")
    job = repository.claim("worker")
    repository.resume(saved["id"], saved["epoch"])
    assert not repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    assert repository.snapshot(saved["id"])["recovery_required"]


def test_preview_coalesces_and_active_result_is_not_starved_by_newer_pending(repository):
    saved = session(repository)
    first, _, _ = submit(repository, saved, "preview", seq=1)
    job = repository.claim("worker")
    submit(repository, saved, "preview", seq=2)
    last, _, _ = submit(repository, saved, "preview", seq=3)
    assert repository.claim("other-worker") is None
    assert repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    observed = repository.snapshot(saved["id"])
    assert observed["feedback"]["id"] == first["capture"]["id"]
    assert observed["baseline"] is None and observed["state"] == "collecting"
    second = repository.claim("worker")
    assert second["capture"]["id"] == last["capture"]["id"]
    assert repository.metrics()["jobs"] == {"running": 1, "succeeded": 1, "superseded": 1}


def test_obsolete_preview_pruned_before_spending_and_stale_result_ignored(repository):
    saved = session(repository)
    submit(repository, saved, "preview", seq=1)
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_captures SET created_at=clock_timestamp()-interval '20 seconds'"
        )
    assert repository.claim("worker") is None
    assert repository.metrics()["jobs"] == {"superseded": 1}
    submit(repository, saved, "preview", seq=2)
    job = repository.claim("worker")
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_captures SET created_at=clock_timestamp()-interval '20 seconds'"
        )
    assert not repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    assert repository.snapshot(saved["id"])["feedback"] is None


def test_preview_fairness_noisy_collector_does_not_starve_other(repository):
    noisy, quiet = session(repository), session(repository)
    submit(repository, noisy, "preview", seq=1)
    job = repository.claim("worker")
    repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    for seq in range(2, 20):
        submit(repository, noisy, "preview", seq=seq)
    submit(repository, quiet, "preview", seq=1)
    next_job = repository.claim("worker")
    assert next_job["session"]["id"] == quiet["id"]


def test_committed_capture_priority_and_preview_revision_fence(repository):
    preview_session, committed_session = session(repository), session(repository)
    submit(repository, preview_session, "preview", seq=1)
    submit(repository, committed_session)
    assert repository.claim("worker")["session"]["id"] == committed_session["id"]
    preview_job = repository.claim("worker")
    submit(repository, preview_session)
    assert not repository.finish(
        preview_job["id"], preview_job["lease_token"], verdict(), preview_job["capture"]["frames"]
    )


def test_replacement_fences_old_job_and_never_publishes_old_counterpart(repository):
    saved = accepted(repository, session(repository))
    original_baseline = saved["baseline"]["id"]
    saved = accepted(repository, saved, "counterpart")
    replacement, _, _ = submit(repository, saved, "counterpart")
    assert replacement["session"]["counterpart"] is None
    assert replacement["session"]["baseline"]["id"] == original_baseline
    with pytest.raises(Conflict):
        repository.publish(saved["id"], replacement["session"]["revision"])
    job = repository.claim("worker")
    repository.finish(job["id"], job["lease_token"], verdict(False), job["capture"]["frames"])
    with pytest.raises(Conflict):
        repository.publish(saved["id"], replacement["session"]["revision"])
    current = repository.snapshot(saved["id"])
    first, _, _ = submit(repository, current, "baseline")
    old_job = repository.claim("old-worker")
    new, _, _ = submit(repository, first["session"], "baseline")
    assert not repository.finish(
        old_job["id"], old_job["lease_token"], verdict(), old_job["capture"]["frames"]
    )
    assert new["session"]["baseline"] is None and new["session"]["state"] == "collecting"


def test_publish_atomic_idempotent_immutable_and_fixture_export_disabled(repository):
    saved = accepted(repository, session(repository))
    saved = accepted(repository, saved, "counterpart")
    with ThreadPoolExecutor(max_workers=4) as pool:
        publications = list(pool.map(lambda _: repository.publish(saved["id"], saved["revision"]), range(8)))
    assert len({pair["id"] for pair in publications}) == 1
    manifest = publications[0]["manifest"]
    assert manifest["split_group"] == saved["id"]
    assert manifest["baseline"]["frames"] and manifest["counterpart"]["frames"]
    assert manifest["provenance"]["evidence_kind"] == "fixture_replay"
    assert publications[0]["export_status"] == "disabled_replay"
    with pytest.raises(Conflict, match="already published"):
        submit(repository, repository.snapshot(saved["id"]), "baseline")
    assert repository.publish(saved["id"], 0)["manifest"] == manifest
    assert repository.metrics()["published_pairs"] == 1
    with psycopg.connect(repository.dsn) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM counterpart_export_intents WHERE status='disabled'"
            ).fetchone()[0]
            == 1
        )


def test_worker_cannot_substitute_evidence_identity(repository):
    saved = session(repository)
    submit(repository, saved)
    job = repository.claim("worker")
    different = [{**job["capture"]["frames"][0], "asset_id": "different"}]
    with pytest.raises(Conflict, match="another capture"):
        repository.finish(job["id"], job["lease_token"], verdict(), different)
    assert repository.snapshot(saved["id"])["state"] == "collecting"
    assert repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])


def test_budget_concurrency_cannot_overspend_and_duplicate_units_match(repository):
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda index: repository.reserve_budget("roboflow", f"request-{index}", 3, 10), range(32)
            )
        )
    assert sum(results) == 3
    assert repository.metrics()["budget_reservations"] == [{"provider": "roboflow", "reserved_units": 9}]
    assert repository.reserve_budget("other", "same", 4, 4)
    assert repository.reserve_budget("other", "same", 4, 4)
    with pytest.raises(Conflict):
        repository.reserve_budget("other", "same", 5, 100)
    assert not repository.reserve_budget("other", "new", 1, 4)


def test_failed_capture_insert_transaction_rolls_back_without_job(repository):
    saved = session(repository)
    metadata = {"slot": "baseline", "expected_revision": 0, "epoch": 0, "idempotency_key": "bad"}
    with pytest.raises(TypeError):
        repository.submit(saved["id"], metadata, [{"bad": object()}], "bad")
    assert repository.snapshot(saved["id"])["revision"] == 0
    assert repository.metrics()["jobs"] == {}


def test_export_fenced_progress_complete_gate_and_immutable_receipts(repository):
    saved = accepted(repository, session(repository, mode="live"))
    saved = accepted(repository, saved, "counterpart")
    pair = repository.publish(saved["id"], saved["revision"])
    initial_manifest = pair["manifest"]
    job = repository.export_claim("exporter")
    assert job["pair_id"] == pair["id"]
    assert repository.export_claim("other") is None
    with pytest.raises(Conflict, match="Every pair asset"):
        repository.export_finish(pair["id"], job["lease_token"], "complete")
    first_asset = pair["manifest"]["baseline"]["frames"][0]["asset_id"]
    assert repository.export_record_asset(pair["id"], job["lease_token"], first_asset, {"state": "uploading"})
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_export_intents SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
    assert not repository.export_heartbeat(pair["id"], job["lease_token"])
    reclaimed = repository.export_claim("second")
    assert reclaimed["receipts"][first_asset] == {"state": "uploading"}
    assert not repository.export_record_asset(
        pair["id"], job["lease_token"], first_asset, {"state": "uploaded"}
    )
    assert not repository.export_finish(pair["id"], job["lease_token"], "complete")
    assets = repository._manifest_assets(pair["manifest"])
    for asset in assets:
        if asset != first_asset:
            assert repository.export_record_asset(
                pair["id"], reclaimed["lease_token"], asset, {"state": "uploading"}
            )
        assert repository.export_record_asset(
            pair["id"], reclaimed["lease_token"], asset, {"state": "uploaded", "image_id": asset}
        )
        assert repository.export_record_asset(
            pair["id"], reclaimed["lease_token"], asset, {"state": "annotating", "image_id": asset}
        )
        receipt = {"state": "confirmed", "image_id": asset}
        assert repository.export_record_asset(pair["id"], reclaimed["lease_token"], asset, receipt)
        assert repository.export_record_asset(pair["id"], reclaimed["lease_token"], asset, receipt)
        with pytest.raises(Conflict, match="cannot be replaced"):
            repository.export_record_asset(
                pair["id"], reclaimed["lease_token"], asset, {"state": "uploading"}
            )
    assert repository.export_finish(pair["id"], reclaimed["lease_token"], "complete")
    snapshot = repository.snapshot(saved["id"])
    assert snapshot["pair"]["export_status"] == "complete"
    assert snapshot["pair"]["manifest"] == initial_manifest
    assert repository.export_claim("worker") is None


def test_export_uncertainty_is_not_blindly_retried_and_replay_is_never_exported(repository):
    replay = accepted(repository, session(repository))
    replay = accepted(repository, replay, "counterpart")
    repository.publish(replay["id"], replay["revision"])
    assert repository.export_claim("worker") is None
    live = accepted(repository, session(repository, mode="live"))
    live = accepted(repository, live, "counterpart")
    repository.publish(live["id"], live["revision"])
    job = repository.export_claim("worker")
    assert repository.export_finish(job["pair_id"], job["lease_token"], "pending")
    job = repository.export_claim("worker")
    assert job["attempt"] == 2
    assert repository.export_finish(job["pair_id"], job["lease_token"], "uncertain", "unknown_remote_result")
    assert repository.export_claim("worker") is None
    assert repository.snapshot(live["id"])["pair"]["export_status"] == "uncertain"


def test_concurrent_worker_claims_obey_global_lease_bound(repository):
    from counterpart.repository import MAX_RUNNING_JOBS

    for _ in range(MAX_RUNNING_JOBS + 3):
        submit(repository, session(repository))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda index: repository.claim(f"worker-{index}"), range(16)))
    claimed = [job for job in claimed if job is not None]
    assert len(claimed) == MAX_RUNNING_JOBS
    assert len({job["id"] for job in claimed}) == MAX_RUNNING_JOBS
    assert repository.metrics()["jobs"]["running"] == MAX_RUNNING_JOBS


def test_abrupt_worker_process_exit_recovers_saved_job(repository):
    import json
    import subprocess
    import sys

    saved = session(repository)
    result, _, _ = submit(repository, saved)
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,os,sys; from counterpart.repository import Repository; "
            "job=Repository(sys.argv[1]).claim('doomed'); "
            "print(json.dumps(job),flush=True); os._exit(17)",
            repository.dsn,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 17
    original = json.loads(child.stdout)
    assert original["capture"]["id"] == result["capture"]["id"]
    assert repository.claim("replacement") is None  # The live lease still protects its grace period.
    expire(repository, original["id"])
    recovered = repository.claim("replacement")
    assert recovered["capture"]["id"] == original["capture"]["id"]
    assert recovered["attempt"] == 2
    assert repository.finish(
        recovered["id"], recovered["lease_token"], verdict(), recovered["capture"]["frames"]
    )
    assert repository.snapshot(saved["id"])["state"] == "baseline_ready"


def test_generated_fault_sequences_preserve_publication_invariants(repository):
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    @given(
        st.lists(
            st.sampled_from(
                [
                    "baseline",
                    "counterpart",
                    "preview",
                    "recovery",
                    "resume",
                    "claim",
                    "accept",
                    "reject",
                    "expire",
                    "publish",
                ]
            ),
            min_size=15,
            max_size=35,
        )
    )
    @settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def exercise(actions):
        with psycopg.connect(repository.dsn) as connection:
            connection.execute("TRUNCATE counterpart_sessions CASCADE")
        saved = session(repository)
        jobs = []
        previous_revision = 0
        published_manifest = None
        for index, action in enumerate(actions):
            saved = repository.snapshot(saved["id"])
            try:
                if action in {"baseline", "counterpart", "preview", "recovery"}:
                    submit(repository, saved, action, seq=index + 1)
                elif action == "resume":
                    repository.resume(saved["id"], saved["epoch"])
                elif action == "claim":
                    job = repository.claim("property-worker")
                    if job:
                        jobs.append(job)
                elif action in {"accept", "reject"} and jobs:
                    job = jobs[index % len(jobs)]
                    repository.finish(
                        job["id"], job["lease_token"], verdict(action == "accept"), job["capture"]["frames"]
                    )
                elif action == "expire" and jobs:
                    expire(repository, jobs[index % len(jobs)]["id"])
                elif action == "publish":
                    repository.publish(saved["id"], saved["revision"])
            except Conflict:
                pass  # Rejected stale/invalid commands are expected in generated histories.
            current = repository.snapshot(saved["id"])
            assert current["revision"] >= previous_revision
            previous_revision = current["revision"]
            if current["state"] == "ready":
                assert current["baseline"]["status"] == current["counterpart"]["status"] == "accepted"
            if current["pair"]:
                manifest = current["pair"]["manifest"]
                assert manifest["baseline"]["status"] == manifest["counterpart"]["status"] == "accepted"
                assert manifest["counterpart"]["revision"] == manifest["revision"]
                assert manifest["split_group"] == saved["id"]
                if published_manifest is not None:
                    assert manifest == published_manifest
                published_manifest = manifest
            assert repository.metrics()["published_pairs"] <= 1

    exercise()


def test_export_reconciliation_delay_persists_without_reupload_or_worker_sleep(repository):
    saved = accepted(repository, session(repository, mode="live"))
    saved = accepted(repository, saved, "counterpart")
    pair = repository.publish(saved["id"], saved["revision"])
    job = repository.export_claim("first")
    asset = pair["manifest"]["baseline"]["frames"][0]["asset_id"]
    receipt = {"state": "uploading", "reconciliation_attempts": 1}
    assert repository.export_record_asset(pair["id"], job["lease_token"], asset, receipt)
    assert repository.export_finish(pair["id"], job["lease_token"], "pending", delay_seconds=120)
    # A restarted repository sees the same schedule; no in-memory sleep is needed.
    restarted = Repository(repository.dsn)
    restarted.initialize()
    assert restarted.export_claim("too-soon") is None
    with psycopg.connect(repository.dsn) as connection:
        delta = connection.execute(
            "SELECT extract(epoch FROM available_at-clock_timestamp()) FROM counterpart_export_intents"
        ).fetchone()[0]
        assert 100 < delta <= 120
        connection.execute(
            "UPDATE counterpart_export_intents SET available_at=clock_timestamp()-interval '1 second'"
        )
    resumed = restarted.export_claim("later")
    assert resumed["receipts"][asset] == receipt
    assert resumed["attempt"] == 2
    assert resumed["lease_token"] != job["lease_token"]
    assert not repository.export_finish(pair["id"], job["lease_token"], "pending", delay_seconds=300)


def test_export_expired_running_recovery_ignores_delayed_pending_schedule(repository):
    saved = accepted(repository, session(repository, mode="live"))
    saved = accepted(repository, saved, "counterpart")
    pair = repository.publish(saved["id"], saved["revision"])
    first = repository.export_claim("crashed")
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_export_intents SET available_at=clock_timestamp()+interval '300 seconds', "
            "lease_expires_at=clock_timestamp()-interval '1 second'"
        )
    recovered = repository.export_claim("recovered")
    assert recovered["pair_id"] == pair["id"]
    assert recovered["lease_token"] != first["lease_token"]


@pytest.mark.parametrize("delay", [-1, 301, float("nan"), float("inf"), True, "10"])
def test_export_delay_rejects_invalid_values_without_mutating_state(repository, delay):
    saved = accepted(repository, session(repository, mode="live"))
    saved = accepted(repository, saved, "counterpart")
    pair = repository.publish(saved["id"], saved["revision"])
    job = repository.export_claim("worker")
    with pytest.raises(ValueError, match="Export delay"):
        repository.export_finish(pair["id"], job["lease_token"], "pending", delay_seconds=delay)
    assert repository.export_heartbeat(pair["id"], job["lease_token"])
    assert repository.snapshot(saved["id"])["pair"]["export_status"] == "running"
