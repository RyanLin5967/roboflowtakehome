"""Metric arithmetic and equal-bound comparator contracts, not timing-win tests."""

import importlib.util
import inspect
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.repository import MAX_PENDING_JOBS, CapacityExceeded, Repository

SPEC = importlib.util.spec_from_file_location(
    "feedback_benchmark", Path(__file__).parents[1] / "scripts/feedback_benchmark.py"
)
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)
FOLLOWUP_SPEC = importlib.util.spec_from_file_location(
    "feedback_followup", Path(__file__).parents[1] / "scripts/feedback_followup.py"
)
followup = importlib.util.module_from_spec(FOLLOWUP_SPEC)
FOLLOWUP_SPEC.loader.exec_module(followup)


def test_percentile_interpolates_and_never_invents_an_empty_sample():
    assert benchmark.percentile([], 0.95) is None
    assert benchmark.percentile([4], 0.95) == 4
    assert benchmark.percentile([10, 0], 0.95) == 9.5
    assert benchmark.distribution([0, 10])["mean"] == 5


def test_age_integrates_visible_staleness_including_gaps():
    # Visible at t=1 from capture t=.5, then visible at t=3 from t=2.5.
    result = benchmark.feedback_coverage([(1, 0.5), (3, 2.5)], 0, 4)
    assert result["no_feedback_ms"] == 1000
    assert result["no_feedback_fraction"] == 0.25
    assert result["mean_age_after_first_feedback_ms"] == pytest.approx(4000 / 3)
    assert result["max_feedback_gap_ms"] == 2000


def test_starved_clients_remain_in_denominator_and_drain_is_not_offered_horizon():
    result = benchmark.feedback_coverage([(5, 1)], 0, 4)
    assert result["no_feedback_ms"] == 4000
    assert result["no_feedback_fraction"] == 1
    assert result["mean_age_after_first_feedback_ms"] is None
    assert result["updates_in_horizon"] == 0
    assert benchmark.jain_fairness([4, 0, 0, 0]) == 0.25
    assert benchmark.jain_fairness([0, 0]) == 0
    assert benchmark.jain_fairness([2, 2]) == 1


def test_invalid_feedback_chronology_is_rejected():
    with pytest.raises(ValueError):
        benchmark.feedback_coverage([(1, 2)], 0, 4)
    with pytest.raises(ValueError):
        benchmark.feedback_coverage([], 4, 0)


def test_schedule_is_identical_across_policies_and_preserves_saved_work():
    case = benchmark.Case("test", 60, noisy_weight=6)
    assert benchmark.offered_schedule(case, 42) == benchmark.offered_schedule(case, 42)
    assert benchmark.offered_schedule(case, 42) != benchmark.offered_schedule(case, 43)
    offers = benchmark.offered_schedule(case, 42)
    assert [offer.number for offer in offers] == list(range(len(offers)))
    assert all(0 <= offer.at < case.duration for offer in offers)
    assert sum(offer.slot == "baseline" for offer in offers) == 2
    assert all(80 <= offer.delay_ms <= 120 for offer in offers)
    assert sum(offer.client == 0 for offer in offers) > 5 * sum(offer.client == 1 for offer in offers)


def test_comparator_only_replaces_session_order_and_refuses_source_drift():
    original = "SELECT s.id FROM counterpart_sessions s WHERE EXISTS (anything) ORDER BY priority, "
    transformed = benchmark.fifo_query(original + benchmark.FIFO_ORIGINAL)
    assert transformed == original + benchmark.FIFO_REPLACEMENT
    assert "ORDER BY priority" in transformed
    assert benchmark.fifo_query("SELECT * FROM anything") == "SELECT * FROM anything"
    with pytest.raises(RuntimeError, match="changed"):
        benchmark.fifo_query(original + "unknown changed ordering")
    assert benchmark.FIFO_ORIGINAL in inspect.getsource(Repository.claim)
    assert "claim" not in benchmark.ComparatorRepository.__dict__
    assert "finish" not in benchmark.ComparatorRepository.__dict__


@pytest.fixture
def repositories():
    dsn = os.environ.get("COUNTERPART_TEST_DSN")
    if not dsn:
        pytest.skip("Set COUNTERPART_TEST_DSN for actual PostgreSQL comparator contracts")
    schema = "feedback_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(dsn, options=f"-c search_path={schema}")
    repo = Repository(scoped)
    repo.initialize()
    try:
        yield (
            repo,
            benchmark.ComparatorRepository(scoped, keep_first=False),
            benchmark.ComparatorRepository(scoped, keep_first=True),
        )
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def submit(repo, session, seq, slot="preview"):
    key = f"feedback-test-{seq}-{slot}"
    return repo.submit(
        session["id"],
        {
            "slot": slot,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "client_seq": seq,
            "idempotency_key": key,
        },
        [benchmark.frame(seq)],
        key,
    )


@pytest.mark.postgres
def test_both_fifo_controls_keep_production_pending_bound(repositories):
    production, latest, sampled = repositories
    session = production.create_session("spatial", "replay", "bottle", "cup")["session"]
    first = submit(sampled, session, 1)
    with pytest.raises(benchmark.SampledOut):
        submit(sampled, session, 2)
    assert sampled.claim("first")["capture"]["id"] == first["capture"]["id"]
    # Running work is retained and one new pending preview is allowed.
    submit(sampled, session, 3)
    with pytest.raises(benchmark.SampledOut):
        submit(sampled, session, 4)
    newest = submit(latest, session, 5)
    with production._connection() as connection:
        count = connection.execute(
            "SELECT count(*) AS n FROM counterpart_jobs WHERE status='pending'"
        ).fetchone()["n"]
    assert count == 1
    assert newest["capture"]["client_seq"] == 5
    assert sampled.claim("second") is None  # Same in-flight-per-session fence.


@pytest.mark.postgres
@pytest.mark.parametrize("policy_index", [0, 1, 2])
def test_each_policy_preserves_committed_priority(repositories, policy_index):
    repo = repositories[policy_index]
    sessions = [repo.create_session("spatial", "replay", "bottle", "cup")["session"] for _ in range(2)]
    submit(repo, sessions[0], 1)
    submit(repo, sessions[1], 2, "baseline")
    assert repo.claim("worker")["capture"]["slot"] == "baseline"


@pytest.mark.postgres
def test_fifo_comparator_is_actually_fifo_when_fair_scheduler_would_choose_other_session(repositories):
    production, latest, _ = repositories
    first, second = [
        production.create_session("spatial", "replay", "bottle", "cup")["session"] for _ in range(2)
    ]
    submit(production, first, 1)
    submit(production, second, 1)
    with production._connection() as connection:
        connection.execute(
            "UPDATE counterpart_sessions SET preview_served_at=clock_timestamp() WHERE id=%s", (first["id"],)
        )
    assert latest.claim("fifo-worker")["session"]["id"] == first["id"]
    assert production.claim("fair-worker")["session"]["id"] == second["id"]


@pytest.mark.postgres
def test_comparators_share_real_global_admission_limit(repositories):
    production, latest, sampled = repositories
    # Narrow the constant only within this contract test to avoid 128 redundant
    # DB setup sessions. All implementations call the exact production gate.
    import counterpart.repository as module

    original = module.MAX_PENDING_JOBS
    assert original == MAX_PENDING_JOBS
    module.MAX_PENDING_JOBS = 2
    try:
        sessions = [
            production.create_session("spatial", "replay", "bottle", "cup")["session"] for _ in range(3)
        ]
        submit(production, sessions[0], 1)
        submit(latest, sessions[1], 1)
        for repo in repositories:
            with pytest.raises(CapacityExceeded):
                submit(repo, sessions[2], 1)
        with pytest.raises(benchmark.SampledOut):
            submit(sampled, sessions[0], 2)
        submit(latest, sessions[0], 2)  # Replacement still admitted at the bound.
    finally:
        module.MAX_PENDING_JOBS = original


@pytest.mark.postgres
@pytest.mark.parametrize("policy", benchmark.POLICIES)
def test_complete_benchmark_observes_saved_work_and_accounts_for_every_offer(policy):
    dsn = os.environ.get("COUNTERPART_TEST_DSN")
    if not dsn:
        pytest.skip("Set COUNTERPART_TEST_DSN for actual PostgreSQL benchmark execution")
    result = benchmark.execute_trial(
        dsn, benchmark.Case("contract", 20, duration=0.6, service_ms=20, poll_ms=20), policy, 42
    )
    assert result["offered_count"] == sum(result["outcomes"].values())
    assert result["committed"]["persisted"] == result["committed"]["client_observed"] == 2
    assert result["queue_drained"]
    assert result["service_delay_ms"]["n"] == result["service_calls"]
    assert result["capture_to_client_observed_feedback_ms"]["p50"] > 0
    assert result["outcomes"]["driver_dropped"] == 0
    assert len(result["raw_offer_events"]) == result["offered_count"]
    assert all(client["client_visible_updates"] > 0 for client in result["per_client"])


@pytest.mark.postgres
def test_stable_fifo_preserves_queue_position_but_refreshes_capture_and_resets_after_claim(repositories):
    production, _, _ = repositories
    repo = followup.StableFIFORepository(production.dsn, keep_first=False)
    first, second = [repo.create_session("spatial", "replay", "bottle", "cup")["session"] for _ in range(2)]
    initial = submit(repo, first, 1)
    with repo._connection() as connection:
        original_queue_time = connection.execute(
            "SELECT created_at FROM counterpart_jobs WHERE capture_id=%s", (initial["capture"]["id"],)
        ).fetchone()["created_at"]
    submit(repo, second, 1)
    replacement = submit(repo, first, 2)
    with repo._connection() as connection:
        queue_time = connection.execute(
            "SELECT created_at FROM counterpart_jobs WHERE capture_id=%s", (replacement["capture"]["id"],)
        ).fetchone()["created_at"]
    assert queue_time == original_queue_time
    assert replacement["capture"]["created_at"] > initial["capture"]["created_at"]
    claim = repo.claim("stable-worker")
    assert claim["session"]["id"] == first["id"]
    assert claim["capture"]["client_seq"] == 2
    # A running preview ends its pending queue slot. New pending work must not
    # inherit that old priority forever and jump ahead of the second collector.
    following = submit(repo, first, 3)
    with repo._connection() as connection:
        new_queue_time = connection.execute(
            "SELECT created_at FROM counterpart_jobs WHERE capture_id=%s", (following["capture"]["id"],)
        ).fetchone()["created_at"]
    assert new_queue_time > original_queue_time
    assert repo.finish(claim["id"], claim["lease_token"], {"accepted": True}, claim["capture"]["frames"])
    assert repo.claim("stable-worker")["session"]["id"] == second["id"]


def test_stable_comparator_substitution_is_restored_after_failure():
    original = benchmark.ComparatorRepository
    with pytest.raises(RuntimeError), followup.stable_comparator():
        assert benchmark.ComparatorRepository is followup.StableFIFORepository
        raise RuntimeError("trial failed")
    assert benchmark.ComparatorRepository is original


@pytest.mark.postgres
def test_stable_fifo_keeps_pending_bound_and_saved_capture_priority(repositories):
    production, _, _ = repositories
    repo = followup.StableFIFORepository(production.dsn, keep_first=False)
    first, second = [repo.create_session("spatial", "replay", "bottle", "cup")["session"] for _ in range(2)]
    submit(repo, first, 1)
    submit(repo, first, 2)
    submit(repo, second, 3, "baseline")
    with repo._connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM counterpart_jobs WHERE status='pending'"
            ).fetchone()["n"]
            == 2
        )
    assert repo.claim("saved-worker")["capture"]["slot"] == "baseline"
