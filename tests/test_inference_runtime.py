"""PostgreSQL dispatch/cache fault tests with no real model or network calls."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.config import Settings
from counterpart.inference_runtime import InferenceRuntime
from counterpart.providers import ProviderError
from counterpart.repository import Repository
from counterpart.storage import InvalidImage, MediaStore

pytestmark = pytest.mark.postgres
IDENTITY = {"provider": "test", "model_id": "no-model-executed/1", "normalizer": "test-v1"}
RESULT = {"detections": [], "model": IDENTITY, "latency_ms": 123.5}


@pytest.fixture
def environment(tmp_path):
    dsn = os.environ.get("COUNTERPART_TEST_DSN")
    if not dsn:
        pytest.skip("Set COUNTERPART_TEST_DSN to an isolated PostgreSQL test database")
    schema = "cp_inference_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repo = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    repo.initialize()
    media = MediaStore(tmp_path / "media")
    config = Settings(database_url=repo.dsn, media_dir=media.root, inference_limit=100)
    try:
        yield repo, media, config
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def frames(media, count=1, start=0):
    result = []
    for index in range(start, start + count):
        image = io.BytesIO()
        Image.new("RGB", (80, 80), (index % 256, index // 256, 0)).save(image, "PNG")
        result.append({**media.put(image.getvalue()), "t_ms": index * 100})
    return result


class Detector:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback
        self.lock = threading.Lock()

    def detect(self, image, width, height):
        with self.lock:
            self.calls.append(image)
        if self.callback:
            return self.callback(image, width, height)
        return RESULT


def runtime(environment, detector=None, **options):
    repo, media, config = environment
    options = {
        "lease_seconds": 2,
        "heartbeat_seconds": 0.1,
        "poll_seconds": 0.01,
        "wait_seconds": 5,
        "detector_configuration": IDENTITY,
        **options,
    }
    return InferenceRuntime(repo, media, config, detector or Detector(), **options)


def observe(instance, source, session="session-a", job=None, heartbeat=None):
    return instance.observe_frames(
        source,
        session_id=session,
        job_key=job or uuid4().hex,
        heartbeat=heartbeat,
    )


def spent(repo):
    return repo.metrics()["budget_reservations"][0]["reserved_units"]


def expire(repo):
    with repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_inference_cache SET lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE status='running'"
        )
        connection.execute(
            "UPDATE counterpart_inference_attempts SET "
            "lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE status='running'"
        )


def test_repeated_evidence_is_one_dispatch_preserving_timestamps_and_provenance(environment):
    repo, media, _ = environment
    detector = Detector()
    instance = runtime(environment, detector)
    original = frames(media)[0]
    first = observe(instance, [original])[0]
    second = observe(instance, [{**original, "t_ms": 800}], session="other")[0]
    assert len(detector.calls) == 1
    assert spent(repo) == 1
    assert first["t_ms"] == 0 and second["t_ms"] == 800
    assert first["inference"]["source"] == "provider"
    assert second["inference"]["source"] == "cache"
    assert first["inference"]["observation_id"] == second["inference"]["observation_id"]
    assert first["inference"]["observed_at"] == second["inference"]["observed_at"]
    assert second["inference"]["provider_latency_ms"] == 123.5
    assert "latency_ms" not in second
    assert instance.cache_stats() == {
        "cached_assets": 1,
        "cache_reuses": 1,
        "waiting_frames": 0,
        "dispatch_attempts": {"succeeded": 1},
    }


def test_two_workers_same_asset_join_one_paid_attempt(environment):
    _, media, _ = environment
    entered, release = threading.Event(), threading.Event()

    def slow(*_):
        entered.set()
        assert release.wait(5)
        return RESULT

    detector = Detector(slow)
    first, second = runtime(environment, detector), runtime(environment, detector)
    source = frames(media)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(observe, first, source, "first")
        assert entered.wait(3)
        two = pool.submit(observe, second, source, "second")
        time.sleep(0.1)
        release.set()
        a, b = one.result(timeout=5), two.result(timeout=5)
    assert len(detector.calls) == 1
    assert a[0]["inference"]["observation_id"] == b[0]["inference"]["observation_id"]
    assert b[0]["inference"]["source"] == "cache"


def test_partial_failure_keeps_completed_frames_and_retry_only_dispatches_missing(environment):
    repo, media, _ = environment
    source = frames(media, 4)
    bad_image = media.read(source[2]["asset_id"])
    failed = False

    def sometimes(image, *_):
        nonlocal failed
        if image == bad_image and not failed:
            failed = True
            raise ProviderError("provider_unavailable", retryable=True, ambiguous=True)
        return RESULT

    detector = Detector(sometimes)
    instance = runtime(environment, detector, concurrency=1)
    with pytest.raises(ProviderError, match="provider_unavailable"):
        observe(instance, source)
    assert len(detector.calls) == 3
    result = observe(instance, source)
    assert len(detector.calls) == 5
    assert [item["inference"]["source"] for item in result] == ["cache", "cache", "provider", "provider"]
    assert spent(repo) == 5  # Failed ambiguous attempt remains charged.
    assert instance.cache_stats()["dispatch_attempts"] == {"succeeded": 4, "failed": 1}


def test_successful_inflight_frame_survives_sibling_failure(environment):
    _, media, _ = environment
    source = frames(media, 3)
    first_bytes = media.read(source[0]["asset_id"])
    both_started = threading.Barrier(2)

    def callback(image, *_):
        both_started.wait(timeout=3)
        if image == first_bytes:
            raise ProviderError("provider_unavailable", retryable=True)
        time.sleep(0.05)
        return RESULT

    detector = Detector(callback)
    instance = runtime(environment, detector)
    with pytest.raises(ProviderError, match="provider_unavailable"):
        observe(instance, source)
    assert len(detector.calls) == 2
    assert instance.cache_stats()["cached_assets"] == 1
    detector.callback = None
    result = observe(instance, source)
    assert len(detector.calls) == 4
    assert result[1]["inference"]["source"] == "cache"


def test_lease_replacement_rejects_late_result_and_counts_both_attempts(environment):
    repo, media, _ = environment
    instance = runtime(environment)
    instance.initialize()
    frame = frames(media)[0]
    first, second = str(uuid4()), str(uuid4())
    assert instance._claim(frame, first, "s", "j1")[0] == "dispatch"
    expire(repo)
    assert instance._claim(frame, second, "s", "j2")[0] == "dispatch"
    assert instance._finish(instance.cache_key(frame), first, RESULT) is None
    assert instance._finish(instance.cache_key(frame), second, RESULT) is not None
    assert spent(repo) == 2
    assert instance.cache_stats()["dispatch_attempts"] == {"succeeded": 1, "abandoned": 1}


def test_model_config_and_dimensions_are_part_of_cache_identity(environment):
    repo, media, _ = environment
    detector = Detector()
    first = runtime(environment, detector)
    source = frames(media)
    observe(first, source)
    second = runtime(environment, detector, detector_configuration={**IDENTITY, "model_id": "no-model/2"})
    observe(second, source)
    third = runtime(environment, detector, detector_configuration={**IDENTITY, "normalizer": "test-v2"})
    observe(third, source)
    assert first.cache_key(source[0]) != first.cache_key({**source[0], "width": 81})
    assert len(detector.calls) == spent(repo) == 3


def test_global_and_per_session_limits_hold_across_independent_workers(environment):
    _, media, _ = environment
    groups = [frames(media, 5, start=group * 10) for group in range(3)]
    owners = {media.read(frame["asset_id"]): group for group, source in enumerate(groups) for frame in source}
    lock = threading.Lock()
    active = [0, 0, 0]
    maximum = [0, 0, 0]
    global_max = 0

    def callback(image, *_):
        nonlocal global_max
        owner = owners[image]
        with lock:
            active[owner] += 1
            maximum[owner] = max(maximum[owner], active[owner])
            global_max = max(global_max, sum(active))
        time.sleep(0.07)
        with lock:
            active[owner] -= 1
        return RESULT

    detector = Detector(callback)
    instances = [runtime(environment, detector, global_limit=3) for _ in groups]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(observe, instances[i], source, f"session-{i}") for i, source in enumerate(groups)
        ]
        for future in futures:
            assert len(future.result(timeout=10)) == 5
    assert 1 < global_max <= 3
    assert all(1 <= count <= 2 for count in maximum)
    assert len(detector.calls) == 15


def test_waiting_new_session_gets_next_slot_before_busy_session(environment):
    _, media, _ = environment
    instance = runtime(environment, concurrency=1, global_limit=1)
    instance.initialize()
    source = frames(media, 3)
    first, busy, newcomer = (str(uuid4()) for _ in range(3))
    assert instance._claim(source[0], first, "busy-session", "j1")[0] == "dispatch"
    assert instance._claim(source[1], busy, "busy-session", "j2")[0] == "wait"
    assert instance._claim(source[2], newcomer, "new-session", "j3")[0] == "wait"
    instance._finish(instance.cache_key(source[0]), first, RESULT)
    assert instance._claim(source[1], busy, "busy-session", "j2")[0] == "wait"
    assert instance._claim(source[2], newcomer, "new-session", "j3")[0] == "dispatch"


def test_budget_is_atomic_under_parallel_workers_and_not_charged_on_hits(environment):
    repo, media, config = environment
    config.inference_limit = 3
    detector = Detector()
    instances = [runtime(environment, detector) for _ in range(6)]
    source = frames(media, 6)

    def work(index):
        try:
            return observe(instances[index], [source[index]], f"session-{index}")
        except ProviderError as error:
            assert error.code == "budget_exhausted"
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        result = list(pool.map(work, range(6)))
    assert sum(item is not None for item in result) == 3
    assert len(detector.calls) == spent(repo) == 3
    successful = next(index for index, item in enumerate(result) if item is not None)
    assert observe(instances[0], [source[successful]])[0]["inference"]["source"] == "cache"
    assert spent(repo) == 3


def test_zero_budget_does_not_dispatch(environment):
    repo, media, config = environment
    config.inference_limit = 0
    detector = Detector()
    instance = runtime(environment, detector)
    with pytest.raises(ProviderError, match="budget_exhausted"):
        observe(instance, frames(media))
    assert detector.calls == []
    assert spent(repo) == 0
    assert instance.cache_stats()["waiting_frames"] == 0


def test_budget_failure_keeps_partial_sequence_resumable(environment):
    repo, media, config = environment
    config.inference_limit = 2
    detector = Detector()
    instance = runtime(environment, detector, concurrency=1)
    source = frames(media, 3)
    with pytest.raises(ProviderError, match="budget_exhausted"):
        observe(instance, source)
    assert instance.cache_stats()["cached_assets"] == 2
    # An operator budget increase is explicit; credits are never assumed/reset.
    config.inference_limit = 3
    result = observe(instance, source)
    assert len(result) == 3
    assert len(detector.calls) == spent(repo) == 3


def test_capture_and_frame_heartbeats_continue_during_slow_io(environment):
    repo, media, _ = environment
    beats = []

    def callback(*_):
        time.sleep(0.3)
        with repo._connection() as connection:
            active = connection.execute(
                "SELECT lease_expires_at>clock_timestamp() AS fresh FROM counterpart_inference_attempts"
            ).fetchone()
        assert active["fresh"]
        return RESULT

    def heartbeat():
        beats.append(time.monotonic())
        return True

    instance = runtime(environment, Detector(callback), lease_seconds=0.2, heartbeat_seconds=0.04)
    result = observe(instance, frames(media), heartbeat=heartbeat)
    assert len(result) == 1
    assert len(beats) >= 4
    assert instance.cache_stats()["dispatch_attempts"] == {"succeeded": 1}


def test_lost_capture_lease_stops_new_dispatch_but_saves_completed_inflight(environment):
    _, media, _ = environment
    beats = 0

    def heartbeat():
        nonlocal beats
        beats += 1
        return beats < 2

    def callback(*_):
        time.sleep(0.2)
        return RESULT

    detector = Detector(callback)
    instance = runtime(environment, detector, concurrency=1, heartbeat_seconds=0.04)
    with pytest.raises(ProviderError, match="capture_lease_lost"):
        observe(instance, frames(media, 3), heartbeat=heartbeat)
    assert len(detector.calls) == 1
    assert instance.cache_stats()["cached_assets"] == 1


def test_corrupt_media_rejected_even_if_observation_cached(environment):
    _, media, _ = environment
    detector = Detector()
    instance = runtime(environment, detector)
    source = frames(media)
    observe(instance, source)
    media.path(source[0]["asset_id"]).write_bytes(b"tampered")
    with pytest.raises(InvalidImage, match="integrity"):
        observe(instance, source)
    assert len(detector.calls) == 1


def test_inconsistent_cross_worker_concurrency_fails_closed(environment):
    first = runtime(environment)
    first.initialize()
    with pytest.raises(ProviderError, match="inference_configuration_mismatch"):
        runtime(environment, global_limit=5).initialize()


def test_expired_waiter_does_not_block_live_session(environment):
    repo, media, _ = environment
    instance = runtime(environment, concurrency=1, global_limit=1)
    instance.initialize()
    source = frames(media, 2)
    with repo._connection() as connection:
        connection.execute(
            "INSERT INTO counterpart_inference_waiters(id,cache_key,session_id,expires_at) "
            "VALUES (%s,%s,'dead-session',clock_timestamp()-interval '1 second')",
            (str(uuid4()), instance.cache_key(source[0])),
        )
    result = observe(instance, [source[1]])
    assert result[0]["inference"]["source"] == "provider"
    assert instance.cache_stats()["waiting_frames"] == 0


def test_provider_secret_exception_is_not_persisted(environment):
    repo, media, _ = environment
    secret = "https://secret.example?api_key=secret-value"

    def fail(*_):
        raise RuntimeError(secret)

    instance = runtime(environment, Detector(fail))
    with pytest.raises(RuntimeError):
        observe(instance, frames(media))
    with repo._connection() as connection:
        rows = connection.execute("SELECT * FROM counterpart_inference_attempts").fetchall()
    assert secret not in str(rows)
    assert rows[0]["error_code"] == "inference_failed"


def test_actual_process_death_after_dispatch_then_lease_reclaim(environment, tmp_path):
    repo, media, config = environment
    source = frames(media)
    marker = tmp_path / "dispatched"
    program = """
import json, os, sys
from pathlib import Path
from counterpart.config import Settings
from counterpart.repository import Repository
from counterpart.storage import MediaStore
from counterpart.inference_runtime import InferenceRuntime
class ExitsInsideCall:
    def detect(self, *args):
        Path(sys.argv[4]).write_text('mock dispatched; response unavailable')
        os._exit(41)
config = Settings(database_url=sys.argv[1], media_dir=Path(sys.argv[2]), inference_limit=100)
instance = InferenceRuntime(Repository(config.database_url), MediaStore(config.media_dir), config,
    ExitsInsideCall(), lease_seconds=0.4, heartbeat_seconds=0.05, poll_seconds=0.01,
    detector_configuration={'provider':'test','model_id':'no-model-executed/1','normalizer':'test-v1'})
instance.observe_frames([json.loads(sys.argv[3])], session_id='crashed', job_key='crashed-job')
"""
    import json

    child = subprocess.run(
        [sys.executable, "-c", program, repo.dsn, str(media.root), json.dumps(source[0]), str(marker)],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 41
    assert marker.exists()
    assert spent(repo) == 1
    # Let the actual frame lease expire: no mutation pretending recovery passed.
    time.sleep(0.45)
    detector = Detector()
    replacement = runtime(environment, detector)
    result = observe(replacement, source, "replacement")
    assert result[0]["inference"]["source"] == "provider"
    assert len(detector.calls) == 1 and spent(repo) == 2
    assert replacement.cache_stats()["dispatch_attempts"] == {"succeeded": 1, "abandoned": 1}


def test_parallel_results_preserve_input_order(environment):
    _, media, _ = environment
    source = frames(media, 6)
    order = {media.read(frame["asset_id"]): index for index, frame in enumerate(source)}

    def callback(image, *_):
        index = order[image]
        time.sleep(0.03 if index % 2 == 0 else 0.001)
        return {**RESULT, "marker": index}

    result = observe(runtime(environment, Detector(callback)), source)
    assert [item["marker"] for item in result] == list(range(6))
    assert [item["t_ms"] for item in result] == [item["t_ms"] for item in source]


@pytest.mark.parametrize(
    "options",
    [
        {"concurrency": 0},
        {"concurrency": True},
        {"global_limit": 0},
        {"concurrency": 5, "global_limit": 4},
        {"lease_seconds": float("nan")},
        {"lease_seconds": 1, "heartbeat_seconds": 1},
        {"wait_seconds": 0},
    ],
)
def test_invalid_dispatch_configuration_rejected(environment, options):
    with pytest.raises(ValueError):
        runtime(environment, **options)


def test_progress_counts_repeated_frames_without_duplicate_compute(environment):
    _, media, _ = environment
    detector = Detector()
    instance = runtime(environment, detector)
    source = frames(media, 2)
    sequence = [source[0], {**source[0], "t_ms": 50}, source[1]]
    assert instance.progress(sequence) == {
        "total_frames": 3,
        "observed_frames": 0,
        "unique_assets": 2,
        "observed_assets": 0,
        "active_assets": 0,
    }
    observe(instance, source[:1])
    assert instance.progress(sequence) == {
        "total_frames": 3,
        "observed_frames": 2,
        "unique_assets": 2,
        "observed_assets": 1,
        "active_assets": 0,
    }
    assert len(detector.calls) == 1


def test_read_only_production_identity_matches_real_adapter_without_credentials(environment):
    from counterpart.providers import RoboflowDetector

    repo, media, config = environment
    # Adapter construction doesn't make an HTTP request; close its idle client.
    with RoboflowDetector("fake-local-test-value") as detector:
        worker = InferenceRuntime(repo, media, config, detector)
        observer = InferenceRuntime(repo, media, config, None)
        assert worker.model_config == observer.model_config


def test_worker_registry_marks_stale_and_gracefully_stopped(environment):
    repo, _, _ = environment
    instance = runtime(environment)
    assert instance.worker_status() == {
        "active_workers": 0,
        "last_seen": None,
        "stale_after_seconds": 15,
    }
    generation = instance.worker_started("capture-worker")
    assert instance.worker_status()["active_workers"] == 1
    with repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_workers SET last_seen_at=clock_timestamp()-interval '16 seconds'"
        )
    assert instance.worker_status()["active_workers"] == 0
    assert instance.worker_heartbeat("capture-worker", generation)
    assert instance.worker_status()["active_workers"] == 1
    assert instance.worker_stopped("capture-worker", generation)
    status = instance.worker_status()
    assert status["active_workers"] == 0 and status["last_seen"] is not None
    assert not instance.worker_heartbeat("capture-worker", generation)
    assert not instance.worker_stopped("capture-worker", generation)


def test_old_worker_generation_cannot_revive_or_stop_replacement(environment):
    instance = runtime(environment)
    old = instance.worker_started("same-name")
    new = instance.worker_started("same-name")
    assert old != new
    assert not instance.worker_heartbeat("same-name", old)
    assert not instance.worker_stopped("same-name", old)
    assert instance.worker_heartbeat("same-name", new)
    assert instance.worker_status()["active_workers"] == 1


def test_worker_registration_cleanup_is_bounded_and_preserves_budget(environment):
    repo, _, _ = environment
    instance = runtime(environment)
    instance.initialize()
    repo.reserve_budget("roboflow", "existing-spend", 1, 100)
    with repo._connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO counterpart_workers(worker_id,generation,state,last_seen_at) "
                "VALUES (%s,%s,'active',clock_timestamp()-interval '25 hours')",
                [(f"old-{index}", str(uuid4())) for index in range(250)],
            )
    instance.worker_started("new-worker")
    with repo._connection() as connection:
        count = connection.execute("SELECT count(*) AS count FROM counterpart_workers").fetchone()["count"]
    assert count == 151  # At most 100 removed by a registration.
    assert instance.worker_status()["active_workers"] == 1
    assert spent(repo) == 1


def test_idle_worker_run_renews_presence_and_marks_graceful_stop(environment, monkeypatch):
    import counterpart.worker as worker_module

    repo, media, config = environment
    monkeypatch.setattr(worker_module, "WORKER_HEARTBEAT_SECONDS", 0.04)
    worker = worker_module.Worker(repo, media, config, worker_id="idle-test")
    inspector = runtime(environment)
    inspector.initialize()
    saw_renewal = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        process = pool.submit(worker.run)
        try:
            deadline, first_seen = time.monotonic() + 3, None
            while time.monotonic() < deadline:
                status = inspector.worker_status()
                if status["active_workers"]:
                    if first_seen is None:
                        first_seen = status["last_seen"]
                    elif first_seen != status["last_seen"]:
                        saw_renewal = True
                        break
                time.sleep(0.01)
        finally:
            worker.stop.set()
            process.result(timeout=5)
    assert saw_renewal
    assert inspector.worker_status()["active_workers"] == 0


def test_busy_worker_heartbeat_does_not_wait_for_job_completion(environment, monkeypatch):
    import counterpart.worker as worker_module

    repo, media, config = environment
    monkeypatch.setattr(worker_module, "WORKER_HEARTBEAT_SECONDS", 0.04)
    worker = worker_module.Worker(repo, media, config, worker_id="busy-test")
    started, release = threading.Event(), threading.Event()

    def occupied():
        started.set()
        release.wait(3)
        return True

    monkeypatch.setattr(worker, "once", occupied)
    inspector = runtime(environment)
    inspector.initialize()
    with ThreadPoolExecutor(max_workers=1) as pool:
        process = pool.submit(worker.run)
        try:
            assert started.wait(2)
            first = inspector.worker_status()["last_seen"]
            time.sleep(0.15)
            after = inspector.worker_status()
            assert after["active_workers"] == 1 and after["last_seen"] != first
        finally:
            worker.stop.set()
            release.set()
            process.result(timeout=5)
    assert inspector.worker_status()["active_workers"] == 0
