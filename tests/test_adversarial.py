"""Independent failure counterexamples, not detector-quality measurements.

Database tests use a random schema in the dedicated counterpart_test database.
The observations below are deliberately synthetic; they validate decision and
transaction semantics without performing local or cloud model computation.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.repository import Conflict, Repository
from counterpart.verification import verify


@pytest.fixture
def adversarial_repo():
    dsn = os.getenv("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "adversarial_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repo = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    repo.initialize()
    try:
        yield repo
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _observation(bottle_x: float, cup_x: float, *, t_ms: float = 0, temporal=False) -> dict:
    objects = [("bottle", bottle_x, 0.28 if temporal else 0.5), ("cup", cup_x, 0.72 if temporal else 0.5)]
    return {
        "asset_id": hashlib.sha256(repr((objects, t_ms)).encode()).hexdigest(),
        "width": 800,
        "height": 600,
        "t_ms": t_ms,
        "detections": [
            {"label": label, "confidence": 0.92, "box": [x - 0.045, y - 0.09, x + 0.045, y + 0.09]}
            for label, x, y in objects
        ],
        "model": {"provider": "test_observations", "model_id": "no-model-executed"},
    }


def _sequence(*, reverse=False):
    return [
        _observation(
            0.2 if i < (7 if reverse else 3) else 0.75,
            0.2 if i < (3 if reverse else 7) else 0.75,
            t_ms=i * 250,
            temporal=True,
        )
        for i in range(12)
    ]


def _submit(repo, session, slot, *, frames=None, client_seq=0, key=None):
    frames = frames or [_observation(0.20 if slot == "baseline" else 0.80, 0.5)]
    metadata = {
        "slot": slot,
        "expected_revision": session["revision"],
        "epoch": session["epoch"],
        "client_seq": client_seq,
        "idempotency_key": key or uuid4().hex,
        "interrupted": False,
    }
    fingerprint = hashlib.sha256(json.dumps([metadata, frames], sort_keys=True).encode()).hexdigest()
    return repo.submit(session["id"], metadata, frames, fingerprint)


def _finish(repo, claim):
    session = claim["session"]
    reference = None
    if session["baseline"]:
        reference = {**session["baseline"]["result"], "frames": session["baseline"]["frames"]}
    result = verify(session["recipe"], claim["capture"]["slot"], claim["capture"]["frames"], reference)
    return repo.finish(claim["id"], claim["lease_token"], result, claim["capture"]["frames"])


def _accepted_baseline(repo, *, mode="replay"):
    session = repo.create_session("spatial", mode, "bottle", "cup")["session"]
    _submit(repo, session, "baseline")
    assert _finish(repo, repo.claim("test-worker"))
    return repo.snapshot(session["id"])


@pytest.mark.postgres
def test_resume_during_first_inference_preserves_baseline_but_requires_regrounding(adversarial_repo):
    repo = adversarial_repo
    session = repo.create_session("spatial", "replay", "bottle", "cup")["session"]
    _submit(repo, session, "baseline")
    original_worker = repo.claim("paused-before-reconnect")
    repo.resume(session["id"], 0)
    assert _finish(repo, original_worker), "Immutable saved evidence should survive reconnect."
    resumed = repo.snapshot(session["id"])
    assert resumed["baseline"]["status"] == "accepted"
    assert resumed["recovery_required"], "Old-epoch baseline completion must not attest the new scene."
    with pytest.raises(Conflict, match="scene"):
        _submit(repo, resumed, "counterpart")


@pytest.mark.postgres
def test_expired_preview_is_discarded_before_dispatch(adversarial_repo):
    repo = adversarial_repo
    session = repo.create_session("spatial", "replay", "bottle", "cup")["session"]
    submission = _submit(repo, session, "preview", frames=[_observation(0.2, 0.5)], client_seq=1)
    with psycopg.connect(repo.dsn) as conn:
        conn.execute(
            "UPDATE counterpart_captures SET created_at=clock_timestamp()-interval '1 minute' WHERE id=%s",
            (submission["capture"]["id"],),
        )
    assert repo.claim("must-not-dispatch-stale-work") is None


@pytest.mark.postgres
def test_preview_from_replaced_revision_does_not_dispatch(adversarial_repo):
    repo = adversarial_repo
    session = repo.create_session("spatial", "replay", "bottle", "cup")["session"]
    _submit(repo, session, "preview", frames=[_observation(0.2, 0.5)], client_seq=1)
    _submit(repo, session, "baseline")
    committed = repo.claim("committed-worker")
    assert committed["capture"]["slot"] == "baseline"
    assert _finish(repo, committed)
    assert repo.claim("must-not-spend-on-replaced-revision") is None


@pytest.mark.postgres
def test_live_mode_does_not_claim_independent_physical_validation(adversarial_repo):
    repo = adversarial_repo
    session = _accepted_baseline(repo, mode="live")
    _submit(repo, session, "counterpart")
    assert _finish(repo, repo.claim("test-worker"))
    session = repo.snapshot(session["id"])
    publication = repo.publish(session["id"], session["revision"])
    assert publication["manifest"]["provenance"].get("physical_capture_validated") is not True


@pytest.mark.postgres
def test_old_recovery_result_cannot_unlock_new_epoch(adversarial_repo):
    repo = adversarial_repo
    session = _accepted_baseline(repo)
    resumed = repo.resume(session["id"], session["epoch"])
    _submit(repo, resumed, "recovery")
    old_recovery = repo.claim("old-recovery-worker")
    repo.resume(session["id"], resumed["epoch"])
    assert _finish(repo, old_recovery) is False
    assert repo.snapshot(session["id"])["recovery_required"]


@pytest.mark.postgres
def test_expired_lease_cannot_be_revived_even_before_reclaim(adversarial_repo):
    repo = adversarial_repo
    session = repo.create_session("spatial", "replay", "bottle", "cup")["session"]
    _submit(repo, session, "baseline")
    old = repo.claim("expired-worker")
    with psycopg.connect(repo.dsn) as conn:
        conn.execute(
            "UPDATE counterpart_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (old["id"],),
        )
    assert repo.heartbeat(old["id"], old["lease_token"]) is False
    assert _finish(repo, old) is False
    new = repo.claim("replacement-worker")
    assert new["id"] == old["id"]
    assert new["lease_token"] != old["lease_token"]
    assert _finish(repo, new)


@pytest.mark.postgres
def test_new_baseline_never_reuses_old_counterpart(adversarial_repo):
    repo = adversarial_repo
    session = _accepted_baseline(repo)
    _submit(repo, session, "counterpart")
    assert _finish(repo, repo.claim("test-worker"))
    ready = repo.snapshot(session["id"])
    old_baseline_id = ready["baseline"]["id"]
    _submit(repo, ready, "baseline")
    replacing = repo.snapshot(session["id"])
    with pytest.raises(Conflict):
        repo.publish(session["id"], replacing["revision"])
    assert _finish(repo, repo.claim("test-worker"))
    replaced = repo.snapshot(session["id"])
    assert replaced["baseline"]["id"] != old_baseline_id
    assert replaced["counterpart"] is None
    assert replaced["state"] == "baseline_ready"
    with pytest.raises(Conflict):
        repo.publish(session["id"], replaced["revision"])


@pytest.mark.postgres
def test_budget_race_respects_global_limit_and_keeps_retries_charged(adversarial_repo):
    repo = adversarial_repo
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda i: repo.reserve_budget("mock", f"attempt-{i}", 1, 5), range(24)))
    assert sum(outcomes) == 5
    assert repo.metrics()["budget_reservations"] == [{"provider": "mock", "reserved_units": 5}]
    assert repo.reserve_budget("mock", "another-possible-execution", 1, 5) is False


@pytest.mark.postgres
def test_preview_results_remain_useful_when_newer_preview_is_pending(adversarial_repo):
    repo = adversarial_repo
    session = repo.create_session("spatial", "replay", "bottle", "cup")["session"]
    _submit(repo, session, "preview", frames=[_observation(0.2, 0.5)], client_seq=1)
    first = repo.claim("preview-worker")
    _submit(repo, session, "preview", frames=[_observation(0.2, 0.5)], client_seq=2)
    assert _finish(repo, first), "Latest-submitted-only fencing would starve all feedback at sustained load."
    assert repo.snapshot(session["id"])["feedback"]["client_seq"] == 1
    assert _finish(repo, repo.claim("preview-worker"))
    assert repo.snapshot(session["id"])["feedback"]["client_seq"] == 2


def test_temporal_reentry_and_gap_do_not_manufacture_order():
    frames = _sequence()
    assert verify("temporal", "baseline", frames)["accepted"]
    reentered = deepcopy(frames)
    reentered[5] = _observation(0.2, 0.2, t_ms=1250, temporal=True)
    decision = verify("temporal", "baseline", reentered)
    assert not decision["accepted"]
    assert decision["code"] == "unstable_arrival"
    missing_time = deepcopy(frames)
    for frame in missing_time[6:]:
        frame["t_ms"] += 1000
    decision = verify("temporal", "baseline", missing_time)
    assert not decision["accepted"]
    assert decision["code"] == "observation_gap"


def test_temporal_simultaneous_interval_abstains_and_opposite_order_passes():
    baseline_frames = _sequence()
    baseline = verify("temporal", "baseline", baseline_frames)
    baseline["frames"] = baseline_frames
    reverse = verify("temporal", "counterpart", _sequence(reverse=True), baseline)
    assert reverse["accepted"]
    same_interval = [
        _observation(0.2 if i < 4 else 0.75, 0.2 if i < 4 else 0.75, t_ms=i * 250, temporal=True)
        for i in range(12)
    ]
    ambiguous = verify("temporal", "baseline", same_interval)
    assert not ambiguous["accepted"]
    assert ambiguous["code"] == "ambiguous_order"


def test_temporal_recovery_allows_deliberate_start_position_reset():
    frames = _sequence()
    baseline = {**verify("temporal", "baseline", frames), "frames": frames}
    reset = [_observation(0.2, 0.2, temporal=True)]
    recovered = verify("temporal", "recovery", reset, baseline)
    assert recovered["accepted"]
    assert recovered["evidence"]["can_complete_pair"] is False


def test_second_plausible_object_makes_spatial_identity_ambiguous():
    frames = [_observation(0.2, 0.5)]
    frames[0]["detections"].append({"label": "bottle", "confidence": 0.36, "box": [0.72, 0.4, 0.82, 0.6]})
    decision = verify("spatial", "baseline", frames)
    assert not decision["accepted"]
    assert decision["code"] == "ambiguous_object"


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_chunked_body_limit_applies_to_actual_bytes_before_route_execution(declared_length):
    from counterpart.api import BoundedRequest

    entered_route = []
    sent = []
    messages = iter(
        [
            {"type": "http.request", "body": b"123456", "more_body": True},
            {"type": "http.request", "body": b"abcdef", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def inner(_scope, _receive, _send):
        entered_route.append(True)

    headers = [] if declared_length is None else [(b"content-length", declared_length.encode())]
    scope = {"type": "http", "method": "POST", "headers": headers, "path": "/api/sessions"}
    asyncio.run(BoundedRequest(inner, 10)(scope, receive, send))
    assert not entered_route
    assert sent[0]["status"] == 413
    assert b"body_too_large" in sent[1]["body"]


def test_chunked_body_below_limit_reaches_route_once_with_exact_bytes():
    from counterpart.api import BoundedRequest

    received = []
    messages = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(_message):
        raise AssertionError("The middleware should not emit an error for a bounded request.")

    async def inner(_scope, downstream, _send):
        received.append(await downstream())

    asyncio.run(BoundedRequest(inner, 10)({"type": "http", "method": "POST"}, receive, send))
    assert received == [{"type": "http.request", "body": b"123456", "more_body": False}]


@pytest.mark.postgres
def test_live_api_worker_provider_and_publication_preserve_exact_evidence(adversarial_repo, tmp_path):
    """Mocked transport, real API/storage/worker/DB; no network or ML execution."""
    from counterpart.api import create_app
    from counterpart.config import Settings
    from counterpart.providers import RoboflowDetector
    from counterpart.storage import MediaStore
    from counterpart.worker import Worker

    repo = adversarial_repo
    credential_file = tmp_path / "fake-credentials.json"
    credential_file.write_text(json.dumps({"ROBOFLOW_API_KEY": "counterpart-test-no-real-key"}))
    config = Settings(
        database_url=repo.dsn,
        media_dir=tmp_path / "media",
        web_dir=tmp_path / "no-web",
        credentials_file=credential_file,
        live_enabled=True,
        inference_limit=2,
    )
    originals = []
    for color in ("navy", "gold"):
        target = io.BytesIO()
        Image.new("RGB", (800, 600), color).save(target, "PNG")
        originals.append(target.getvalue())
    calls = []

    def fake_provider(request):
        assert request.url.path == "/coco/39"
        raw = base64.b64decode(request.content)
        assert raw in originals, "The worker must use saved bytes, not browser-supplied predictions."
        calls.append(raw)
        bottle_x = 160 if raw == originals[0] else 640
        return httpx.Response(
            200,
            json={
                "image": {"width": 800, "height": 600},
                "model_id": "coco/39",
                "predictions": [
                    {
                        "class": "bottle",
                        "confidence": 0.95,
                        "x": bottle_x,
                        "y": 300,
                        "width": 72,
                        "height": 108,
                    },
                    {"class": "cup", "confidence": 0.94, "x": 400, "y": 300, "width": 72, "height": 108},
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(fake_provider)) as transport:
        detector = RoboflowDetector("counterpart-test-no-real-key", client=transport)
        worker = Worker(repo, MediaStore(config.media_dir), config, detector=detector)
        with TestClient(create_app(config, repo)) as client:
            # This transport-only fixture is a historical v1 recipe. Solid
            # colors deliberately do not purport to satisfy v2 registration.
            from counterpart.execution_contract import build_execution_contract
            from counterpart.recipes import build_recipe_snapshot

            with repo._connection() as connection:
                created = repo._create_session(
                    connection,
                    "spatial",
                    "live",
                    "bottle",
                    "cup",
                    recipe_snapshot=build_recipe_snapshot("spatial"),
                    execution_contract=build_execution_contract(config),
                )
            session, token = created["session"], created["token"]
            headers = {"Authorization": f"Bearer {token}"}
            path = f"/api/sessions/{session['id']}"
            for slot, raw in zip(("baseline", "counterpart"), originals, strict=True):
                metadata = {
                    "slot": slot,
                    "expected_revision": session["revision"],
                    "epoch": session["epoch"],
                    "idempotency_key": uuid4().hex,
                    "timestamps_ms": [0],
                    "client_seq": 0,
                }
                response = client.post(
                    path + "/captures",
                    headers=headers,
                    data={"metadata": json.dumps(metadata)},
                    files={"frames": ("evidence.png", raw, "image/png")},
                )
                assert response.status_code == 202
                capture_id = response.json()["capture"]["id"]
                assert worker.once()
                session = client.get(path, headers=headers).json()
                assert session[slot]["status"] == "accepted"
                assert session[slot]["frames"][0]["asset_id"] == hashlib.sha256(raw).hexdigest()
                # A lost acknowledgement can be retried without another capture or model call.
                repeated = client.post(
                    path + "/captures",
                    headers=headers,
                    data={"metadata": json.dumps(metadata)},
                    files={"frames": ("evidence.png", raw, "image/png")},
                )
                assert repeated.status_code == 202
                assert repeated.json()["capture"]["id"] == capture_id
                assert not worker.once()
            assert session["state"] == "ready"
            published = client.post(
                path + "/publish", headers=headers, json={"expected_revision": session["revision"]}
            )
            assert published.status_code == 200
            pair = published.json()["pair"]
            assert pair["manifest"]["baseline"]["frames"][0]["model"]["provider"] == "roboflow"
            assert (
                pair["manifest"]["counterpart"]["frames"][0]["asset_id"]
                == hashlib.sha256(originals[1]).hexdigest()
            )
            assert pair["export_status"] != "complete", "Local publish is not remote export."
            assert token not in json.dumps(pair)
            assert len(calls) == 2
            assert repo.metrics()["budget_reservations"] == [{"provider": "roboflow", "reserved_units": 2}]
        detector.close()


@pytest.mark.postgres
def test_abrupt_worker_exit_reclaims_saved_evidence_and_charges_possible_retry(adversarial_repo, tmp_path):
    """A real child process dies after its mock provider responds, before acceptance."""
    from counterpart.config import Settings
    from counterpart.storage import MediaStore
    from counterpart.worker import Worker

    repo = adversarial_repo
    media = MediaStore(tmp_path / "crash-media")
    raw = io.BytesIO()
    Image.new("RGB", (800, 600), "navy").save(raw, "JPEG")
    saved = {**media.put(raw.getvalue()), "t_ms": 0}
    from counterpart.recipes import build_recipe_snapshot

    with repo._connection() as connection:
        session = repo._create_session(
            connection,
            "spatial",
            "live",
            "bottle",
            "cup",
            recipe_snapshot=build_recipe_snapshot("spatial"),
        )["session"]
    submitted = _submit(repo, session, "baseline", frames=[saved])
    marker = tmp_path / "mock-provider-completed"
    # No real adapter or credentials: this is a crash/transaction experiment only.
    program = """
import os, sys
from pathlib import Path
from counterpart.config import Settings
from counterpart.repository import Repository
from counterpart.storage import MediaStore
from counterpart.worker import Worker

import httpx
from counterpart.providers import RoboflowDetector

def completed_then_lost(request):
    Path(sys.argv[3]).write_text('mock provider completed; receipt lost')
    os._exit(41)

config = Settings(database_url=sys.argv[1], media_dir=Path(sys.argv[2]),
                  live_enabled=True, inference_limit=2)
with httpx.Client(transport=httpx.MockTransport(completed_then_lost)) as transport:
    detector = RoboflowDetector('test-no-provider-key', client=transport)
    Worker(Repository(config.database_url), MediaStore(config.media_dir), config,
           detector=detector, worker_id='abrupt-subprocess').once()
"""
    child = subprocess.run(
        [sys.executable, "-c", program, repo.dsn, str(media.root), str(marker)],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert child.returncode == 41
    assert marker.exists()
    after_crash = repo.snapshot(session["id"])
    assert after_crash["baseline"] is None
    assert after_crash["latest_capture"]["status"] == "running"
    assert repo.metrics()["budget_reservations"] == [{"provider": "roboflow", "reserved_units": 1}]
    with psycopg.connect(repo.dsn) as conn:
        original = conn.execute(
            "SELECT id,lease_token FROM counterpart_jobs WHERE capture_id=%s",
            (submitted["capture"]["id"],),
        ).fetchone()
        conn.execute(
            "UPDATE counterpart_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (original[0],),
        )
        # The same adapter namespace now safely waits for its own frame lease.
        # Advance both expired owners, rather than switching model namespaces.
        conn.execute(
            "UPDATE counterpart_inference_cache SET lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE status='running'"
        )
        conn.execute(
            "UPDATE counterpart_inference_attempts SET "
            "lease_expires_at=clock_timestamp()-interval '1 second' WHERE status='running'"
        )

    from counterpart.providers import RoboflowDetector

    def replacement_response(request):
        assert base64.b64decode(request.content) == raw.getvalue()
        return httpx.Response(
            200,
            json={
                "image": {"width": 800, "height": 600},
                "model_id": "coco/39",
                "predictions": [
                    {"class": "bottle", "confidence": 0.99, "x": 160, "y": 300, "width": 72, "height": 108},
                    {"class": "cup", "confidence": 0.99, "x": 400, "y": 300, "width": 72, "height": 108},
                ],
            },
        )

    config = Settings(database_url=repo.dsn, media_dir=media.root, live_enabled=True, inference_limit=2)
    detector = RoboflowDetector(
        "test-no-provider-key", client=httpx.Client(transport=httpx.MockTransport(replacement_response))
    )
    replacement = Worker(repo, media, config, detector=detector, worker_id="replacement-process")
    assert replacement.once()
    final = repo.snapshot(session["id"])
    assert final["baseline"]["id"] == submitted["capture"]["id"]
    assert final["baseline"]["status"] == "accepted"
    assert repo.metrics()["budget_reservations"] == [{"provider": "roboflow", "reserved_units": 2}]
    assert repo.heartbeat(str(original[0]), str(original[1])) is False
    assert not replacement.once()
    detector.close()


@pytest.mark.postgres
def test_worker_rejects_changed_frozen_recipe_before_provider_dispatch(adversarial_repo, tmp_path):
    from counterpart.config import Settings
    from counterpart.storage import MediaStore
    from counterpart.worker import Worker

    repo = adversarial_repo
    session = repo.create_session("spatial", "live", "bottle", "cup")["session"]
    _submit(repo, session, "baseline")
    with psycopg.connect(repo.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_sessions SET recipe_snapshot=jsonb_set(recipe_snapshot,"
            "'{recipe_version}','\"999.0.0\"') WHERE id=%s",
            (session["id"],),
        )

    class MustNotDispatch:
        def detect(self, *_):
            pytest.fail("An incompatible recipe cannot dispatch inference")

    config = Settings(database_url=repo.dsn, media_dir=tmp_path / "media", live_enabled=True)
    assert Worker(repo, MediaStore(config.media_dir), config, MustNotDispatch()).once()
    saved = repo.snapshot(session["id"])
    assert saved["latest_capture"]["status"] == "failed"
    assert saved["latest_capture"]["error"] == "unsupported_recipe"
    assert repo.metrics()["budget_reservations"] == []


@pytest.mark.postgres
def test_worker_retries_transient_shared_storage_outage_without_spending(adversarial_repo, tmp_path):
    from counterpart.config import Settings
    from counterpart.object_storage import MediaUnavailable
    from counterpart.worker import Worker

    repo = adversarial_repo
    session = repo.create_session("spatial", "live", "bottle", "cup")["session"]
    _submit(repo, session, "baseline")

    class UnavailableStorage:
        def read(self, *_):
            raise MediaUnavailable("Evidence storage could not read this asset.")

    from counterpart.providers import RoboflowDetector

    def must_not_dispatch(request):
        pytest.fail("No provider dispatch before immutable media is available")

    config = Settings(database_url=repo.dsn, media_dir=tmp_path / "media", live_enabled=True)
    with RoboflowDetector(
        "test-no-provider-key", client=httpx.Client(transport=httpx.MockTransport(must_not_dispatch))
    ) as detector:
        assert Worker(repo, UnavailableStorage(), config, detector).once()
    saved = repo.snapshot(session["id"])
    assert saved["latest_capture"]["status"] == "queued"
    assert saved["latest_capture"]["error"] == "media_unavailable"
    assert repo.metrics()["jobs"] == {"retry_wait": 1}
    assert repo.metrics()["budget_reservations"] == []
