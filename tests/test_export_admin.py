"""Recovery against real PostgreSQL and the actual worker; no provider traffic."""

import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_exporting import intent, make_available, run_all
from test_exporting import system as system

from counterpart.export_admin import ExportAdmin
from counterpart.repository import Conflict, NotFound

pytestmark = pytest.mark.postgres


def uncertain_upload(system):
    def unavailable(request):
        if request.url.path.endswith("/upload"):
            raise httpx.ReadTimeout("Uncertain test outcome", request=request)
        return httpx.Response(200, json={"results": []})

    system.handler = unavailable
    for _ in range(6):
        assert system.worker.once()
        make_available(system)
    assert intent(system)["status"] == "uncertain"
    return copy.deepcopy(intent(system)["receipts"])


def test_inspection_preserves_state_and_shows_safe_receipt_fields(system):
    before = uncertain_upload(system)
    row = ExportAdmin(system.repo).status(system.pair["id"])
    assert row["status"] == "uncertain"
    assert row["destination"] == system.destination
    assert row["can_recheck"] is row["safe_recheck"] is True
    assert row["recheck_count"] == 0
    assert row["history"] == []
    assert row["receipts"][0]["state"] == "uncertain"
    assert row["receipts"][0]["reconciliation_attempts"] == 5
    assert intent(system)["receipts"] == before
    assert len(system.calls) == 6


def test_recheck_preserves_old_receipts_and_never_repeats_unknown_upload(system):
    before = uncertain_upload(system)
    admin = ExportAdmin(system.repo)
    queued = admin.recheck(system.pair["id"], "workspace-operator")
    assert queued["status"] == "pending"
    assert not queued["can_recheck"]
    assert queued["recheck_count"] == 1
    current = intent(system)["receipts"]
    assert set(current) == set(before)
    receipt = next(iter(current.values()))
    assert receipt["state"] == "uploading"
    assert receipt["reconciliation_attempts"] == 0
    assert receipt["operator_rechecks"] == 1
    assert len(system.calls) == 6  # administration never calls the provider
    with psycopg.connect(system.repo.dsn) as connection:
        event = connection.execute(
            "SELECT data FROM counterpart_events WHERE kind='export_recheck_requested'"
        ).fetchone()[0]
    assert event["previous_receipts"] == before
    assert event["previous_status"] == "uncertain"
    for _ in range(5):
        assert system.worker.once()
        make_available(system)
    assert intent(system)["status"] == "uncertain"
    assert len(system.calls) == 11
    assert sum(call.url.path.endswith("/upload") for call in system.calls) == 1
    assert all(call.url.path.endswith("/search") for call in system.calls[6:])


def test_eventually_found_upload_resumes_from_recorded_image_not_duplicate(system):
    uncertain_upload(system)
    existing_asset = next(iter(intent(system)["receipts"]))
    system.uploaded[existing_asset] = "previously-uploaded-image"
    system.handler = system.successful
    ExportAdmin(system.repo).recheck(system.pair["id"], "workspace-operator")
    assert system.worker.once()
    assert system.calls[-1].url.path.endswith("/search")
    assert intent(system)["receipts"][existing_asset]["image_id"] == "previously-uploaded-image"
    run_all(system)
    assert intent(system)["status"] == "complete"
    # One old ambiguous upload plus exactly one distinct counterpart upload.
    assert sum(call.url.path.endswith("/upload") for call in system.calls) == 2


def test_annotation_ambiguity_remains_blocked_with_portable_local_dataset(system):
    assert system.worker.once()

    def timeout(request):
        raise httpx.ReadTimeout("Annotation response missing", request=request)

    system.handler = timeout
    assert system.worker.once()
    before = intent(system)
    admin = ExportAdmin(system.repo)
    status = admin.status(system.pair["id"])
    assert not status["can_recheck"]
    assert "annotation" in status["recheck_reason"].lower()
    assert "local dataset remains available" in status["recheck_reason"]
    with pytest.raises(Conflict, match="annotation"):
        admin.recheck(system.pair["id"], "workspace-operator")
    assert intent(system)["receipts"] == before["receipts"]
    assert intent(system)["status"] == "uncertain"
    assert len(system.calls) == 2
    assert system.repo.snapshot(system.session["id"])["pair"]["manifest"] == system.pair["manifest"]


@pytest.mark.parametrize("state", ["pending", "running", "complete", "disabled"])
def test_active_completed_and_unrequested_exports_cannot_be_rechecked(system, state):
    if state == "running":
        assert system.repo.export_claim("active-owner")
    elif state == "complete":
        run_all(system)
    elif state == "disabled":
        with psycopg.connect(system.repo.dsn) as connection:
            connection.execute("UPDATE counterpart_export_intents SET status='disabled',destination=NULL")
    admin = ExportAdmin(system.repo)
    assert not admin.status(system.pair["id"])["can_recheck"]
    with pytest.raises(Conflict):
        admin.recheck(system.pair["id"], "workspace-operator")
    assert admin.status(system.pair["id"])["recheck_count"] == 0


def test_fixed_metadata_auth_can_resume_without_deleting_any_receipts(system):
    system.metadata_handler = lambda _: httpx.Response(401)
    assert system.worker.once()
    assert intent(system)["status"] == "failed"
    assert intent(system)["receipts"] == {}
    admin = ExportAdmin(system.repo)
    assert admin.status(system.pair["id"])["can_recheck"]
    system.metadata_handler = lambda _: httpx.Response(
        200, json={"project": {"id": "test-workspace/test-project", "public": False}}
    )
    admin.recheck(system.pair["id"], "workspace-operator")
    run_all(system)
    assert intent(system)["status"] == "complete"
    assert len(system.calls) == 4


def test_definite_upload_auth_failure_keeps_intent_and_only_searches(system):
    system.handler = lambda _: httpx.Response(401)
    assert system.worker.once()
    assert intent(system)["status"] == "failed"
    prior = copy.deepcopy(intent(system)["receipts"])
    assert len(prior) == 1
    admin = ExportAdmin(system.repo)
    assert admin.status(system.pair["id"])["can_recheck"]
    admin.recheck(system.pair["id"], "workspace-operator")
    system.handler = system.successful
    assert system.worker.once()
    assert len(system.calls) == 2
    assert system.calls[-1].url.path.endswith("/search")
    assert set(intent(system)["receipts"]) == set(prior)


def test_corrupted_evidence_or_receipts_cannot_use_recheck_as_validation_bypass(system):
    uncertain_upload(system)
    corrupted = copy.deepcopy(intent(system)["receipts"])
    next(iter(corrupted.values()))["destination"]["project"] = "different"
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute("UPDATE counterpart_export_intents SET receipts=%s", (Jsonb(corrupted),))
    admin = ExportAdmin(system.repo)
    assert not admin.status(system.pair["id"])["can_recheck"]
    with pytest.raises(Conflict):
        admin.recheck(system.pair["id"], "workspace-operator")
    assert intent(system)["receipts"] == corrupted


def test_concurrent_operator_requests_open_only_one_reconciliation_cycle(system):
    uncertain_upload(system)
    admin = ExportAdmin(system.repo)

    def recheck(_):
        try:
            return admin.recheck(system.pair["id"], "workspace-operator")["status"]
        except Conflict:
            return "refused"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(recheck, range(8)))
    assert outcomes.count("pending") == 1
    assert outcomes.count("refused") == 7
    assert admin.status(system.pair["id"])["recheck_count"] == 1
    assert len(system.calls) == 6


def test_recheck_cycles_have_a_persistent_manual_limit(system):
    uncertain_upload(system)
    admin = ExportAdmin(system.repo)
    for cycle in range(3):
        admin.recheck(system.pair["id"], "workspace-operator")
        for _ in range(5):
            assert system.worker.once()
            make_available(system)
        assert admin.status(system.pair["id"])["recheck_count"] == cycle + 1
    status = ExportAdmin(system.repo).status(system.pair["id"])
    assert not status["can_recheck"]
    assert len(status["history"]) == 3
    with pytest.raises(Conflict, match="Three operator rechecks"):
        admin.recheck(system.pair["id"], "workspace-operator")
    assert sum(call.url.path.endswith("/upload") for call in system.calls) == 1


def test_status_never_returns_raw_receipt_metadata_or_unrecognized_errors(system):
    uncertain_upload(system)
    corrupted = copy.deepcopy(intent(system)["receipts"])
    receipt = next(iter(corrupted.values()))
    receipt.update(error="secret-query?api_key=unsafe", raw_response="unsafe", state={"bad": "unsafe"})
    with psycopg.connect(system.repo.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_export_intents SET receipts=%s,last_error=%s",
            (Jsonb(corrupted), "secret-query?api_key=unsafe"),
        )
    result = ExportAdmin(system.repo).status(system.pair["id"])
    assert result["last_error"] == "unrecognized_export_error"
    assert result["receipts"][0]["error"] == "unrecognized_export_error"
    assert result["receipts"][0]["state"] == "unknown"
    assert "unsafe" not in str(result)


@pytest.mark.parametrize("pair_id", ["invalid", str(uuid4())])
def test_missing_pair_is_not_found(system, pair_id):
    with pytest.raises(NotFound):
        ExportAdmin(system.repo).status(pair_id)
    with pytest.raises(NotFound):
        ExportAdmin(system.repo).recheck(pair_id, "workspace-operator")


@pytest.mark.parametrize("reviewer", ["", "x" * 121, "bad\nidentity", None])
def test_operator_identity_is_required_for_audit(system, reviewer):
    with pytest.raises(Conflict):
        ExportAdmin(system.repo).recheck(system.pair["id"], reviewer)
