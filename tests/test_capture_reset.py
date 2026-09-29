"""Reset only the active pair, with real PostgreSQL fencing and preserved evidence."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
import test_api
import test_reviewed_baseline_runtime
from test_api import new_session, replay
from test_repository import accepted, session, submit, verdict
from test_repository import repository as repository
from test_reviewed_baseline_runtime import authorized

from counterpart.collections import CollectionRepository
from counterpart.evidence_revisions import BASELINE_CRITERIA, capture_digest
from counterpart.repository import Conflict

pytestmark = pytest.mark.postgres
api_system = test_api.system
reviewed_system = test_reviewed_baseline_runtime.system


def reset(repo, saved, key=None, slot="baseline"):
    return repo.reset_capture(saved["id"], saved["revision"], saved["epoch"], key or uuid4().hex, slot)


@pytest.mark.parametrize("accept", [True, False])
def test_reset_clears_active_state_but_preserves_completed_evidence(repository, accept):
    saved = session(repository)
    submit(repository, saved)
    job = repository.claim("worker")
    repository.finish(job["id"], job["lease_token"], verdict(accept), job["capture"]["frames"])
    saved = repository.snapshot(saved["id"])
    original = saved["latest_capture"]
    fresh = reset(repository, saved)
    assert fresh["state"] == "collecting" and not fresh["recovery_required"]
    assert fresh["revision"] == saved["revision"] + 1 and fresh["epoch"] == saved["epoch"] + 1
    for pointer in ("baseline", "counterpart", "latest_capture", "feedback", "baseline_authorization"):
        assert fresh[pointer] is None
    assert not fresh["human_review_required"]
    with repository._connection() as connection:
        assert repository._capture(connection, original["id"]) == original
    assert fresh["events"][-1]["kind"] == "capture_reset"
    assert fresh["events"][-1]["data"]["previous_latest_capture_id"] == original["id"]
    replacement = accepted(repository, fresh)
    assert replacement["baseline"]["id"] != original["id"]
    assert replacement["baseline"]["reference_capture_id"] is None


def test_reset_preserves_collection_recipe_execution_and_split_identity(repository):
    collections = CollectionRepository(repository)
    campaign = collections.create_campaign("Retake without using another invitation", "spatial")
    group = collections.create_split_group("Same desk")
    invitation = collections.create_invite(campaign["id"], max_sessions=1, split_group_id=group["id"])
    joined = collections.create_session_from_invite(invitation["token"], uuid4().hex)
    saved = accepted(repository, joined["session"])
    saved = repository.resume(saved["id"], saved["epoch"])
    assert saved["recovery_required"]
    fresh = reset(repository, saved)
    for field in ("id", "recipe_snapshot", "execution_contract", "campaign_id", "split_group_snapshot"):
        assert fresh[field] == saved[field]
    assert repository.authenticate(fresh["id"], joined["token"])
    assert not fresh["recovery_required"]
    assert collections.get_campaign(campaign["id"])["progress"]["sessions"] == 1


@pytest.mark.parametrize("slot", ["baseline", "counterpart", "preview", "recovery"])
def test_reset_fences_inflight_workers_and_old_capture_submissions(repository, slot):
    saved = session(repository)
    if slot != "baseline":
        saved = accepted(repository, saved)
    if slot == "recovery":
        saved = repository.resume(saved["id"], saved["epoch"])
    result, metadata, frames = submit(repository, saved, slot, seq=1)
    job = repository.claim("old-worker")
    fresh = reset(repository, result["session"])
    assert not repository.heartbeat(job["id"], job["lease_token"])
    assert not repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    assert not repository.fail(job["id"], job["lease_token"], "provider_unavailable", retryable=True)
    assert repository.snapshot(saved["id"])["latest_capture"] is None
    # Recovering a previously admitted upload receipt cannot reattach its evidence.
    duplicate = repository.submit(saved["id"], metadata, frames, metadata["idempotency_key"])
    assert duplicate["capture"]["status"] == "superseded"
    assert duplicate["session"]["baseline"] is None
    with pytest.raises(Conflict) as caught:
        repository.submit(
            saved["id"], {**metadata, "idempotency_key": uuid4().hex}, frames, "new-upload"
        )
    assert caught.value.code == "stale_revision"
    assert accepted(repository, fresh)["state"] == "baseline_ready"


def test_reset_clears_feedback_and_supersedes_queued_preview_and_recovery(repository):
    saved = accepted(repository, session(repository))
    saved = accepted(repository, saved, "preview")
    assert saved["feedback"] is not None
    submit(repository, saved, "preview", seq=1)
    submit(repository, saved, "recovery")
    fresh = reset(repository, repository.snapshot(saved["id"]))
    assert fresh["feedback"] is None and fresh["latest_capture"] is None
    assert repository.claim("worker") is None
    # Preview sequence starts over in the fresh epoch.
    assert submit(repository, fresh, "preview", seq=0)[0]["capture"]["epoch"] == fresh["epoch"]


def test_concurrent_duplicate_reset_only_advances_once_and_cannot_erase_new_baseline(repository):
    saved = accepted(repository, session(repository))
    key = uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reset(repository, saved, key), range(8)))
    assert {row["revision"] for row in results} == {saved["revision"] + 1}
    assert {row["epoch"] for row in results} == {saved["epoch"] + 1}
    current = accepted(repository, results[0])
    retried = reset(repository, saved, key)
    assert retried["baseline"] == current["baseline"] and retried["revision"] == current["revision"]
    assert sum(event["kind"] == "capture_reset" for event in retried["events"]) == 1
    with pytest.raises(Conflict) as caught:
        reset(repository, current, key)
    assert caught.value.code == "idempotency_mismatch"


def test_reset_racing_worker_completion_cannot_restore_old_baseline(repository):
    saved = session(repository)
    submitted, _, _ = submit(repository, saved)
    job = repository.claim("finishing-worker")
    gate = Barrier(2)

    def finish():
        gate.wait(timeout=5)
        return repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])

    def reset_now():
        gate.wait(timeout=5)
        return reset(repository, submitted["session"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        finishing, resetting = pool.submit(finish), pool.submit(reset_now)
        finishing.result(timeout=10)
        resetting.result(timeout=10)
    current = repository.snapshot(saved["id"])
    assert current["baseline"] is None and current["latest_capture"] is None
    assert current["state"] == "collecting"
    assert current["revision"] == submitted["session"]["revision"] + 1
    assert repository.claim("replacement-worker") is None


@pytest.mark.parametrize("field,code", [("revision", "stale_revision"), ("epoch", "stale_epoch")])
def test_reset_refuses_stale_browser_without_changing_pair(repository, field, code):
    saved = accepted(repository, session(repository))
    stale = {**saved, field: saved[field] + 1}
    with pytest.raises(Conflict) as caught:
        reset(repository, stale)
    assert caught.value.code == code
    assert repository.snapshot(saved["id"]) == saved


def test_reset_failed_capture_prevents_reverification_from_restoring_it(repository):
    saved = session(repository)
    submitted, _, _ = submit(repository, saved)
    job = repository.claim("worker")
    repository.fail(job["id"], job["lease_token"], "provider_unavailable")
    fresh = reset(repository, repository.snapshot(saved["id"]))
    with pytest.raises(Conflict) as caught:
        repository.retry_capture(
            saved["id"], submitted["capture"]["id"], fresh["revision"], fresh["epoch"], uuid4().hex
        )
    assert caught.value.code == "stale_capture"


def test_reset_preserves_authorization_receipt_but_prevents_reactivation(reviewed_system):
    repo, evidence, _, _ = reviewed_system
    saved, original, record = authorized(reviewed_system)
    fresh = reset(repo, saved)
    assert fresh["baseline_authorization"] is None and fresh["baseline"] is None
    with repo._connection() as connection:
        assert repo._capture(connection, original["id"]) == original
        receipt = connection.execute(
            "SELECT record FROM counterpart_baseline_authorizations WHERE id=%s", (record["id"],)
        ).fetchone()["record"]
    assert receipt == record
    with pytest.raises(Conflict) as caught:
        evidence.authorize_baseline(
            saved["id"],
            original["id"],
            expected_session_revision=fresh["revision"],
            expected_evidence_sha256=capture_digest(original, saved["recipe_snapshot"]),
            answer=saved["recipe_snapshot"]["expected_answers"]["baseline"],
            criteria=dict.fromkeys(BASELINE_CRITERIA, True),
            reason="A delayed review of the old initial observation after its collector reset it.",
            idempotency_key=uuid4().hex,
        )
    assert caught.value.code == "stale_evidence"
    assert repo.snapshot(saved["id"])["baseline"] is None


def test_reset_http_capability_validation_and_saved_media_retention(api_system):
    client, repo, worker = api_system
    saved, auth = new_session(client)
    _, other_auth = new_session(client)
    replay(client, saved, auth, "baseline")
    assert worker.once()
    saved = repo.snapshot(saved["id"])
    asset = saved["baseline"]["frames"][0]["asset_id"]
    original_bytes = client.app.state.media.read(asset)
    route = f"/api/sessions/{saved['id']}/reset"
    body = {"expected_revision": saved["revision"], "epoch": saved["epoch"], "idempotency_key": uuid4().hex}
    for headers in ({}, other_auth):
        assert client.post(route, headers=headers, json=body).status_code == 404
    assert client.post(route, headers=auth, json={**body, "epoch": -1}).status_code == 422
    response = client.post(route, headers=auth, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["baseline"] is None and response.json()["latest_capture"] is None
    assert client.post(route, headers=auth, json=body).json() == response.json()
    # The collector no longer displays this asset; operator evidence and the
    # immutable backing media retain it without widening collector authorization.
    assert client.app.state.media.read(asset) == original_bytes


@pytest.mark.parametrize("slot", ["baseline", "counterpart"])
def test_reset_http_refuses_published_pair_without_mutating_manifest(api_system, slot):
    client, repo, worker = api_system
    saved, auth = new_session(client)
    for scenario in ("baseline", "counterpart"):
        replay(client, saved, auth, scenario)
        assert worker.once()
        saved = repo.snapshot(saved["id"])
    pair = repo.publish(saved["id"], saved["revision"])
    before = repo.snapshot(saved["id"])
    response = client.post(
        f"/api/sessions/{saved['id']}/reset",
        headers=auth,
        json={
            "expected_revision": saved["revision"], "epoch": saved["epoch"],
            "idempotency_key": uuid4().hex, "slot": slot,
        },
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "already_published"
    assert repo.snapshot(saved["id"]) == before
    assert repo.publish(saved["id"], saved["revision"])["manifest"] == pair["manifest"]


def test_retake_second_preserves_baseline_contract_and_old_evidence(repository):
    collections = CollectionRepository(repository)
    campaign = collections.create_campaign("Keep first photo", "spatial")
    group = collections.create_split_group("Same desk after second-photo retake")
    invitation = collections.create_invite(campaign["id"], max_sessions=1, split_group_id=group["id"])
    joined = collections.create_session_from_invite(invitation["token"], uuid4().hex)
    saved = accepted(repository, joined["session"])
    saved = accepted(repository, saved, "counterpart")
    saved = accepted(repository, saved, "preview")
    assert saved["counterpart"] and saved["feedback"]

    fresh = reset(repository, saved, slot="counterpart")

    assert fresh["state"] == "baseline_ready"
    assert fresh["revision"] == saved["revision"] + 1 and fresh["epoch"] == saved["epoch"] + 1
    assert fresh["counterpart"] is None and fresh["feedback"] is None
    assert fresh["latest_capture"] == fresh["baseline"] == saved["baseline"]
    for field in (
        "id", "recipe_snapshot", "execution_contract", "campaign_id", "split_group_snapshot",
        "baseline_authorization", "human_review_required", "recovery_required",
    ):
        assert fresh[field] == saved[field]
    with repository._connection() as connection:
        assert repository._capture(connection, saved["counterpart"]["id"]) == saved["counterpart"]
    assert repository.authenticate(fresh["id"], joined["token"])
    assert collections.get_campaign(campaign["id"])["progress"]["sessions"] == 1


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_retake_second_http_accepts_next_counterpart_against_same_baseline(api_system, recipe):
    client, repo, worker = api_system
    saved, auth = new_session(client, recipe)
    for scenario in ("baseline", "counterpart"):
        replay(client, saved, auth, scenario)
        assert worker.once()
        saved = repo.snapshot(saved["id"])
    original_baseline, original_counterpart = saved["baseline"], saved["counterpart"]
    response = client.post(
        f"/api/sessions/{saved['id']}/reset", headers=auth,
        json={
            "expected_revision": saved["revision"], "epoch": saved["epoch"],
            "idempotency_key": uuid4().hex, "slot": "counterpart",
        },
    )
    assert response.status_code == 200, response.text
    fresh = response.json()
    assert fresh["latest_capture"] == original_baseline
    replay(client, fresh, auth, "counterpart")
    assert worker.once()
    current = repo.snapshot(saved["id"])
    assert current["state"] == "ready" and current["baseline"] == original_baseline
    assert current["counterpart"]["id"] != original_counterpart["id"]
    assert current["counterpart"]["reference_capture_id"] == original_baseline["id"]
    assert current["counterpart"]["result"]["accepted"]
    # The ordinary publisher still verifies the replacement revision and pair.
    assert repo.publish(current["id"], current["revision"])["manifest"]


@pytest.mark.parametrize("slot", ["counterpart", "preview", "recovery"])
def test_retake_second_fences_inflight_work_and_pending_submissions(repository, slot):
    saved = accepted(repository, session(repository))
    if slot == "recovery":
        saved = repository.resume(saved["id"], saved["epoch"])
    submitted, metadata, frames = submit(repository, saved, slot, seq=1)
    job = repository.claim("old-worker")

    fresh = reset(repository, submitted["session"], slot="counterpart")

    assert fresh["baseline"] == saved["baseline"]
    assert fresh["recovery_required"] == saved["recovery_required"]
    assert not repository.heartbeat(job["id"], job["lease_token"])
    assert not repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])
    assert not repository.fail(job["id"], job["lease_token"], "provider_unavailable", retryable=True)
    duplicate = repository.submit(saved["id"], metadata, frames, metadata["idempotency_key"])
    assert duplicate["capture"]["status"] == "superseded"
    assert duplicate["session"]["latest_capture"] == saved["baseline"]
    with pytest.raises(Conflict) as caught:
        repository.submit(saved["id"], {**metadata, "idempotency_key": uuid4().hex}, frames, "new-upload")
    assert caught.value.code == "stale_revision"
    assert repository.claim("replacement-worker") is None


def test_retake_second_requires_baseline_and_preserves_unresolved_recovery(repository):
    saved = session(repository)
    with pytest.raises(Conflict) as caught:
        reset(repository, saved, slot="counterpart")
    assert caught.value.code == "baseline_required"
    assert repository.snapshot(saved["id"]) == saved
    saved = accepted(repository, saved)
    saved = repository.resume(saved["id"], saved["epoch"])
    fresh = reset(repository, saved, slot="counterpart")
    assert fresh["recovery_required"]
    with pytest.raises(Conflict) as caught:
        submit(repository, fresh, "counterpart")
    assert caught.value.code == "recovery_required"


def test_retake_second_retains_exact_human_baseline_authority(reviewed_system):
    repo, _, _, _ = reviewed_system
    saved, original, record = authorized(reviewed_system)
    saved = repo.resume(saved["id"], saved["epoch"])
    fresh = reset(repo, saved, slot="counterpart")
    assert fresh["baseline"] == fresh["latest_capture"] == original
    assert fresh["baseline_authorization"] == record
    assert fresh["human_review_required"] and fresh["recovery_required"]
    submitted, _, _ = submit(repo, fresh, "counterpart")
    assert submitted["capture"]["reference_capture_id"] == original["id"]
    assert submitted["capture"]["baseline_authorization"] == record
    assert submitted["capture"]["recovery_unresolved"]


def test_retake_second_duplicate_cannot_erase_new_second_photo_and_slot_mismatch_conflicts(repository):
    saved = accepted(repository, session(repository))
    saved = accepted(repository, saved, "counterpart")
    key = uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reset(repository, saved, key, "counterpart"), range(8)))
    assert {row["revision"] for row in results} == {saved["revision"] + 1}
    current = accepted(repository, results[0], "counterpart")
    retried = reset(repository, saved, key, "counterpart")
    assert retried["counterpart"] == current["counterpart"]
    assert retried["baseline"] == saved["baseline"]
    assert retried["revision"] == current["revision"]
    with pytest.raises(Conflict) as caught:
        reset(repository, saved, key, "baseline")
    assert caught.value.code == "idempotency_mismatch"


def test_retake_second_racing_worker_completion_cannot_restore_old_second_photo(repository):
    saved = accepted(repository, session(repository))
    submitted, _, _ = submit(repository, saved, "counterpart")
    job = repository.claim("finishing-worker")
    gate = Barrier(2)

    def finish():
        gate.wait(timeout=5)
        return repository.finish(job["id"], job["lease_token"], verdict(), job["capture"]["frames"])

    def reset_now():
        gate.wait(timeout=5)
        return reset(repository, submitted["session"], slot="counterpart")

    with ThreadPoolExecutor(max_workers=2) as pool:
        finishing, resetting = pool.submit(finish), pool.submit(reset_now)
        finishing.result(timeout=10)
        resetting.result(timeout=10)
    current = repository.snapshot(saved["id"])
    assert current["baseline"] == current["latest_capture"] == saved["baseline"]
    assert current["counterpart"] is None and current["state"] == "baseline_ready"
    assert current["revision"] == submitted["session"]["revision"] + 1
    assert repository.claim("replacement-worker") is None


def test_legacy_reset_receipt_still_accepts_explicit_baseline_but_not_counterpart(api_system):
    client, repo, worker = api_system
    saved, auth = new_session(client)
    replay(client, saved, auth, "baseline")
    assert worker.once()
    saved = repo.snapshot(saved["id"])
    key = uuid4().hex
    original = repo.reset_capture(saved["id"], saved["revision"], saved["epoch"], key)
    receipt = original["events"][-1]["data"]
    assert receipt["request"] == {"expected_revision": saved["revision"], "epoch": saved["epoch"]}
    route = f"/api/sessions/{saved['id']}/reset"
    body = {**receipt["request"], "idempotency_key": key, "slot": "baseline"}
    response = client.post(route, headers=auth, json=body)
    assert response.status_code == 200 and response.json() == original
    response = client.post(route, headers=auth, json={**body, "slot": "counterpart"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "idempotency_mismatch"
    response = client.post(route, headers=auth, json={**body, "slot": "preview"})
    assert response.status_code == 422
