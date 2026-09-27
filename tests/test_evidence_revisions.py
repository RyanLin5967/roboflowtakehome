"""Original media, real PostgreSQL, append-only decisions; no model or cloud calls."""

from __future__ import annotations

import asyncio
import copy
import io
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.api import create_app
from counterpart.archives import archive_response
from counterpart.collections import CollectionRepository
from counterpart.config import Settings
from counterpart.consumer import PortableRelease
from counterpart.evidence_revisions import CRITERIA, EvidenceRepository
from counterpart.fixtures import generate
from counterpart.repository import Conflict, NotFound, Repository
from counterpart.storage import MediaStore
from counterpart.worker import Worker

pytestmark = pytest.mark.postgres


@pytest.fixture
def system(tmp_path):
    dsn = os.environ.get("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "evidence_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(dsn, options=f"-c search_path={schema}")
    repo = Repository(scoped)
    repo.initialize()
    media = MediaStore(tmp_path / "media")
    config = Settings(database_url=scoped, media_dir=tmp_path / "media", live_enabled=False)
    try:
        yield repo, media, config, CollectionRepository(repo), EvidenceRepository(repo, media)
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def start(system, recipe="spatial", group=False):
    _, _, _, collections, _ = system
    campaign = collections.create_campaign("Review uncertain attempts", recipe, mode="replay")
    kwargs = {}
    if group:
        kwargs["split_group_id"] = collections.create_split_group("Same desk scene")["id"]
    invite = collections.create_invite(campaign["id"], **kwargs)
    return campaign, collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]


def submit(system, session, slot, *, scenario=None, low_confidence=False):
    repo, media, config, _, _ = system
    frames = []
    for frame in generate(session["recipe"], scenario or slot):
        detections = copy.deepcopy(frame["detections"])
        if low_confidence:
            for detection in detections:
                if detection["label"] == "bottle":
                    detection["confidence"] = 0.50
        frames.append({**media.put(frame["bytes"]), "t_ms": frame["t_ms"], "fixture_detections": detections})
    key = uuid4().hex
    repo.submit(
        session["id"],
        {
            "slot": slot,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "idempotency_key": key,
            "client_seq": 0,
        },
        frames,
        key,
    )
    worker = Worker(repo, media, config)
    assert worker.once()
    return repo.snapshot(session["id"])


def candidate(system, *, recipe="spatial", group=False, both_unknown=False):
    campaign, session = start(system, recipe, group)
    session = submit(system, session, "baseline", low_confidence=both_unknown)
    first = session["latest_capture"]["id"]
    session = submit(
        system,
        session,
        "baseline" if both_unknown else "counterpart",
        scenario="counterpart",
        low_confidence=True,
    )
    last = session["latest_capture"]["id"]
    detail = system[4].detail(session["id"], first, last)
    body = {
        "baseline_id": first,
        "counterpart_id": last,
        "expected_session_revision": session["revision"],
        "expected_evidence_sha256": detail["selected_pair"]["evidence_sha256"],
        "labels": detail["recipe_snapshot"]["expected_answers"],
        "criteria": dict.fromkeys(CRITERIA, True),
        "reason": "Inspected every original frame and named control; the detector confidence is low.",
        "idempotency_key": uuid4().hex,
    }
    return campaign, session, detail, body


def archive(manifest, media):
    response = archive_response(manifest, media, "evidence.zip")

    async def read():
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(read())


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
@pytest.mark.parametrize("both_unknown", [False, True])
def test_human_uncertainty_resolution_retains_originals_and_requires_separate_release_review(
    system, recipe, both_unknown
):
    repo, media, _, collections, evidence = system
    campaign, session, detail, body = candidate(system, recipe=recipe, both_unknown=both_unknown)
    before = repo.evaluation_snapshot(session["id"])["captures"]
    pair = evidence.adjudicate(session["id"], **body)
    assert pair["manifest"]["schema_version"] == "counterpart.pair.v2"
    assert pair["manifest"]["counterpart"]["result"]["accepted"] is False
    assert repo.evaluation_snapshot(session["id"])["captures"] == before
    assert evidence.adjudicate(session["id"], **body) == pair
    assert collections.list_pairs(campaign["id"])[0]["review"]["status"] == "pending"
    with pytest.raises(Conflict):
        collections.export_manifests(
            campaign["id"], pair_ids=[pair["id"]], include_replay=True, idempotency_key=uuid4().hex
        )
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reason="Separate review of human-origin labels and original frames",
        idempotency_key=uuid4().hex,
        reviewer="second human",
    )
    release = collections.export_manifests(
        campaign["id"], pair_ids=[pair["id"]], include_replay=True, idempotency_key=uuid4().hex
    )
    with PortableRelease(io.BytesIO(archive(release, media)), allow_replay=True) as consumer:
        rows = list(consumer.iter_examples())
        assert len(rows) == 2
        assert all(row["label_origin"] == "human_adjudication" for row in rows)
        assert rows[0]["answer"] != rows[1]["answer"]
        assert (
            rows[0]["adjudication"]["record_sha256"]
            == pair["manifest"]["evidence_adjudication"]["record_sha256"]
        )
    assert evidence.detail(session["id"])["adjudications"][0] == pair["manifest"]["evidence_adjudication"]


def test_human_pairs_keep_operator_scene_groups(system):
    _, media, _, _, evidence = system
    _, session, _, body = candidate(system, group=True)
    pair = evidence.adjudicate(session["id"], **body)["manifest"]
    assert pair["split_group"] != session["id"]
    assert pair["split_provenance"]["kind"] == "operator_group"
    with PortableRelease(io.BytesIO(archive(pair, media)), allow_replay=True) as consumer:
        assert list(consumer.iter_examples())[0]["split_group"] == pair["split_group"]


@pytest.mark.parametrize("scenario", ["wrong_relation", "camera_moved"])
def test_known_false_controls_cannot_be_adjudicated(system, scenario):
    repo, _, _, _, evidence = system
    _, session = start(system)
    session = submit(system, session, "baseline")
    baseline_id = session["baseline"]["id"]
    session = submit(system, session, "counterpart", scenario=scenario)
    detail = evidence.detail(session["id"])
    assert detail["captures"][-1]["review_eligibility"]["allowed"] is False
    with pytest.raises(Conflict) as refused:
        evidence.adjudicate(
            session["id"],
            baseline_id=baseline_id,
            counterpart_id=session["latest_capture"]["id"],
            expected_session_revision=session["revision"],
            expected_evidence_sha256=detail["selected_pair"]["evidence_sha256"],
            labels={"baseline": "left", "counterpart": "right"},
            criteria=dict.fromkeys(CRITERIA, True),
            reason="A long rationale cannot waive a recorded false condition.",
            idempotency_key=uuid4().hex,
        )
    assert refused.value.code == "known_control_failure"
    assert repo.snapshot(session["id"])["pair"] is None


@pytest.mark.parametrize("mutation", ["labels", "criteria", "reason", "hash", "revision"])
def test_adjudication_refuses_stale_or_incomplete_decisions(system, mutation):
    _, _, _, _, evidence = system
    _, session, _, body = candidate(system)
    if mutation == "labels":
        body["labels"] = {"baseline": "right", "counterpart": "left"}
    elif mutation == "criteria":
        body["criteria"].pop("controls_confirmed")
    elif mutation == "reason":
        body["reason"] = "Okay"
    elif mutation == "hash":
        body["expected_evidence_sha256"] = "0" * 64
    else:
        body["expected_session_revision"] -= 1
    with pytest.raises(Conflict):
        evidence.adjudicate(session["id"], **body)


def test_foreign_capture_is_not_exposed_or_adjudicated(system):
    _, session, _, body = candidate(system)
    _, other, _, other_body = candidate(system)
    body["counterpart_id"] = other_body["counterpart_id"]
    with pytest.raises(NotFound):
        system[4].adjudicate(session["id"], **body)
    with pytest.raises(NotFound):
        system[4].detail(session["id"], body["baseline_id"], body["counterpart_id"])


def test_reverify_is_idempotent_append_only_and_never_changes_original_decisions(system):
    repo, _, _, _, evidence = system
    _, session, detail, _ = candidate(system)
    capture = detail["captures"][-1]
    before = repo.evaluation_snapshot(session["id"])
    body = {
        "expected_version": 0,
        "expected_evidence_sha256": capture["evidence_sha256"],
        "recipe_version": "1.0.0",
        "idempotency_key": "one-version-replay",
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: evidence.reverify(session["id"], capture["capture"]["id"], **body), range(8))
        )
    assert len({record["id"] for record in results}) == 1
    assert results[0]["result"] == capture["capture"]["result"]
    assert results[0]["cloud_calls"] == 0 and results[0]["model_rerun"] is False
    assert repo.evaluation_snapshot(session["id"])["captures"] == before["captures"]
    with pytest.raises(Conflict, match="Another verifier revision"):
        evidence.reverify(
            session["id"], capture["capture"]["id"], **{**body, "idempotency_key": "another-version-key"}
        )
    with pytest.raises(Conflict, match="another re-verification"):
        evidence.reverify(session["id"], capture["capture"]["id"], **{**body, "recipe_version": "2.0.0"})
    for command in (
        "UPDATE counterpart_evidence_revisions SET version=99",
        "DELETE FROM counterpart_evidence_revisions",
    ):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            with repo._connection() as connection:
                connection.execute(command)


def test_new_verifier_revision_preserves_original_snapshot_and_has_no_side_effects(system):
    repo, _, _, _, evidence = system
    _, session, detail, _ = candidate(system)
    capture = detail["captures"][-1]
    revision = evidence.reverify(
        session["id"],
        capture["capture"]["id"],
        expected_version=0,
        expected_evidence_sha256=capture["evidence_sha256"],
        recipe_version="2.0.0",
        idempotency_key=uuid4().hex,
    )
    assert revision["recipe_snapshot"]["recipe_version"] == "2.0.0"
    assert revision["original_recipe_snapshot"]["recipe_version"] == "1.0.0"
    assert repo.snapshot(session["id"])["recipe_snapshot"]["recipe_version"] == "1.0.0"
    assert revision["reference_evidence_sha256"]
    assert revision["result"]["accepted"] is False


def test_attempt_review_is_versioned_and_does_not_accept_capture(system):
    repo, _, _, _, evidence = system
    _, session, detail, _ = candidate(system)
    capture = detail["captures"][-1]
    body = {
        "decision": "needs_recapture",
        "expected_version": 0,
        "expected_evidence_sha256": capture["evidence_sha256"],
        "reason": "Cannot determine identity in this image",
        "idempotency_key": uuid4().hex,
    }
    record = evidence.review_attempt(session["id"], capture["capture"]["id"], **body)
    assert evidence.review_attempt(session["id"], capture["capture"]["id"], **body) == record
    assert evidence.detail(session["id"])["captures"][-1]["review_version"] == 1
    assert repo.snapshot(session["id"])["latest_capture"]["status"] == "rejected"
    assert repo.snapshot(session["id"])["pair"] is None
    with pytest.raises(Conflict):
        evidence.review_attempt(
            session["id"], capture["capture"]["id"], **{**body, "idempotency_key": uuid4().hex}
        )


def test_operator_auth_and_typed_revision_routes(system):
    repo, _, config, _, _ = system
    _, session, detail, _ = candidate(system)
    with TestClient(create_app(config, repo)) as client:
        path = f"/api/operator/sessions/{session['id']}/evidence"
        assert client.get(path).status_code == 401
        assert client.post("/api/operator/bootstrap").status_code == 200
        response = client.get(path)
        assert response.status_code == 200 and response.json()["captures"]
        capture = detail["captures"][-1]
        route = f"/api/operator/sessions/{session['id']}/captures/{capture['capture']['id']}/reverify"
        payload = {
            "expected_version": 0,
            "expected_evidence_sha256": capture["evidence_sha256"],
            "recipe_version": "1.0.0",
            "idempotency_key": uuid4().hex,
        }
        assert client.post(route, json=payload).status_code == 201
        assert client.post(route, json={**payload, "detections": []}).status_code == 422
        assert (
            client.post(
                route,
                json={
                    **payload,
                    "expected_version": 1,
                    "recipe_version": "99.0.0",
                    "idempotency_key": uuid4().hex,
                },
            ).status_code
            == 409
        )


def test_authorized_first_unknown_api_to_reviewed_release_consumer(system, monkeypatch):
    """One complete path, including auth HTTP and separate final release approval."""
    from PIL import Image, ImageDraw

    from counterpart.evidence_revisions import BASELINE_CRITERIA

    # Explicit v2 fixture campaign; production replay defaults remain v1. No
    # provider calls or machine-accuracy claim is hidden in this fixture.
    monkeypatch.setattr("counterpart.recipes.version_for_mode", lambda _: "2.0.0")
    repo, media, config, collections, evidence = system
    campaign, session = start(system)

    def collect(current, slot, right=False):
        image = Image.new("RGB", (320, 240), "#dddddd")
        draw = ImageDraw.Draw(image)
        detections = [
            {
                "label": "bottle",
                "confidence": 0.97,
                "box": [0.74, 0.40, 0.84, 0.68] if right else [0.14, 0.40, 0.24, 0.68],
            },
            {"label": "cup", "confidence": 0.97, "box": [0.44, 0.40, 0.54, 0.68]},
        ]
        for detection in detections:
            draw.rectangle(
                tuple(round(v * d) for v, d in zip(detection["box"], (320, 240, 320, 240), strict=True)),
                fill="#208040" if detection["label"] == "bottle" else "#df8040",
            )
        raw = io.BytesIO()
        image.save(raw, "PNG")
        frames = [{**media.put(raw.getvalue()), "t_ms": 0, "fixture_detections": detections}]
        key = uuid4().hex
        repo.submit(
            current["id"],
            {
                "slot": slot,
                "expected_revision": current["revision"],
                "epoch": current["epoch"],
                "idempotency_key": key,
                "client_seq": 0,
            },
            frames,
            key,
        )
        assert Worker(repo, media, config).once()
        return repo.snapshot(current["id"])

    session = collect(session, "baseline")
    detail = evidence.detail(session["id"])
    original = copy.deepcopy(detail["captures"][0]["capture"])
    assert original["result"]["outcome"] == "unknown"
    assert detail["captures"][0]["baseline_authorization_eligibility"]["allowed"] is True
    body = {
        "expected_session_revision": session["revision"],
        "expected_evidence_sha256": detail["captures"][0]["evidence_sha256"],
        "answer": "left",
        "criteria": dict.fromkeys(BASELINE_CRITERIA, True),
        "reason": "Reviewed flat-background uncertainty; the complete original object geometry is valid.",
        "idempotency_key": uuid4().hex,
    }
    with TestClient(create_app(config, repo)) as client:
        route = f"/api/operator/sessions/{session['id']}/captures/{original['id']}/authorize-baseline"
        assert client.post(route, json=body).status_code == 401
        assert client.post("/api/operator/bootstrap").status_code == 200
        response = client.post(route, json=body)
        assert response.status_code == 201, response.json()
        authority = response.json()
        assert client.post(route, json=body).json() == authority
    session = repo.snapshot(session["id"])
    assert session["revision"] == body["expected_session_revision"] + 1
    assert session["baseline"]["result"]["accepted"] is False
    session = collect(session, "counterpart", right=True)
    assert session["latest_capture"]["baseline_authorization"] == authority
    assert session["human_review_required"] is True
    with pytest.raises(Conflict) as ordinary:
        repo.publish(session["id"], session["revision"])
    assert ordinary.value.code == "human_review_required"
    detail = evidence.detail(session["id"])
    counterpart = detail["captures"][-1]
    # Comparing a weaker historical verifier does not reuse the human's v2 authorization.
    revision = evidence.reverify(
        session["id"],
        counterpart["capture"]["id"],
        expected_version=0,
        expected_evidence_sha256=counterpart["evidence_sha256"],
        recipe_version="1.0.0",
        idempotency_key=uuid4().hex,
    )
    assert revision["source_baseline_authorization_id"] == authority["id"]
    assert revision["baseline_authorization_applied"] is False
    pair = evidence.adjudicate(
        session["id"],
        baseline_id=original["id"],
        counterpart_id=counterpart["capture"]["id"],
        expected_session_revision=session["revision"],
        expected_evidence_sha256=detail["selected_pair"]["evidence_sha256"],
        labels={"baseline": "left", "counterpart": "right"},
        criteria=dict.fromkeys(CRITERIA, True),
        reason="Reviewed both original images and every named control; their opposing relations are visible.",
        idempotency_key=uuid4().hex,
    )
    assert pair["manifest"]["baseline"] == original
    assert pair["manifest"]["baseline_authorization"] == authority
    with pytest.raises(Conflict):
        collections.export_manifests(
            campaign["id"], pair_ids=[pair["id"]], include_replay=True, idempotency_key=uuid4().hex
        )
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reviewer="final-reviewer",
        reason="Separate review of explicitly human-derived labels",
        idempotency_key=uuid4().hex,
    )
    release = collections.export_manifests(
        campaign["id"], pair_ids=[pair["id"]], include_replay=True, idempotency_key=uuid4().hex
    )
    with PortableRelease(io.BytesIO(archive(release, media)), allow_replay=True) as consumer:
        assert consumer.summary["release_reviewed"] is True
        assert consumer.summary["human_adjudicated_pairs"] == 1
        assert [row["answer"] for row in consumer.iter_examples()] == ["left", "right"]
        assert consumer.manifest["pairs"][0]["manifest"]["baseline"]["result"]["accepted"] is False


def test_accepted_published_v1_pair_comparison_records_actual_v2_reference_checks_without_rewriting_history(
    system,
):
    repo, media, _, collections, evidence = system
    campaign, session = start(system)
    session = submit(system, session, "baseline")
    session = submit(system, session, "counterpart")
    assert session["baseline"]["result"]["accepted"] is True
    assert session["counterpart"]["result"]["accepted"] is True
    pair = repo.publish(session["id"], session["revision"])
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reviewer="original reviewer",
        reason="Original v1 pair review",
        idempotency_key=uuid4().hex,
    )
    release = collections.export_manifests(
        campaign["id"], pair_ids=[pair["id"]], include_replay=True, idempotency_key=uuid4().hex
    )
    originals = copy.deepcopy(repo.evaluation_snapshot(session["id"])["captures"])
    original_reviews = collections.review_history(campaign["id"], pair["id"])
    original_archive = archive(release, media)
    detail = evidence.detail(session["id"])
    capture = detail["captures"][-1]
    revision = evidence.reverify(
        session["id"],
        capture["capture"]["id"],
        expected_version=0,
        expected_evidence_sha256=capture["evidence_sha256"],
        recipe_version="2.0.0",
        idempotency_key=uuid4().hex,
    )

    reference = revision["reference_result"]
    assert reference["verifier_version"] == "counterpart-verifier/2.0.0"
    assert reference["outcome"] == "unknown"
    assert reference["hard_error"] is False
    registrations = [
        check for check in reference["checks"] if check["name"].startswith("background_registration:")
    ]
    assert registrations and registrations[0]["outcome"] == "unknown"
    measurements = registrations[0]["measurements"]
    baseline_hash = session["baseline"]["frames"][0]["asset_id"]
    assert measurements["reference_sha256"] == measurements["candidate_sha256"] == baseline_hash
    assert isinstance(measurements["reference_features"], int)
    assert reference["evidence"]["visual"]["sources"][0]["sha256"] == baseline_hash
    # The strict candidate result is retained exactly, not replaced with a
    # invented registration result or an implicitly approved reference.
    assert revision["result"]["code"] == "baseline_required"
    assert revision["result"]["accepted"] is False
    assert "visual" not in revision["result"]["evidence"]
    assert revision["comparison"]["status"] == "blocked_reference_unknown"
    assert revision["comparison"]["outcome"] == "unknown"
    assert revision["comparison"]["candidate_checks"] == "dependency_gate_only"
    assert revision["comparison"]["reference_capture_id"] == session["baseline"]["id"]
    assert revision["cloud_calls"] == 0 and revision["model_rerun"] is False
    assert revision["baseline_authorization_applied"] is False

    assert repo.evaluation_snapshot(session["id"])["captures"] == originals
    assert repo.snapshot(session["id"])["pair"] == pair
    assert collections.review_history(campaign["id"], pair["id"]) == original_reviews
    assert collections.get_release(campaign["id"], release["id"]) == release
    # ZIP directory timestamps belong to the download operation; immutable
    # members, rather than a fresh container's timestamps, are the evidence.
    import zipfile

    with (
        zipfile.ZipFile(io.BytesIO(original_archive)) as old,
        zipfile.ZipFile(io.BytesIO(archive(release, media))) as new,
    ):
        assert {name: old.read(name) for name in old.namelist()} == {
            name: new.read(name) for name in new.namelist()
        }
    with repo._connection() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM counterpart_budget_reservations").fetchone()["n"]
            == 0
        )


def test_reference_unknown_summary_does_not_hide_an_independent_authorization_error():
    from counterpart.evidence_revisions import comparison_status

    strict = {
        "accepted": False,
        "outcome": "fail",
        "code": "baseline_required",
        "checks": [
            {"passed": False, "code": "baseline_required"},
            {"passed": False, "code": "baseline_authorization_invalid"},
        ],
    }
    summary = comparison_status(
        {"reference_capture_id": str(uuid4())},
        strict,
        {"accepted": False, "outcome": "unknown", "hard_error": False},
    )
    assert summary["status"] == "evaluated"
    assert summary["outcome"] == "fail"
    assert strict["code"] == "baseline_required"


def test_known_failed_reference_comparison_remains_a_failure():
    from counterpart.evidence_revisions import comparison_status

    strict = {
        "accepted": False,
        "outcome": "fail",
        "code": "baseline_required",
        "checks": [{"passed": False, "code": "baseline_required"}],
    }
    summary = comparison_status(
        {"reference_capture_id": str(uuid4())},
        strict,
        {"accepted": False, "outcome": "fail", "hard_error": False},
    )
    assert summary["status"] == "blocked_reference_failure"
    assert summary["outcome"] == "fail"
    assert summary["candidate_checks"] == "dependency_gate_only"
