#!/usr/bin/env python3
"""Bounded whole-product fault replay. No cloud calls, no ML, no credentials.

Uses a temporary PostgreSQL schema, generated illustrations, actual Worker child
process deaths, and a localhost fake HTTP detector. Timings describe this replay
on this machine, not detector quality or deployment capacity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from PIL import Image, ImageDraw
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.collections import CollectionRepository
from counterpart.config import Settings
from counterpart.execution_contract import build_execution_contract
from counterpart.fixtures import generate
from counterpart.inference_runtime import InferenceRuntime
from counterpart.recipes import build_recipe_snapshot
from counterpart.repository import Conflict, Repository
from counterpart.storage import MediaStore
from counterpart.worker import Worker


def fixture_configuration(endpoint: str) -> dict:
    """Explicit semantics for this local HTTP fixture, never a hosted-model claim."""
    return {
        "provider": "local_fake_http",
        "model_id": "generated-observations-v1",
        "endpoint": endpoint,
        "adapter": "product_fault_probe.FixtureHTTPDetector",
        "adapter_version": "product-fault-probe-v2",
        "normalizer_version": "fixture-normalized-xyxy-v1",
        "confidence_threshold": 0.0,
        "disable_active_learning": True,
    }


class FixtureHTTPDetector:
    model_id = "generated-observations-v1"

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def detect(self, image: bytes, width: int, height: int) -> dict:
        response = httpx.post(self.endpoint, content=image, timeout=5, trust_env=False)
        response.raise_for_status()
        result = response.json()
        assert result["width"] == width and result["height"] == height
        return {
            "detections": result["detections"],
            "model": {"provider": "local_fake_http", "model_id": self.model_id},
        }


class ExitBeforeAcceptanceRepository(Repository):
    def finish(self, *args, **kwargs):
        os._exit(73)  # Actual process death after durable frame checkpoints.


class BlockBeforeMediaRead(MediaStore):
    def __init__(self, root: Path, marker: Path):
        super().__init__(root)
        self.marker = marker

    def read(self, asset_id: str) -> bytes:
        self.marker.write_text("worker-has-claimed-saved-evidence\n")
        threading.Event().wait(30)
        raise RuntimeError("Parent should terminate this worker")


def configure(dsn: str, media_root: Path, live: bool = False) -> Settings:
    return Settings(
        database_url=dsn,
        media_dir=media_root,
        credentials_file=media_root / "no-credentials-exist",
        live_enabled=live,
        inference_limit=64,
        inference_concurrency=2,
        inference_global_limit=4,
        export_enabled=False,
    )


def make_worker(repo, media, config, endpoint=None, worker_id=None):
    detector = FixtureHTTPDetector(endpoint) if endpoint else None
    worker = Worker(repo, media, config, detector=detector, worker_id=worker_id)
    if detector:
        # Worker preflight and frame dispatch must share the exact contract.
        worker.runtime = InferenceRuntime(
            repo, media, config, detector, detector_configuration=fixture_configuration(endpoint)
        )
        worker.inference = worker.runtime
    return worker


def child(path: Path) -> None:
    value = json.loads(path.read_text())
    config = configure(value["dsn"], Path(value["media"]), value["stage"] == "after-checkpoints")
    if value["stage"] == "before-read":
        repo = Repository(value["dsn"])
        media = BlockBeforeMediaRead(Path(value["media"]), Path(value["marker"]))
        make_worker(repo, media, config, worker_id="probe-doomed-before-read").once()
    else:
        repo = ExitBeforeAcceptanceRepository(value["dsn"])
        make_worker(
            repo,
            MediaStore(Path(value["media"])),
            config,
            value["endpoint"],
            worker_id="probe-doomed-after-checkpoints",
        ).once()
    raise RuntimeError("Fault injection did not reach its intended boundary")


def child_process(directory: Path, payload: dict) -> subprocess.Popen:
    path = directory / f"child-{uuid4().hex}.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def frames(media, recipe, scenario, *, live=False, collector=None):
    result = []
    for frame in generate(recipe, scenario):
        image_bytes = frame["bytes"]
        if collector is not None:
            # Each synthetic collector has explicitly distinct test media. This
            # is fixture generation, not a way to bypass real-data leakage QA.
            image = Image.open(BytesIO(image_bytes))
            ImageDraw.Draw(image).text((30, 78), f"SYNTHETIC COLLECTOR {collector}", fill="#111111")
            encoded = BytesIO()
            image.save(encoded, format="JPEG", quality=88)
            image_bytes = encoded.getvalue()
        result.append(
            {
                **media.put(image_bytes),
                "t_ms": frame["t_ms"],
                **({} if live else {"fixture_detections": frame["detections"]}),
            }
        )
    return result


def submit(repo, saved, evidence, slot, sequence=0):
    key = uuid4().hex
    return repo.submit(
        saved["id"],
        {
            "slot": slot,
            "expected_revision": saved["revision"],
            "epoch": saved["epoch"],
            "client_seq": sequence,
            "idempotency_key": key,
        },
        evidence,
        key,
    )


def drain(repo, media, config, *, workers=4, endpoint=None):
    counts = []

    def run(index):
        worker = make_worker(repo, media, config, endpoint, worker_id=f"probe-drain-{index}")
        count = 0
        while worker.once():
            count += 1
            if count > 1000:
                raise AssertionError("Unbounded worker drain")
        return count

    with ThreadPoolExecutor(max_workers=workers) as pool:
        counts = list(pool.map(run, range(workers)))
    return sum(counts)


def expire_capture_lease(repo, worker_id):
    with repo._connection() as connection:
        original = connection.execute(
            "SELECT * FROM counterpart_jobs WHERE worker_id=%s AND status='running'",
            (worker_id,),
        ).fetchone()
        assert original is not None
        connection.execute(
            "UPDATE counterpart_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (original["id"],),
        )
        return original


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 3)


def exercise_product(repo, media, config, directory):
    collections = CollectionRepository(repo)
    campaigns = [
        collections.create_campaign(f"Probe {recipe}", recipe, "replay", target_pairs=4)
        for recipe in ("spatial", "temporal")
    ]
    invites = [collections.create_invite(campaign["id"], max_sessions=4) for campaign in campaigns]

    def join(index):
        invite = invites[index // 4]
        key = f"collector-{index:04d}"
        result = collections.create_session_from_invite(invite["token"], key)
        retry = collections.create_session_from_invite(invite["token"], key)
        assert result == retry
        return result["session"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        sessions = list(pool.map(join, range(8)))
    assert len({session["id"] for session in sessions}) == 8
    evidence = {
        (saved["id"], scenario): frames(media, saved["recipe"], scenario, collector=index)
        for index, saved in enumerate(sessions)
        for scenario in ("baseline", "counterpart", "wrong_relation", "recovery")
    }
    for saved in sessions:
        submit(repo, saved, evidence[saved["id"], "baseline"], "baseline")

    marker = directory / "worker-claimed"
    doomed = child_process(
        directory,
        {
            "dsn": repo.dsn,
            "media": str(media.root),
            "stage": "before-read",
            "marker": str(marker),
        },
    )
    deadline = time.monotonic() + 15
    while not marker.exists() and doomed.poll() is None and time.monotonic() < deadline:
        time.sleep(0.03)
    try:
        assert marker.exists(), "Child Worker did not reach the claimed-media boundary"
        doomed.kill()
        doomed.communicate(timeout=10)
        assert doomed.returncode < 0
    finally:
        if doomed.poll() is None:
            doomed.kill()
            doomed.communicate(timeout=5)
    # Other sessions make progress while the killed worker's lease remains live.
    finished_before_reclaim = drain(repo, media, config)
    assert finished_before_reclaim == 7
    old = expire_capture_lease(repo, "probe-doomed-before-read")
    recovered_start = time.perf_counter()
    assert drain(repo, media, config) == 1
    recovery_ms = (time.perf_counter() - recovered_start) * 1000
    assert not repo.finish(str(old["id"]), str(old["lease_token"]), {"accepted": True}, [])
    sessions = [repo.snapshot(saved["id"]) for saved in sessions]
    assert all(saved["state"] == "baseline_ready" for saved in sessions)

    # Reconnect preserves the baseline but does not assume the physical scene.
    baseline = sessions[0]["baseline"]["id"]
    sessions[0] = repo.resume(sessions[0]["id"], sessions[0]["epoch"])
    assert sessions[0]["baseline"]["id"] == baseline and sessions[0]["recovery_required"]
    try:
        submit(repo, sessions[0], evidence[sessions[0]["id"], "counterpart"], "counterpart")
        raise AssertionError("Reconnect skipped scene recovery")
    except Conflict as error:
        assert error.code == "recovery_required"
    submit(repo, sessions[0], evidence[sessions[0]["id"], "recovery"], "recovery")
    assert drain(repo, media, config) == 1
    sessions[0] = repo.snapshot(sessions[0]["id"])

    submitted_ms = []

    def previews(index):
        count = 64 if index == 0 else 3
        for sequence in range(1, count + 1):
            start = time.perf_counter()
            submit(
                repo,
                sessions[index],
                evidence[sessions[index]["id"], "baseline"][:1],
                "preview",
                sequence,
            )
            submitted_ms.append((time.perf_counter() - start) * 1000)
        return count

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        preview_counts = list(pool.map(previews, range(8)))
    before = repo.metrics()
    assert before["jobs"]["pending"] == 8
    assert drain(repo, media, config) == 8
    preview_wall = time.perf_counter() - start
    for index, saved in enumerate(sessions):
        feedback = repo.snapshot(saved["id"])["feedback"]
        assert feedback and feedback["client_seq"] == preview_counts[index]

    # A late observation from the old epoch cannot bless the reconnected scene.
    saved = repo.snapshot(sessions[0]["id"])
    submit(repo, saved, evidence[sessions[0]["id"], "baseline"][:1], "preview", 65)
    old_preview = repo.claim("stale-preview")
    assert old_preview is not None
    sessions[0] = repo.resume(saved["id"], saved["epoch"])
    assert not repo.finish(
        old_preview["id"], old_preview["lease_token"], {"accepted": True}, old_preview["capture"]["frames"]
    )
    submit(repo, sessions[0], evidence[sessions[0]["id"], "recovery"], "recovery")
    assert drain(repo, media, config) == 1
    sessions = [repo.snapshot(saved["id"]) for saved in sessions]

    submit(repo, sessions[0], evidence[sessions[0]["id"], "wrong_relation"], "counterpart")
    assert drain(repo, media, config) == 1
    sessions[0] = repo.snapshot(sessions[0]["id"])
    assert sessions[0]["latest_capture"]["status"] == "rejected"
    assert sessions[0]["baseline"]["id"] == baseline
    for saved in sessions:
        submit(repo, saved, evidence[saved["id"], "counterpart"], "counterpart")
    assert drain(repo, media, config) == 8
    sessions = [repo.snapshot(saved["id"]) for saved in sessions]
    assert all(saved["state"] == "ready" for saved in sessions)

    def publish_review(index):
        saved = sessions[index]
        pair = repo.publish(saved["id"], saved["revision"])
        duplicate = repo.publish(saved["id"], saved["revision"])
        assert pair["id"] == duplicate["id"]
        key = f"review-collector-{index}"
        kwargs = {
            "decision": "approved",
            "expected_version": 0,
            "reviewer": "probe-fixture-review",
            "reason": "Infrastructure fixture, not a human CV quality verdict",
            "idempotency_key": key,
        }
        review = collections.review_pair(saved["campaign_id"], pair["id"], **kwargs)
        assert review == collections.review_pair(saved["campaign_id"], pair["id"], **kwargs)
        return pair

    with ThreadPoolExecutor(max_workers=8) as pool:
        pairs = list(pool.map(publish_review, range(8)))
    releases = []
    for campaign in campaigns:

        def release(_, campaign_id=campaign["id"]):
            return collections.export_manifests(
                campaign_id, include_replay=True, idempotency_key="probe-reviewed-release"
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            attempts = list(pool.map(release, range(4)))
        assert len({value["id"] for value in attempts}) == 1
        releases.append(attempts[0])
        assert attempts[0]["pair_count"] == 4
        assert sum(attempts[0]["split_counts"].values()) == 4
        assert collections.get_campaign(campaign["id"])["progress"]["approved_pairs"] == 4
        for wrapper in attempts[0]["pairs"]:
            manifest = wrapper["manifest"]
            assert manifest["split_group"] == manifest["session_id"]
            assert manifest["baseline"]["id"] != manifest["counterpart"]["id"]
            assert manifest["counterpart"]["reference_capture_id"] == manifest["baseline"]["id"]
            assert wrapper["review"]["status"] == "approved"
    # Re-review changes future collection progress, never an existing release.
    collections.review_pair(
        campaigns[0]["id"],
        pairs[0]["id"],
        decision="needs_revision",
        expected_version=1,
        reviewer="probe-reviewer",
        reason="Deliberate review revision fault",
        idempotency_key="probe-review-revision",
    )
    assert collections.get_release_by_id(releases[0]["id"]) == releases[0]
    spare = collections.create_invite(campaigns[0]["id"])
    collections.revoke_invite(campaigns[0]["id"], spare["id"])
    try:
        collections.create_session_from_invite(spare["token"], "revoked-invite-attempt")
        raise AssertionError("Revoked invitation admitted a collector")
    except Conflict as error:
        assert error.code == "invite_inactive"
    assert repo.export_claim("no-provider-side-effects") is None
    return {
        "campaigns": 2,
        "collectors": 8,
        "join_requests_including_retries": 16,
        "sessions_created": 8,
        "actual_worker_process_kills": 1,
        "other_sessions_completed_while_dead_worker_lease_active": finished_before_reclaim,
        "worker_recovery_after_injected_lease_expiry_ms": round(recovery_ms, 3),
        "old_worker_result_rejected": True,
        "baseline_survives_reconnect": True,
        "scene_recovery_required_and_completed": True,
        "stale_epoch_preview_rejected": True,
        "previews_submitted": sum(preview_counts),
        "pending_after_burst": 8,
        "collectors_served_latest_preview": 8,
        "preview_burst_and_drain_seconds": round(preview_wall, 3),
        "submit_ms_p50": percentile(submitted_ms, 0.50),
        "submit_ms_p95": percentile(submitted_ms, 0.95),
        "deliberately_rejected_counterparts": 1,
        "published_pairs": 8,
        "publish_requests_including_retries": 16,
        "review_requests_including_retries": 16,
        "unique_releases": 2,
        "release_requests_including_retries": 8,
        "pairs_per_release": [release["pair_count"] for release in releases],
        "pair_membership_and_splits_intact": True,
        "released_review_snapshot_survives_revision": True,
        "revoked_invite_rejected": True,
        "cloud_export_claimable": False,
    }


def exercise_checkpoint_cache(repo, media, directory):
    generated = generate("temporal", "baseline")
    responses = {hashlib.sha256(frame["bytes"]).hexdigest(): frame for frame in generated}
    received = []
    received_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            digest = hashlib.sha256(payload).hexdigest()
            with received_lock:
                received.append(digest)
            frame = responses[digest]
            data = json.dumps(
                {"width": frame["width"], "height": frame["height"], "detections": frame["detections"]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/fixture-detections"
    config = configure(repo.dsn, media.root, True)
    try:
        # This is a frozen v1 orchestration fixture, not a v2 scene-registration
        # or hosted Roboflow quality test. Pin the actual local adapter before
        # dispatch; never change production contracts to accommodate fixtures.
        contract = build_execution_contract(detector_configuration=fixture_configuration(endpoint))
        with repo._connection() as connection:
            saved = repo._create_session(
                connection,
                "temporal",
                "live",
                "bottle",
                "cup",
                recipe_snapshot=build_recipe_snapshot("temporal", recipe_version="1.0.0"),
                execution_contract=contract,
            )["session"]
        submitted = submit(repo, saved, frames(media, "temporal", "baseline", live=True), "baseline")
        doomed = child_process(
            directory,
            {"dsn": repo.dsn, "media": str(media.root), "stage": "after-checkpoints", "endpoint": endpoint},
        )
        try:
            doomed.communicate(timeout=30)
            if doomed.returncode != 73:
                capture = repo.snapshot(saved["id"])["latest_capture"]
                raise AssertionError(
                    "Checkpoint worker failed before the injected acceptance crash: "
                    f"exit={doomed.returncode}, capture_status={capture['status']}, "
                    f"error_code={capture['error']}"
                )
        finally:
            if doomed.poll() is None:
                doomed.kill()
                doomed.communicate(timeout=5)
        dispatched_before = len(received)
        assert dispatched_before == len(generated)
        with repo._connection() as connection:
            units_before = connection.execute(
                "SELECT reserved_units FROM counterpart_budget_accounts WHERE provider='roboflow'"
            ).fetchone()["reserved_units"]
        assert units_before == dispatched_before
        old = expire_capture_lease(repo, "probe-doomed-after-checkpoints")
        worker = make_worker(repo, media, config, endpoint, worker_id="probe-cache-recovery")
        start = time.perf_counter()
        assert worker.once()
        recovery_ms = (time.perf_counter() - start) * 1000
        after = repo.snapshot(saved["id"])
        assert after["state"] == "baseline_ready"
        assert after["baseline"]["id"] == submitted["capture"]["id"]
        assert len(received) == dispatched_before
        assert [frame["t_ms"] for frame in after["baseline"]["frames"]] == [
            frame["t_ms"] for frame in generated
        ]
        assert all(frame["inference"]["source"] == "cache" for frame in after["baseline"]["frames"])
        with repo._connection() as connection:
            units_after = connection.execute(
                "SELECT reserved_units FROM counterpart_budget_accounts WHERE provider='roboflow'"
            ).fetchone()["reserved_units"]
        assert units_after == units_before
        assert not repo.finish(str(old["id"]), str(old["lease_token"]), {"accepted": True}, [])
        return {
            "provider_kind": "localhost fake HTTP responses from generated fixture declarations",
            "recipe_version": saved["recipe_snapshot"]["recipe_version"],
            "frozen_fixture_contract_verified": saved["execution_contract"] == contract,
            "cloud_calls": 0,
            "local_model_calls": 0,
            "sampled_frames": len(generated),
            "actual_worker_process_exits_before_acceptance": 1,
            "durable_observations_before_process_exit": dispatched_before,
            "additional_http_requests_on_recovery": len(received) - dispatched_before,
            "reservation_units_before": units_before,
            "reservation_units_after": units_after,
            "original_capture_and_timestamps_preserved": True,
            "all_recovered_observations_from_cache": True,
            "recovery_after_injected_lease_expiry_ms": round(recovery_ms, 3),
            "cache_stats": worker.inference.cache_stats(),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def run(dsn: str) -> dict:
    schema = "product_probe_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        postgres = connection.execute("SELECT version()").fetchone()[0]
    repo = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    started = time.perf_counter()
    try:
        repo.initialize()
        with tempfile.TemporaryDirectory(prefix="counterpart-product-probe-") as temporary:
            directory = Path(temporary)
            media = MediaStore(directory / "media")
            config = configure(repo.dsn, media.root)
            product = exercise_product(repo, media, config, directory)
            cache = exercise_checkpoint_cache(repo, media, directory)
            return {
                "recorded_at": datetime.now(UTC).isoformat(),
                "schema_version": "counterpart.product-fault-probe.v1",
                "evidence_kind": "deterministic infrastructure replay, not CV accuracy",
                "environment": {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "postgres": postgres,
                    "logical_cpus": os.cpu_count(),
                },
                "limitations": [
                    "Eight synthetic collectors, not real people; no sustained-capacity claim.",
                    "Generated illustrations and declared detections; no ML model executed.",
                    "Worker crashes are real subprocess deaths; lease expiry is accelerated by test SQL.",
                    "Fake HTTP calls and budget reservations occur only in a temporary test schema.",
                    "No shared database restart, provider requests, private credentials, or billing.",
                ],
                "collection_product": product,
                "durable_inference_recovery": cache,
                "total_wall_seconds": round(time.perf_counter() - started, 3),
            }
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default="postgresql:///counterpart_test")
    parser.add_argument("--out", default="artifacts/product_fault_probe.json")
    parser.add_argument("--child", type=Path)
    arguments = parser.parse_args()
    if arguments.child:
        child(arguments.child)
    else:
        report = run(arguments.dsn)
        output = Path(arguments.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
