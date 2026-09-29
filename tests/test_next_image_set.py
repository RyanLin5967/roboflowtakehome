"""Durable collector continuation; actual PostgreSQL and fixture replay, no ML."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import pytest
import test_api

from counterpart.collections import CollectionRepository
from counterpart.consumer import PortableRelease
from counterpart.next_image_set import NextCollection, continuation_token
from counterpart.recipes import LIVE_RECIPE_VERSION
from counterpart.repository import Conflict
from counterpart.splits import manifest_split

pytestmark = pytest.mark.postgres
system = test_api.system


def auth(created):
    return {"Authorization": f"Bearer {created['token']}"}


def saved(system, created=None):
    client, repo, worker = system
    if created is None:
        response = client.post("/api/sessions", json={"mode": "replay"})
        assert response.status_code == 201
        created = response.json()
    session = created["session"]
    for scenario in ("baseline", "counterpart"):
        test_api.replay(client, session, auth(created), scenario)
        assert worker.once()
        session = repo.snapshot(session["id"])
    response = client.post(
        f"/api/sessions/{session['id']}/publish",
        headers=auth(created),
        json={"expected_revision": session["revision"]},
    )
    assert response.status_code == 200, response.text
    return {**created, "session": repo.snapshot(session["id"])}


def next_set(system, created, key=None):
    return system[0].post(
        f"/api/sessions/{created['session']['id']}/next",
        headers=auth(created),
        json={"idempotency_key": key or uuid4().hex},
    )


def campaign_source(system, *, limit=20, group=False):
    collections = CollectionRepository(system[1])
    campaign = collections.create_campaign("Collection", "spatial", "replay")
    group_id = collections.create_split_group("Same desk")["id"] if group else None
    invite = collections.create_invite(campaign["id"], max_sessions=limit, split_group_id=group_id)
    created = collections.create_session_from_invite(invite["token"], uuid4().hex)
    return saved(system, created), collections, campaign, invite


def test_saved_pair_continues_without_review_preserves_pair_and_exports(system):
    client, repo, _ = system
    source = saved(system)
    before = repo.snapshot(source["session"]["id"])
    assert before["review"]["status"] == "pending"
    response = next_set(system, source)
    assert response.status_code == 200, response.text
    child = response.json()
    session = child["session"]
    assert session["id"] != before["id"]
    assert session["state"] == "collecting"
    assert session["baseline"] is session["counterpart"] is session["latest_capture"] is None
    assert session["pair"] is None and session["review"] is None
    assert (session["recipe"], session["mode"], session["object_a"], session["object_b"]) == (
        "spatial",
        "replay",
        "bottle",
        "cup",
    )
    assert session["execution_contract"] == before["execution_contract"]
    assert manifest_split(session)["split_group"] == before["id"]
    assert session["split_group_snapshot"]["kind"] == "continued_session"
    after = repo.snapshot(before["id"])
    assert {k: v for k, v in after.items() if k != "events"} == {
        k: v for k, v in before.items() if k != "events"
    }
    # The new capability cannot read source evidence or vice versa.
    assert client.get(f"/api/sessions/{before['id']}", headers=auth(child)).status_code == 404
    assert client.get(f"/api/sessions/{session['id']}", headers=auth(source)).status_code == 404
    child = saved(system, child)
    archive = client.get(f"/api/sessions/{session['id']}/archive", headers=auth(child))
    assert archive.status_code == 200
    with PortableRelease(BytesIO(archive.content), allow_replay=True) as portable:
        assert portable.summary["pair_count"] == 1
    grandchild = next_set(system, child).json()
    assert grandchild["session"]["split_group_snapshot"] == session["split_group_snapshot"]


def test_lost_response_and_concurrent_retry_create_one_session(system):
    _, repo, _ = system
    source = saved(system)
    key = uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: next_set(system, source, key), range(4)))
    assert all(response.status_code == 200 for response in responses)
    children = [response.json() for response in responses]
    assert len({item["session"]["id"] for item in children}) == 1
    assert len({item["token"] for item in children}) == 1
    assert next_set(system, source, key).json() == children[0]
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 2
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM counterpart_events WHERE kind='next_image_set_created'"
            ).fetchone()["n"]
            == 1
        )


@pytest.mark.parametrize("mutation", ["missing_auth", "wrong_auth", "short_key", "extra_input", "not_saved"])
def test_continuation_rejects_invalid_or_unauthorized_requests(system, mutation):
    client, _, _ = system
    source = (
        saved(system)
        if mutation != "not_saved"
        else client.post("/api/sessions", json={"mode": "replay"}).json()
    )
    headers = auth(source)
    body = {"idempotency_key": uuid4().hex}
    if mutation == "missing_auth":
        headers = {}
    elif mutation == "wrong_auth":
        headers = {"Authorization": "Bearer incorrect"}
    elif mutation == "short_key":
        body["idempotency_key"] = "bad"
    elif mutation == "extra_input":
        body["mode"] = "live"
    response = client.post(f"/api/sessions/{source['session']['id']}/next", headers=headers, json=body)
    assert response.status_code == (
        404 if mutation.endswith("auth") else 409 if mutation == "not_saved" else 422
    ), response.text


@pytest.mark.parametrize("group", [False, True])
def test_campaign_pending_review_can_continue_with_same_contract_and_invite(system, group):
    _, repo, _ = system
    source, collections, campaign, invite = campaign_source(system, group=group)
    response = next_set(system, source)
    assert response.status_code == 200, response.text
    child = response.json()["session"]
    assert child["campaign_id"] == campaign["id"]
    assert child["recipe_snapshot"] == campaign["recipe_snapshot"]
    assert child["execution_contract"] == campaign["execution_contract"]
    assert manifest_split(child)["split_group"] == manifest_split(source["session"])["split_group"]
    assert child["split_group_snapshot"]["kind"] == ("operator_group" if group else "continued_session")
    assert collections.list_invites(campaign["id"])[0]["sessions_created"] == 2
    with repo._connection() as connection:
        origin = connection.execute(
            "SELECT invite_id FROM counterpart_invite_sessions WHERE session_id=%s", (child["id"],)
        ).fetchone()
        assert str(origin["invite_id"]) == invite["id"]


@pytest.mark.parametrize(
    "restriction,code",
    [
        ("paused", "collection_paused"),
        ("revoked", "invite_inactive"),
        ("expired", "invite_inactive"),
        ("quota", "invite_quota_reached"),
    ],
)
def test_campaign_continuation_respects_admission(system, restriction, code):
    _, repo, _ = system
    source, collections, campaign, invite = campaign_source(system, limit=1 if restriction == "quota" else 20)
    if restriction == "paused":
        collections.set_campaign_active(campaign["id"], False)
    elif restriction == "revoked":
        collections.revoke_invite(campaign["id"], invite["id"])
    elif restriction == "expired":
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_collection_invites SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE id=%s",
                (invite["id"],),
            )
    response = next_set(system, source)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code
    assert collections.list_invites(campaign["id"])[0]["sessions_created"] == 1


def test_campaign_receipt_recovers_after_quota_and_revocation(system):
    source, collections, campaign, invite = campaign_source(system, limit=2)
    key = uuid4().hex
    first = next_set(system, source, key)
    assert first.status_code == 200
    collections.revoke_invite(campaign["id"], invite["id"])
    retry = next_set(system, source, key)
    assert retry.status_code == 200 and retry.json() == first.json()
    assert next_set(system, source).status_code == 409


def test_concurrent_campaign_requests_cannot_exceed_quota(system):
    source, collections, campaign, _ = campaign_source(system, limit=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: next_set(system, source), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert collections.list_invites(campaign["id"])[0]["sessions_created"] == 2


def test_live_next_uses_current_rules_and_keeps_protected_objects(system):
    # Infrastructure-only synthetic publication: no observations/model execution.
    _, repo, _ = system
    created = repo.create_session("spatial", "live", "bottle", "cup", protected_objects=["book"])
    source_id = created["session"]["id"]
    with repo._connection() as connection:
        connection.execute("UPDATE counterpart_sessions SET state='published' WHERE id=%s", (source_id,))
        connection.execute(
            "INSERT INTO counterpart_pairs(id,session_id,manifest,export_status) "
            "VALUES(%s,%s,'{}','disabled')",
            (str(uuid4()), source_id),
        )
    key = uuid4().hex
    token = continuation_token(created["token"], source_id, key)
    with pytest.raises(Conflict) as error:
        NextCollection(repo).run(source_id, idempotency_key=key, token=token, allow_new_live=False)
    assert error.value.code == "live_disabled"
    child = NextCollection(repo).run(source_id, idempotency_key=key, token=token)["session"]
    assert child["recipe_snapshot"]["recipe_version"] == LIVE_RECIPE_VERSION
    assert child["recipe_snapshot"]["protected_objects"] == ["book"]
