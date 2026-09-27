"""Durable reviewed-baseline collection; generated images and fake hosted HTTP."""

from __future__ import annotations

import base64
import hashlib
import io
import os
from uuid import uuid4

import httpx
import psycopg
import pytest
from PIL import Image, ImageDraw
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.config import Settings
from counterpart.evidence_revisions import BASELINE_CRITERIA, EvidenceRepository, capture_digest
from counterpart.providers import RoboflowDetector
from counterpart.repository import Conflict, Repository
from counterpart.storage import MediaStore
from counterpart.worker import Worker

pytestmark = pytest.mark.postgres


@pytest.fixture
def system(tmp_path):
    dsn = os.environ.get("COUNTERPART_TEST_DSN")
    if not dsn:
        pytest.skip("COUNTERPART_TEST_DSN required")
    namespace = "cp_reviewed_baseline_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace)))
    repo = Repository(make_conninfo(dsn, options=f"-c search_path={namespace}"))
    repo.initialize()
    media = MediaStore(tmp_path / "media")
    config = Settings(database_url=repo.dsn, media_dir=media.root, live_enabled=True, inference_limit=10)
    known = {}

    def make_frame(right=False):
        image = Image.new("RGB", (640, 480), "#d9dadb")
        draw = ImageDraw.Draw(image)
        boxes = {
            "bottle": [0.74, 0.4, 0.84, 0.68] if right else [0.14, 0.4, 0.24, 0.68],
            "cup": [0.44, 0.4, 0.54, 0.68],
        }
        predictions = []
        for label, (left, top, right, bottom) in boxes.items():
            draw.rectangle(
                (round(left * 640), round(top * 480), round(right * 640), round(bottom * 480)),
                fill="#208040" if label == "bottle" else "#df8040",
            )
            predictions.append(
                {
                    "class": label,
                    "confidence": 0.95,
                    "x": (left + right) / 2 * 640,
                    "y": (top + bottom) / 2 * 480,
                    "width": (right - left) * 640,
                    "height": (bottom - top) * 480,
                }
            )
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        data = buffer.getvalue()
        known[hashlib.sha256(data).hexdigest()] = predictions
        return {**media.put(data), "t_ms": 0}

    def respond(request):
        raw = base64.b64decode(request.content)
        return httpx.Response(
            200,
            json={
                "image": {"width": 640, "height": 480},
                "model_id": "coco/39",
                "predictions": known[hashlib.sha256(raw).hexdigest()],
            },
        )

    detector = RoboflowDetector(
        "test-not-a-provider-key", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    try:
        yield repo, EvidenceRepository(repo, media), Worker(repo, media, config, detector), make_frame
    finally:
        detector.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(namespace)))


def submit(repo, session, source, slot):
    return repo.submit(
        session["id"],
        {
            "slot": slot,
            "idempotency_key": uuid4().hex,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "client_seq": 0,
        },
        [source],
        uuid4().hex,
    )


def authorized(system):
    repo, evidence, worker, frame = system
    session = repo.create_session("spatial", "live", "bottle", "cup")["session"]
    submit(repo, session, frame(), "baseline")
    assert worker.once()
    session = repo.snapshot(session["id"])
    original = session["latest_capture"]
    assert original["result"]["outcome"] == "unknown" and original["result"]["accepted"] is False
    record = evidence.authorize_baseline(
        session["id"],
        original["id"],
        expected_session_revision=session["revision"],
        expected_evidence_sha256=capture_digest(original, session["recipe_snapshot"]),
        answer=session["recipe_snapshot"]["expected_answers"]["baseline"],
        criteria=dict.fromkeys(BASELINE_CRITERIA, True),
        reason="Independent review of the generated flat background and named object controls.",
        idempotency_key=uuid4().hex,
        reviewer="test-reviewer",
    )
    saved = repo.snapshot(session["id"])
    assert saved["baseline"] == original
    assert saved["baseline_authorization"] == record and saved["human_review_required"] is True
    return saved, original, record


def test_reviewed_baseline_collects_counterpart_after_resume_without_faking_recovery(system):
    repo, _, worker, frame = system
    session, original, record = authorized(system)
    resumed = repo.resume(session["id"], session["epoch"])
    assert resumed["recovery_required"]
    submitted = submit(repo, resumed, frame(True), "counterpart")
    capture = submitted["capture"]
    assert capture["baseline_authorization"] == record
    assert capture["human_review_required"] and capture["recovery_unresolved"]
    assert capture["epoch"] == resumed["epoch"]
    assert worker.once()
    final = repo.snapshot(session["id"])
    assert final["baseline"] == original
    assert final["recovery_required"] and final["human_review_required"]
    result = final["latest_capture"]["result"]
    assert result["human_review_required"] and result["baseline_authorization_id"] == record["id"]
    with pytest.raises(Conflict) as caught:
        repo.publish(session["id"], final["revision"])
    assert caught.value.code == "human_review_required"


def test_even_successful_machine_counterpart_cannot_clear_reviewed_recovery_or_publish(system):
    repo, _, _, frame = system
    session, original, _ = authorized(system)
    resumed = repo.resume(session["id"], session["epoch"])
    submit(repo, resumed, frame(True), "counterpart")
    job = repo.claim("synthetic-state-transition")
    # This injected verifier response is only a state-machine adversary: it must
    # not make operator-authorized evidence become an ordinary machine pair.
    assert repo.finish(
        job["id"],
        job["lease_token"],
        {"accepted": True, "checks": [], "evidence": {}},
        job["capture"]["frames"],
    )
    final = repo.snapshot(session["id"])
    assert final["state"] == "ready" and final["recovery_required"]
    assert final["baseline"] == original and final["human_review_required"]
    with pytest.raises(Conflict) as caught:
        repo.publish(session["id"], final["revision"])
    assert caught.value.code == "human_review_required"


def test_replacement_baseline_clears_active_authority_but_not_original_receipts(system):
    repo, _, _, frame = system
    session, _, record = authorized(system)
    counterpart = submit(repo, session, frame(True), "counterpart")["capture"]
    active = repo.snapshot(session["id"])
    replacement = submit(repo, active, frame(), "baseline")["session"]
    assert replacement["baseline"] is None and replacement["baseline_authorization"] is None
    assert replacement["human_review_required"] is False
    with repo._connection() as connection:
        prior = repo._capture(connection, counterpart["id"])
        auth = connection.execute(
            "SELECT record FROM counterpart_baseline_authorizations WHERE id=%s", (record["id"],)
        ).fetchone()["record"]
    assert prior["baseline_authorization"] == auth == record
    assert prior["human_review_required"] and prior["status"] == "superseded"


def test_retry_retains_original_authority_epoch_and_unresolved_recovery(system):
    repo, _, _, frame = system
    session, _, record = authorized(system)
    resumed = repo.resume(session["id"], session["epoch"])
    first = submit(repo, resumed, frame(True), "counterpart")["capture"]
    job = repo.claim("fake-network-failure")
    repo.fail(job["id"], job["lease_token"], "provider_unavailable")
    current = repo.snapshot(session["id"])
    later = repo.resume(current["id"], current["epoch"])
    retried = repo.retry_capture(session["id"], first["id"], later["revision"], later["epoch"], uuid4().hex)
    assert retried["capture"]["epoch"] == first["epoch"]
    assert retried["capture"]["baseline_authorization"] == record
    assert retried["capture"]["recovery_unresolved"] is True
    assert retried["capture"]["retry_of_capture_id"] == first["id"]
