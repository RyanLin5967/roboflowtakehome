"""Real PostgreSQL outbox + mocked HTTP fault tests; no cloud calls or CV claims."""

from __future__ import annotations

import copy
import io
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from counterpart.collections import CollectionRepository
from counterpart.config import Settings
from counterpart.destination import DestinationResolver
from counterpart.exporting import ExportWorker, export_split
from counterpart.object_storage import MediaUnavailable
from counterpart.providers import RoboflowExporter
from counterpart.repository import Repository
from counterpart.storage import MediaStore

pytestmark = pytest.mark.postgres


@pytest.fixture
def system(tmp_path, monkeypatch, request):
    dsn = os.getenv("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "test_export_" + uuid4().hex
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    except psycopg.OperationalError:
        pytest.skip("An isolated PostgreSQL test database is required")
    scoped = make_conninfo(dsn, options=f"-c search_path={schema}")
    repo = Repository(scoped)
    repo.initialize()
    collections = CollectionRepository(repo)
    media = MediaStore(tmp_path / "media")
    config = Settings(export_enabled=True, export_project="test-project", credentials_file=tmp_path / "none")
    monkeypatch.setenv("ROBOFLOW_API_KEY", "only-a-test-key")
    monkeypatch.setenv("ROBOFLOW_WORKSPACE", "test-workspace")
    campaign = collections.create_campaign("Export transport fixture", "spatial", "live")
    invitation = collections.create_invite(campaign["id"])
    session = collections.create_session_from_invite(invitation["token"], uuid4().hex)["session"]
    for slot, color in (("baseline", "red"), ("counterpart", "blue")):
        image = io.BytesIO()
        Image.new("RGB", (100, 100), color).save(image, format="PNG")
        frame = {**media.put(image.getvalue()), "t_ms": 0}
        metadata = {
            "slot": slot,
            "idempotency_key": uuid4().hex,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "client_seq": 0,
        }
        repo.submit(session["id"], metadata, [frame], uuid4().hex)
        job = repo.claim("fixture-construction-no-inference")
        frame.update(
            {
                "detections": [{"label": "cup", "confidence": 0.9, "box": [0.1, 0.2, 0.3, 0.4]}],
                "model": {"provider": "roboflow", "model_id": "coco/39"},
            }
        )
        result = {
            "accepted": True,
            "code": "test-only",
            "message": "Synthetic export fixture",
            "checks": [],
            "evidence": {},
            "verifier_version": "test-only",
        }
        repo.finish(job["id"], job["lease_token"], result, [frame])
        session = repo.snapshot(session["id"])
    pair = repo.publish(session["id"], session["revision"])
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reviewer="test-fixture-reviewer",
        idempotency_key=uuid4().hex,
    )
    destination = {"workspace": "test-workspace", "project": "test-project", "visibility": "private"}
    if getattr(request, "param", True):
        collections.request_export(pair["id"], destination, "test-fixture-operator")
    calls, uploaded = [], {}
    rig = SimpleNamespace(
        repo=repo,
        media=media,
        config=config,
        pair=pair,
        session=session,
        calls=calls,
        uploaded=uploaded,
        collections=collections,
        campaign=campaign,
        destination=destination,
        metadata_calls=[],
    )

    def successful(request):
        if request.url.path.endswith("/upload"):
            asset_id = request.url.params["tag"].removeprefix("cp-asset-")
            image_id = f"image-{len(uploaded)}"
            uploaded[asset_id] = image_id
            return httpx.Response(200, json={"success": True, "id": image_id})
        if request.url.path.endswith("/search"):
            asset_id = json.loads(request.content)["tag"].removeprefix("cp-asset-")
            results = []
            if asset_id in uploaded:
                results = [
                    {
                        "id": uploaded[asset_id],
                        "split": export_split(session["id"]),
                        "user_metadata": {
                            "counterpart_asset_id": asset_id,
                            "counterpart_media_sha256": asset_id,
                            "counterpart_pair_id": pair["id"],
                            "counterpart_session_id": session["id"],
                        },
                    }
                ]
            return httpx.Response(200, json={"results": results})
        assert "/annotate/" in request.url.path
        assert request.url.params["prediction"] == "true"
        assert request.url.params["overwrite"] == "false"
        assert request.url.params["addToDataset"] == "false"
        return httpx.Response(200, json={"success": True})

    rig.handler = successful
    rig.successful = successful

    def transport(request):
        calls.append(request)
        return rig.handler(request)

    rig.metadata_handler = lambda _: httpx.Response(
        200, json={"project": {"id": "test-workspace/test-project", "public": False}}
    )

    def metadata_transport(request):
        rig.metadata_calls.append(request)
        return rig.metadata_handler(request)

    with (
        httpx.Client(transport=httpx.MockTransport(transport)) as client,
        httpx.Client(transport=httpx.MockTransport(metadata_transport)) as metadata_client,
    ):
        with RoboflowExporter("only-a-test-key", "test-workspace", "test-project", client=client) as adapter:
            rig.adapter = adapter
            rig.resolver = DestinationResolver(config, client=metadata_client)
            rig.worker = ExportWorker(repo, media, config, adapter, destination_resolver=rig.resolver)
            yield rig
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def intent(system):
    with psycopg.connect(system.repo.dsn, row_factory=psycopg.rows.dict_row) as connection:
        return connection.execute("SELECT * FROM counterpart_export_intents").fetchone()


def expire(system):
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_export_intents SET lease_expires_at=clock_timestamp()-interval '1s'"
        )


def make_available(system):
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_export_intents SET available_at=clock_timestamp()-interval '1s'"
        )


def run_all(system, limit=12):
    for _ in range(limit):
        count = len(system.calls)
        worked = system.worker.once()
        assert len(system.calls) - count <= 1, "Exports must yield between HTTP operations"
        if not worked:
            return
    pytest.fail("Export failed to settle in bounded iterations")


def test_success_records_both_image_and_prediction_before_remote_completion(system):
    assert system.worker.once()
    assert intent(system)["status"] == "pending"
    assert system.repo.snapshot(system.session["id"])["state"] == "published"
    assert system.repo.snapshot(system.session["id"])["pair"]["export_status"] == "pending"
    run_all(system)
    row = intent(system)
    assert row["status"] == "complete"
    assert len(row["receipts"]) == 2
    assert len(system.calls) == 4
    for asset_id, receipt in row["receipts"].items():
        assert receipt["state"] == "confirmed"
        assert receipt["media_sha256"] == asset_id
        assert receipt["annotation_status"] == "confirmed"
        assert receipt["is_prediction"] is True
        assert receipt["annotation_sha256"] == receipt["expected_annotation_sha256"]
        assert receipt["split"] == export_split(system.session["id"])
        assert receipt["destination"]["project"] == "test-project"
    uploads = [r for r in system.calls if r.url.path.endswith("/upload")]
    for request in uploads:
        asset_id = request.url.params["tag"].removeprefix("cp-asset-")
        assert f"counterpart-{asset_id}.png".encode() in request.content
    assert system.repo.snapshot(system.session["id"])["pair"]["manifest"] == system.pair["manifest"]


@pytest.mark.parametrize("disabled", ["enabled", "project", "adapter", "mismatched_adapter", "resolver"])
def test_disabled_or_unconfigured_export_does_not_even_claim(system, monkeypatch, disabled):
    if disabled == "enabled":
        system.config.export_enabled = False
    elif disabled == "project":
        system.config.export_project = ""
    elif disabled == "adapter":
        system.worker.exporter = None
    elif disabled == "resolver":
        system.worker.destination_resolver = None
    else:
        system.adapter.project = "another-project"
    monkeypatch.setattr(system.repo, "export_claim", lambda _: pytest.fail("No claim permitted"))
    assert not system.worker.once()
    assert system.calls == []


def test_upload_response_lost_reconciles_without_upload_retry(system):
    def handler(request):
        response = system.successful(request)
        if len(system.calls) == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        return response

    system.handler = handler
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert [r.url.path.rsplit("/", 1)[-1] for r in system.calls[:3]] == ["upload", "search", "image-0"]
    assert sum(r.url.path.endswith("/upload") for r in system.calls) == 2


def test_upload_timeout_no_search_match_retries_reads_with_bounded_backoff(system):
    def handler(request):
        if request.url.path.endswith("/upload"):
            raise httpx.ReadTimeout("unknown result", request=request)
        return system.successful(request)

    system.handler = handler
    assert system.worker.once()  # Unknown upload; only reads may follow.
    for attempt, delay in enumerate((2, 10, 30, 120), start=1):
        assert system.worker.once()
        row = intent(system)
        assert row["status"] == "pending"
        assert list(row["receipts"].values())[0]["reconciliation_attempts"] == attempt
        with psycopg.connect(system.repo.dsn) as connection:
            remaining = connection.execute(
                "SELECT extract(epoch FROM available_at-clock_timestamp()) FROM counterpart_export_intents"
            ).fetchone()[0]
        assert delay - 1 < remaining <= delay
        calls = len(system.calls)
        assert not system.worker.once(), "A delayed read must not monopolize or hot-loop the worker"
        assert len(system.calls) == calls
        make_available(system)
    assert system.worker.once()  # Fifth missing result exhausts the four scheduled delays.
    assert intent(system)["status"] == "uncertain"
    assert len(system.calls) == 6
    assert sum(r.url.path.endswith("/upload") for r in system.calls) == 1
    assert not system.worker.once()


def test_eventually_visible_upload_continues_without_reupload(system):
    searches = 0

    def handler(request):
        nonlocal searches
        response = system.successful(request)
        if len(system.calls) == 1:
            raise httpx.ReadTimeout("upload succeeded but acknowledgment was lost", request=request)
        if request.url.path.endswith("/search"):
            searches += 1
            if searches == 1:
                return httpx.Response(200, json={"results": []})
        return response

    system.handler = handler
    assert system.worker.once()
    assert system.worker.once()
    assert intent(system)["status"] == "pending"
    assert not system.worker.once()
    make_available(system)
    run_all(system)
    assert searches == 2
    assert intent(system)["status"] == "complete"
    assert sum(r.url.path.endswith("/upload") for r in system.calls) == 2
    receipt = next(iter(intent(system)["receipts"].values()))
    assert receipt["reconciliation_attempts"] == 2
    assert "error" not in receipt


def test_crash_after_upload_before_receipt_reconciles(system, monkeypatch):
    original = system.repo.export_record_asset

    def crash(pair_id, lease, asset_id, receipt):
        if receipt["state"] == "uploaded":
            raise psycopg.OperationalError("database disconnected after provider success")
        return original(pair_id, lease, asset_id, receipt)

    monkeypatch.setattr(system.repo, "export_record_asset", crash)
    assert system.worker.once()
    assert intent(system)["status"] == "running"
    assert list(intent(system)["receipts"].values())[0]["state"] == "uploading"
    monkeypatch.setattr(system.repo, "export_record_asset", original)
    expire(system)
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert sum(r.url.path.endswith("/upload") for r in system.calls) == 2
    assert system.calls[1].url.path.endswith("/search")


def test_duplicate_upload_receipt_requires_metadata_reconciliation(system):
    def handler(request):
        response = system.successful(request)
        if len(system.calls) == 1:
            return httpx.Response(200, json={"duplicate": True, "id": "image-0"})
        return response

    system.handler = handler
    assert system.worker.once()
    receipt = list(intent(system)["receipts"].values())[0]
    assert receipt["state"] == "uploading"
    assert receipt["metadata_confirmed"] is False
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert system.calls[1].url.path.endswith("/search")


@pytest.mark.parametrize("field", ["pair", "session", "split"])
def test_duplicate_owned_by_another_pair_or_split_stays_uncertain(system, field):
    def handler(request):
        response = system.successful(request)
        if len(system.calls) == 1:
            return httpx.Response(200, json={"duplicate": True, "id": "image-0"})
        if request.url.path.endswith("/search"):
            body = response.json()
            if field == "split":
                body["results"][0]["split"] = "other-split"
            else:
                body["results"][0]["user_metadata"][f"counterpart_{field}_id"] = "other-owner"
            return httpx.Response(200, json=body)
        return response

    system.handler = handler
    run_all(system)
    assert intent(system)["status"] == "uncertain"
    assert len(system.calls) == 2


def test_annotation_timeout_never_retries_write_or_claims_complete(system):
    def handler(request):
        if "/annotate/" in request.url.path:
            raise httpx.ReadTimeout("response lost", request=request)
        return system.successful(request)

    system.handler = handler
    run_all(system)
    row = intent(system)
    assert row["status"] == "uncertain"
    assert list(row["receipts"].values())[0]["state"] == "uncertain"
    assert len(system.calls) == 2


def test_crash_after_annotation_ack_requires_manual_reconciliation(system, monkeypatch):
    assert system.worker.once()  # confirmed image receipt
    original = system.repo.export_record_asset

    def crash(pair_id, lease, asset_id, receipt):
        if receipt["state"] == "confirmed":
            raise psycopg.OperationalError("lost database")
        return original(pair_id, lease, asset_id, receipt)

    monkeypatch.setattr(system.repo, "export_record_asset", crash)
    assert system.worker.once()
    assert list(intent(system)["receipts"].values())[0]["state"] == "annotating"
    monkeypatch.setattr(system.repo, "export_record_asset", original)
    expire(system)
    run_all(system)
    assert intent(system)["status"] == "uncertain"
    assert len(system.calls) == 2


def test_db_failure_before_write_intent_does_not_dispatch(system, monkeypatch):
    original = system.repo.export_record_asset

    def unavailable(*_):
        raise psycopg.OperationalError("not connected")

    monkeypatch.setattr(system.repo, "export_record_asset", unavailable)
    assert system.worker.once()
    assert not system.calls
    assert intent(system)["receipts"] == {}
    monkeypatch.setattr(system.repo, "export_record_asset", original)
    expire(system)
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert len(system.calls) == 4


def test_crash_after_confirmed_receipt_does_not_repeat_annotation(system, monkeypatch):
    assert system.worker.once()
    original = system.repo.export_finish

    def unavailable(*_, **__):
        raise psycopg.OperationalError("not connected")

    monkeypatch.setattr(system.repo, "export_finish", unavailable)
    assert system.worker.once()
    assert list(intent(system)["receipts"].values())[0]["state"] == "confirmed"
    monkeypatch.setattr(system.repo, "export_finish", original)
    expire(system)
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert len(system.calls) == 4


@pytest.mark.parametrize("point", ["record", "heartbeat"])
def test_fenced_intent_or_lease_loss_cannot_dispatch(system, monkeypatch, point):
    method = "export_record_asset" if point == "record" else "export_heartbeat"
    monkeypatch.setattr(system.repo, method, lambda *_: False)
    assert system.worker.once()
    assert not system.calls
    assert intent(system)["status"] == "running"


def test_worker_losing_lease_during_upload_cannot_record_or_complete(system):
    def handler(request):
        response = system.successful(request)
        if len(system.calls) == 1:
            expire(system)
            assert system.repo.export_claim("replacement")
        return response

    system.handler = handler
    assert system.worker.once()
    row = intent(system)
    assert row["worker_id"] == "replacement"
    assert list(row["receipts"].values())[0]["state"] == "uploading"
    expire(system)
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert sum(r.url.path.endswith("/upload") for r in system.calls) == 2


def test_partial_export_cannot_switch_destination(system):
    assert system.worker.once()
    system.adapter.project = "new-project"
    system.config.export_project = "new-project"
    assert system.worker.once()
    assert intent(system)["status"] == "failed"
    assert intent(system)["last_error"] == "export_destination_mismatch"
    assert len(system.calls) == 1


@pytest.mark.parametrize(
    "corruption", ["fixture", "model", "unaccepted", "missing_predictions", "malformed_model"]
)
def test_invalid_or_fixture_evidence_cannot_reach_provider(system, corruption):
    manifest = copy.deepcopy(system.pair["manifest"])
    if corruption == "fixture":
        manifest["provenance"]["evidence_kind"] = "fixture_replay"
    elif corruption == "model":
        manifest["baseline"]["frames"][0]["model"]["provider"] = "fixture"
    elif corruption == "unaccepted":
        manifest["counterpart"]["result"]["accepted"] = False
    elif corruption == "malformed_model":
        manifest["baseline"]["frames"][0]["model"] = None
    else:
        manifest["counterpart"]["frames"][0].pop("detections")
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute("UPDATE counterpart_pairs SET manifest=%s", (Jsonb(manifest),))
    assert system.worker.once()
    assert intent(system)["status"] == "failed"
    assert not system.calls


def test_corrupted_media_never_uploads(system):
    asset_id = system.pair["manifest"]["baseline"]["frames"][0]["asset_id"]
    system.media.path(asset_id).write_bytes(b"corrupted")
    assert system.worker.once()
    assert intent(system)["status"] == "failed"
    assert intent(system)["last_error"] == "invalid_export_media"
    assert not system.calls


def test_repeated_asset_shared_across_frames_has_one_external_image(system):
    manifest = copy.deepcopy(system.pair["manifest"])
    frame = manifest["baseline"]["frames"][0]
    manifest["counterpart"]["frames"] = [{**frame, "t_ms": 50}, {**frame, "t_ms": 100}]
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute("UPDATE counterpart_pairs SET manifest=%s", (Jsonb(manifest),))
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert len(intent(system)["receipts"]) == 1
    assert len(system.calls) == 2


def test_same_asset_with_conflicting_observations_fails_before_any_upload(system):
    manifest = copy.deepcopy(system.pair["manifest"])
    manifest["counterpart"]["frames"] = copy.deepcopy(manifest["baseline"]["frames"])
    manifest["counterpart"]["frames"][0]["detections"][0]["label"] = "bottle"
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute("UPDATE counterpart_pairs SET manifest=%s", (Jsonb(manifest),))
    assert system.worker.once()
    assert intent(system)["status"] == "failed"
    assert intent(system)["last_error"] == "conflicting_asset_observations"
    assert not system.calls


@pytest.mark.parametrize("status", [401, 402, 429])
def test_definite_provider_rejection_is_not_silently_retried(system, status):
    system.handler = lambda _: httpx.Response(status, json={"error": "not permitted"})
    run_all(system)
    assert intent(system)["status"] == "failed"
    assert len(system.calls) == 1


@pytest.mark.parametrize("system", [False], indirect=True)
def test_enabling_adapter_never_sweeps_historical_published_pairs(system):
    assert intent(system)["status"] == "disabled"
    assert intent(system)["destination"] is None
    assert system.repo.snapshot(system.session["id"])["pair"]["export_status"] == "local_only"
    assert not system.worker.once()
    assert not system.calls
    assert not system.metadata_calls
    system.collections.request_export(system.pair["id"], system.destination, "explicit-test-operator")
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert len(system.calls) == 4


@pytest.mark.parametrize("field", ["workspace", "project", "visibility"])
def test_destination_mismatch_fails_before_any_provider_request(system, field):
    destination = {**system.destination, field: "different"}
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute("UPDATE counterpart_export_intents SET destination=%s", (Jsonb(destination),))
    assert system.worker.once()
    assert intent(system)["last_error"] == "export_destination_mismatch"
    assert not system.calls
    assert not system.metadata_calls


def test_new_upload_rechecks_project_privacy_after_previous_success(system):
    assert system.worker.once()  # first image
    assert system.worker.once()  # first annotation uses bounded cached privacy
    assert len(system.metadata_calls) == 1
    system.metadata_handler = lambda _: httpx.Response(
        200, json={"project": {"id": "test-workspace/test-project", "public": True}}
    )
    assert system.worker.once()  # force check blocks next image
    assert intent(system)["status"] == "failed"
    assert intent(system)["last_error"] == "private_destination_required"
    assert len(system.calls) == 2
    assert len(system.metadata_calls) == 2


def test_metadata_outage_preserves_intent_without_upload_or_hot_loop(system):
    system.metadata_handler = lambda _: httpx.Response(503)
    assert system.worker.once()
    assert intent(system)["status"] == "pending"
    assert intent(system)["last_error"] == "destination_unavailable"
    assert intent(system)["receipts"] == {}
    assert not system.worker.once()
    assert len(system.metadata_calls) == 1
    assert not system.calls
    system.metadata_handler = lambda _: httpx.Response(
        200, json={"project": {"id": "test-workspace/test-project", "public": False}}
    )
    make_available(system)
    run_all(system)
    assert intent(system)["status"] == "complete"


def test_transient_shared_storage_failure_retries_saved_bytes_without_write_intent(system, monkeypatch):
    original = system.media.read

    def unavailable(_):
        raise MediaUnavailable("object_storage_unavailable")

    monkeypatch.setattr(system.media, "read", unavailable)
    assert system.worker.once()
    assert intent(system)["status"] == "pending"
    assert intent(system)["last_error"] == "media_unavailable"
    assert intent(system)["receipts"] == {}
    assert not system.worker.once()
    assert not system.calls
    assert not system.metadata_calls
    monkeypatch.setattr(system.media, "read", original)
    make_available(system)
    run_all(system)
    assert intent(system)["status"] == "complete"
