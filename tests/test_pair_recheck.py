"""Saved-pixel rechecks, durable capability receipts and real PostgreSQL fences.

Generated pixels/observations exercise infrastructure, not provider accuracy.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from uuid import uuid4

import pytest
import test_api
from PIL import Image, ImageDraw
from psycopg.types.json import Jsonb
from test_visual_v2 import image_frame

from counterpart.collections import CollectionRepository
from counterpart.consumer import PortableRelease
from counterpart.evidence_revisions import evaluate
from counterpart.execution_contract import canonical
from counterpart.recheck import PairRecheck, derived_token
from counterpart.recipes import build_recipe_snapshot
from counterpart.repository import Conflict
from counterpart.splits import manifest_split, validate_split_group
from counterpart.verification import verify

pytestmark = pytest.mark.postgres
system = test_api.system


def saved_pair(
    system,
    *,
    moved_anchor=False,
    changed_color=True,
    bottle_color="#1f8040",
    illumination_shift=0,
    recipe_version="2.0.0",
    initial_only=False,
    flat=False,
    wrong_initial_relation=False,
):
    client, repo, _ = system
    with repo._connection() as connection:
        created = repo._create_session(
            connection,
            "spatial",
            "live",
            "bottle",
            "cup",
            recipe_snapshot=build_recipe_snapshot("spatial", recipe_version=recipe_version),
        )
    session = created["session"]
    media = client.app.state.media
    baseline = None
    for index, slot in enumerate(("baseline",) if initial_only else ("baseline", "counterpart")):
        assets = {}
        observed = image_frame(
            assets,
            right=bool(index) or wrong_initial_relation,
            moved_anchor=bool(index) and moved_anchor,
            flat=flat,
        )
        raw = assets[observed["asset_id"]]
        if index and changed_color:
            image = Image.open(BytesIO(raw)).convert("RGB")
            # One red-channel level crosses the old 32-wide hard histogram bin:
            # RGB(32,128,64) -> RGB(31,128,64). This is a numeric discontinuity,
            # not grounds for waiving an uncertain appearance check.
            ImageDraw.Draw(image).rectangle((474, 192, 538, 326), fill=bottle_color)
            output = BytesIO()
            image.save(output, "PNG")
            raw = output.getvalue()
        if index and illumination_shift:
            image = Image.open(BytesIO(raw)).convert("RGB")
            image = image.point(lambda value: min(255, max(0, value + illumination_shift)))
            output = BytesIO()
            image.save(output, "PNG")
            raw = output.getvalue()
        frame = {**media.put(raw), "t_ms": 0.0}
        observed = {
            **frame,
            "detections": observed["detections"],
            "model": {"provider": "roboflow", "model_id": "coco/39"},
            "inference": {"configuration_digest": session["execution_contract"]["digest"]},
        }
        repo.submit(
            session["id"],
            {
                "slot": slot,
                "expected_revision": session["revision"],
                "epoch": session["epoch"],
                "idempotency_key": uuid4().hex,
            },
            [frame],
            uuid4().hex,
        )
        job = repo.claim("generated-observation-test")
        result = verify(
            "spatial",
            slot,
            [observed],
            baseline,
            recipe_snapshot=session["recipe_snapshot"],
            media_reader=media.read,
        )
        assert repo.finish(job["id"], job["lease_token"], result, [observed])
        session = repo.snapshot(session["id"])
        if index == 0:
            if not initial_only:
                assert result["accepted"], result
            baseline = {**result, "frames": [observed]}
    created["session"] = session
    return created


def arguments(created, key=None):
    session = created["session"]
    return {
        "expected_revision": session["revision"],
        "epoch": session["epoch"],
        "idempotency_key": key or uuid4().hex,
    }


def recheck(system, created, body=None):
    body = body or arguments(created)
    return system[0].post(
        f"/api/sessions/{created['session']['id']}/recheck",
        json=body,
        headers={"Authorization": f"Bearer {created['token']}"},
    )


@pytest.mark.parametrize("variation", ["one_level_histogram_boundary", "global_illumination"])
@pytest.mark.parametrize("source_version", ["2.0.0", "2.1.0", "2.2.0", "2.3.0"])
def test_real_saved_pixels_recheck_without_provider_or_rewriting_source(system, variation, source_version):
    client, repo, _ = system
    created = saved_pair(
        system,
        changed_color=variation != "global_illumination",
        illumination_shift=12 if variation == "global_illumination" else 0,
        recipe_version=source_version,
    )
    original = created["session"]
    if source_version == "2.0.0":
        assert original["latest_capture"]["result"]["code"] == "appearance_uncertain"
    else:
        assert original["latest_capture"]["result"]["accepted"]
    before = repo.evaluation_snapshot(original["id"])["captures"]
    response = recheck(system, created)
    assert response.status_code == 200, response.text
    derived = response.json()
    session = derived["session"]
    assert session["id"] != original["id"] and session["state"] == "ready"
    assert session["recipe_snapshot"]["recipe_version"] == "2.4.0"
    assert session["execution_contract"] == original["execution_contract"]
    result = session["counterpart"]["result"]
    appearance = [check for check in result["checks"] if check["name"].startswith("appearance_continuity:")]
    assert appearance and all(check["outcome"] == "pass" for check in appearance)
    assert all(check["outcome"] == "pass" for check in result["checks"])
    assert not result.get("warnings")
    assert not session["human_review_required"]
    assert repo.evaluation_snapshot(original["id"])["captures"] == before
    original_now = repo.snapshot(original["id"])
    assert {k: v for k, v in original_now.items() if k != "events"} == {
        k: v for k, v in original.items() if k != "events"
    }
    assert manifest_split(session)["split"] == manifest_split(original)["split"]
    assert session["split_group_snapshot"]["id"] == original["id"]
    assert session["split_group_snapshot"]["kind"] == "derived_session"
    for slot, source in (("baseline", original["baseline"]), ("counterpart", original["latest_capture"])):
        capture = session[slot]
        assert capture["frames"] == source["frames"]
        assert capture["created_at"] == source["created_at"]
        assert capture["retry_of_capture_id"] == source["id"]
        assert capture["result"]["verifier_version"] == "counterpart-verifier/2.4.0"
        asset = source["frames"][0]["asset_id"]
        image = client.get(
            f"/api/sessions/{session['id']}/assets/{asset}",
            headers={"Authorization": f"Bearer {derived['token']}"},
        )
        assert image.status_code == 200
        assert image.content == client.app.state.media.read(asset)
    assert repo.authenticate(session["id"], derived["token"])
    assert not repo.authenticate(session["id"], created["token"])
    assert not repo.authenticate(original["id"], derived["token"])
    publication = repo.publish(session["id"], session["revision"])
    assert publication["manifest"]["provenance"]["human_reviewed"] is False
    assert all(
        check["outcome"] == "pass" for check in publication["manifest"]["counterpart"]["result"]["checks"]
    )
    baseline = None
    for slot in ("baseline", "counterpart"):
        record = publication["manifest"][slot]
        replayed = evaluate(
            record, session["recipe_snapshot"], baseline, media_reader=client.app.state.media.read
        )
        assert replayed == record["result"]
        assert canonical(replayed) == canonical(record["result"])
        baseline = {**replayed, "frames": record["frames"]}
    archive = client.get(
        f"/api/sessions/{session['id']}/archive",
        headers={"Authorization": f"Bearer {derived['token']}"},
    )
    assert archive.status_code == 200
    with PortableRelease(BytesIO(archive.content)) as consumer:
        assert consumer.summary["pair_count"] == 1
        assert consumer.evidence_kind == "roboflow_capture"
    with repo._connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM counterpart_jobs WHERE session_id=%s", (session["id"],)
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM counterpart_budget_reservations").fetchone()["n"]
            == 0
        )
        payloads = connection.execute("SELECT data FROM counterpart_events").fetchall()
        assert all(derived["token"] not in str(row) and created["token"] not in str(row) for row in payloads)


def test_recheck_does_not_pass_known_geometry_failure(system):
    created = saved_pair(system, moved_anchor=True)
    response = recheck(system, created)
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    assert session["state"] == "baseline_ready" and session["counterpart"] is None
    assert session["latest_capture"]["result"]["outcome"] == "fail"
    assert any(c.get("code") == "reference_moved" for c in session["latest_capture"]["result"]["checks"])
    with pytest.raises(Conflict, match="Both observations"):
        system[1].publish(session["id"], session["revision"])


def test_recheck_does_not_waive_actual_object_appearance_change(system):
    created = saved_pair(system, bottle_color="#e0e0e0")
    response = recheck(system, created)
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    assert session["state"] == "baseline_ready" and session["counterpart"] is None
    result = session["latest_capture"]["result"]
    assert result["accepted"] is False and result["outcome"] in {"fail", "unknown"}
    assert not result.get("warnings")
    assert any(
        check["name"].startswith("appearance_continuity:bottle:") and check["outcome"] != "pass"
        for check in result["checks"]
    )
    with pytest.raises(Conflict, match="Both observations"):
        system[1].publish(session["id"], session["revision"])


def test_recheck_lost_ack_is_idempotent_even_after_source_reset_and_child_publication(system):
    _, repo, _ = system
    created = saved_pair(system)
    body = arguments(created)
    first = recheck(system, created, body).json()
    repo.publish(first["session"]["id"], first["session"]["revision"])
    source = created["session"]
    repo.reset_capture(source["id"], source["revision"], source["epoch"], uuid4().hex)
    duplicate = recheck(system, created, body)
    assert duplicate.status_code == 200
    assert duplicate.json()["token"] == first["token"]
    assert duplicate.json()["session"]["id"] == first["session"]["id"]
    assert duplicate.json()["session"]["state"] == "published"
    changed = recheck(system, created, {**body, "expected_revision": body["expected_revision"] + 1})
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "idempotency_mismatch"


@pytest.mark.parametrize("previous_version", ["2.1.0", "2.2.0", "2.3.0"])
def test_historical_receipt_survives_target_upgrade_and_derived_chain_keeps_original_split(
    system, monkeypatch, previous_version
):
    import counterpart.recheck as module

    client, repo, _ = system
    created = saved_pair(system)
    source = created["session"]
    body = arguments(created)
    # Exercise a real receipt produced under the previous deployed target,
    # rather than forging a JSON event that merely resembles one.
    with monkeypatch.context() as previous_server:
        previous_server.setattr(module, "TARGET_VERSION", previous_version)
        response = recheck(system, created, body)
    assert response.status_code == 200, response.text
    old_child = response.json()
    old_session = old_child["session"]
    assert old_session["recipe_snapshot"]["recipe_version"] == previous_version
    original_captures = repo.evaluation_snapshot(old_session["id"])["captures"]
    original_receipt = next(
        event for event in repo.snapshot(source["id"])["events"] if event["kind"] == "saved_pair_rechecked"
    )
    assert original_receipt["data"]["request"]["recipe_version"] == previous_version

    # Same caller request after upgrade recovers its original child and token.
    recovered = recheck(system, created, body)
    assert recovered.status_code == 200
    assert recovered.json() == old_child
    assert (
        next(
            event
            for event in repo.snapshot(source["id"])["events"]
            if event["kind"] == "saved_pair_rechecked"
        )
        == original_receipt
    )
    changed = recheck(system, created, {**body, "epoch": body["epoch"] + 1})
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "idempotency_mismatch"

    # A new request to the historical derived collection verifies exact saved evidence
    # under 2.3, retaining the original scene's split through the entire chain.
    current = recheck(system, old_child)
    assert current.status_code == 200, current.text
    grandchild = current.json()
    session = grandchild["session"]
    assert session["recipe_snapshot"]["recipe_version"] == "2.4.0" and session["state"] == "ready"
    assert session["split_group_snapshot"] == old_session["split_group_snapshot"]
    assert session["split_group_snapshot"]["id"] == source["id"]
    assert manifest_split(session)["split"] == manifest_split(source)["split"]
    assert repo.evaluation_snapshot(old_session["id"])["captures"] == original_captures
    for slot in ("baseline", "counterpart"):
        assert session[slot]["frames"] == old_session[slot]["frames"]
        assert session[slot]["retry_of_capture_id"] == old_session[slot]["id"]
        assert session[slot]["result"]["verifier_version"] == "counterpart-verifier/2.4.0"
    repo.publish(session["id"], session["revision"])
    archive = client.get(
        f"/api/sessions/{session['id']}/archive",
        headers={"Authorization": f"Bearer {grandchild['token']}"},
    )
    assert archive.status_code == 200
    with PortableRelease(BytesIO(archive.content)) as consumer:
        assert consumer.summary["pair_count"] == 1
    recovered_after_chain = recheck(system, created, body).json()
    assert recovered_after_chain["token"] == old_child["token"]
    assert recovered_after_chain["session"]["id"] == old_session["id"]
    assert recovered_after_chain["session"]["recipe_snapshot"] == old_session["recipe_snapshot"]
    assert recovered_after_chain["session"]["baseline"] == old_session["baseline"]
    assert recovered_after_chain["session"]["counterpart"] == old_session["counterpart"]


def test_current_version_source_cannot_create_another_automatic_clone(system):
    created = saved_pair(system, recipe_version="2.4.0")
    response = recheck(system, created)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "recheck_ineligible"
    assert system[1].snapshot(created["session"]["id"]) == created["session"]


@pytest.mark.parametrize("source_version", ["2.0.0", "2.1.0", "2.2.0", "2.3.0"])
@pytest.mark.parametrize("flat", [True, False])
def test_initial_saved_image_recheck_preserves_source_and_requires_physical_recovery(
    system, source_version, flat
):
    client, repo, _ = system
    created = saved_pair(system, recipe_version=source_version, initial_only=True, flat=flat)
    original = created["session"]
    assert original["latest_capture"]["result"]["accepted"] is (not flat or source_version == "2.3.0")
    before = repo.evaluation_snapshot(original["id"])["captures"]
    body = arguments(created)
    response = recheck(system, created, body)
    assert response.status_code == 200, response.text
    derived = response.json()
    session = derived["session"]
    assert session["id"] != original["id"]
    assert session["state"] == "baseline_ready"
    assert session["recipe_snapshot"]["recipe_version"] == "2.4.0"
    assert session["revision"] == 1 and session["recovery_required"] is True
    assert session["counterpart"] is None
    baseline = session["baseline"]
    assert baseline["result"]["accepted"]
    assert baseline["reference_capture_id"] is None
    assert baseline["frames"] == original["latest_capture"]["frames"]
    assert baseline["created_at"] == original["latest_capture"]["created_at"]
    assert baseline["retry_of_capture_id"] == original["latest_capture"]["id"]
    assert session["latest_capture"]["id"] == baseline["id"]
    assert session["execution_contract"] == original["execution_contract"]
    assert manifest_split(session)["split"] == manifest_split(original)["split"]
    assert repo.evaluation_snapshot(original["id"])["captures"] == before
    assert not session["human_review_required"]
    # Database recovery cannot assert the physical scene still matches these
    # old pixels. The next committed capture must first check the current scene.
    with pytest.raises(Conflict) as caught:
        repo.submit(
            session["id"],
            {"slot": "counterpart", "expected_revision": 1, "epoch": 0, "idempotency_key": uuid4().hex},
            baseline["frames"],
            uuid4().hex,
        )
    assert caught.value.code == "recovery_required"
    replayed = evaluate(baseline, session["recipe_snapshot"], None, media_reader=client.app.state.media.read)
    assert canonical(replayed) == canonical(baseline["result"])
    with repo._connection() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM counterpart_budget_reservations").fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM counterpart_jobs WHERE session_id=%s", (session["id"],)
            ).fetchone()["n"]
            == 0
        )
    # Lost ACK recovers its exact child even if the parent is reset meanwhile.
    repo.reset_capture(original["id"], original["revision"], original["epoch"], uuid4().hex)
    recovered = recheck(system, created, body)
    assert recovered.status_code == 200 and recovered.json() == derived


def test_initial_recheck_keeps_wrong_relation_failed_even_on_plain_background(system):
    created = saved_pair(
        system, recipe_version="2.2.0", initial_only=True, flat=True, wrong_initial_relation=True
    )
    response = recheck(system, created)
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    assert session["state"] == "collecting" and session["baseline"] is None
    assert session["latest_capture"]["result"]["accepted"] is False
    assert session["latest_capture"]["result"]["outcome"] == "fail"
    assert any(
        check.get("code") == "wrong_relation" for check in session["latest_capture"]["result"]["checks"]
    )
    with pytest.raises(Conflict):
        system[1].publish(session["id"], session["revision"])


def test_initial_recheck_concurrent_receipts_create_one_child(system):
    client, repo, _ = system
    created = saved_pair(system, recipe_version="2.2.0", initial_only=True, flat=True)
    body = arguments(created)
    token = derived_token(created["token"], created["session"]["id"], body["idempotency_key"])
    service = PairRecheck(repo, client.app.state.media)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda _: service.run(created["session"]["id"], token=token, **body), range(3))
        )
    assert len({row["session"]["id"] for row in results}) == 1
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 2
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM counterpart_events WHERE kind='saved_pair_rechecked'"
            ).fetchone()["n"]
            == 1
        )


@pytest.mark.parametrize("field,code", [("expected_revision", "stale_revision"), ("epoch", "stale_epoch")])
def test_recheck_stale_request_cannot_fork(system, field, code):
    created = saved_pair(system)
    body = arguments(created)
    body[field] += 1
    response = recheck(system, created, body)
    assert response.status_code == 409 and response.json()["detail"]["code"] == code
    assert system[1].snapshot(created["session"]["id"]) == created["session"]


def test_recheck_resume_after_saved_pair_does_not_require_recapture(system):
    created = saved_pair(system)
    before = created["session"]
    created["session"] = system[1].resume(before["id"], before["epoch"])
    assert created["session"]["recovery_required"]
    response = recheck(system, created)
    assert response.status_code == 200 and response.json()["session"]["state"] == "ready"


def test_recheck_keeps_explicit_scene_group(system):
    client, repo, _ = system
    created = saved_pair(system)
    # Group is frozen at admission; create a separate source with the same
    # declared grouping, then reuse exact observations through normal jobs.
    group = CollectionRepository(repo).create_split_group("One physical test scene")
    snapshot = {
        "schema_version": "counterpart.split-group.v1",
        "id": group["id"],
        "kind": "operator_group",
        "name": group["name"],
    }
    with repo._connection() as connection:
        grouped = repo._create_session(
            connection,
            "spatial",
            "live",
            "bottle",
            "cup",
            recipe_snapshot=created["session"]["recipe_snapshot"],
            split_group_snapshot=snapshot,
        )
    current = grouped["session"]
    for slot, field in (("baseline", "baseline"), ("counterpart", "latest_capture")):
        original = created["session"][field]
        repo.submit(
            current["id"],
            {
                "slot": slot,
                "expected_revision": current["revision"],
                "epoch": 0,
                "idempotency_key": uuid4().hex,
            },
            original["frames"],
            uuid4().hex,
        )
        job = repo.claim("grouped-observation-test")
        assert repo.finish(job["id"], job["lease_token"], original["result"], original["frames"])
        current = repo.snapshot(current["id"])
    grouped["session"] = current
    response = recheck(system, grouped)
    assert response.status_code == 200, response.text
    assert response.json()["session"]["split_group_snapshot"] == snapshot


def test_recheck_rejects_human_authorized_source_before_automatic_verification(system):
    client, repo, _ = system
    created = saved_pair(system)
    with repo._connection() as connection:
        session = repo._session(connection, created["session"]["id"])
        session["baseline_authorization"] = {"decision": "allow_counterpart"}
        with pytest.raises(Conflict) as caught:
            PairRecheck(repo, client.app.state.media)._source(connection, session, arguments(created))
        assert caught.value.code == "recheck_ineligible"


def test_recheck_rejects_unauthorized_capability_and_client_policy(system):
    client, _, _ = system
    created = saved_pair(system)
    url = f"/api/sessions/{created['session']['id']}/recheck"
    body = arguments(created)
    for headers in ({}, {"Authorization": "Bearer another-session-token"}):
        assert client.post(url, json=body, headers=headers).status_code == 404
    assert recheck(system, created, {**body, "recipe_version": "1.0.0"}).status_code == 422
    assert client.get("/api/health").json()["saved_pair_recheck"] is True


@pytest.mark.parametrize(
    "change,code",
    [
        ("published", "already_published"),
        ("campaign", "campaign_contract_frozen"),
        ("reference", "recheck_reference_changed"),
        ("observations", "recheck_ineligible"),
        ("provenance", "recheck_evidence_invalid"),
        ("source_media", "recheck_evidence_invalid"),
        ("running", "verification_in_progress"),
    ],
)
def test_recheck_refuses_invalid_source_without_new_session(system, change, code):
    _, repo, _ = system
    created = saved_pair(system, changed_color=False)
    original = created["session"]
    campaign = (
        CollectionRepository(repo).create_campaign("Frozen campaign", "spatial")
        if change == "campaign"
        else None
    )
    with repo._connection() as connection:
        if change == "published":
            connection.execute(
                "UPDATE counterpart_sessions SET state='published' WHERE id=%s", (original["id"],)
            )
        elif change == "campaign":
            connection.execute(
                "UPDATE counterpart_sessions SET campaign_id=%s WHERE id=%s", (campaign["id"], original["id"])
            )
        elif change == "reference":
            connection.execute(
                "UPDATE counterpart_captures SET reference_capture_id=NULL WHERE id=%s",
                (original["latest_capture"]["id"],),
            )
        elif change == "observations":
            connection.execute(
                "UPDATE counterpart_captures SET observed_frames=NULL WHERE id=%s",
                (original["latest_capture"]["id"],),
            )
        elif change in {"provenance", "source_media"}:
            frames = deepcopy(original["latest_capture"]["frames"])
            if change == "provenance":
                frames[0]["model"]["model_id"] = "another/1"
            else:
                frames[0]["t_ms"] = 12
            connection.execute(
                "UPDATE counterpart_captures SET observed_frames=%s WHERE id=%s",
                (Jsonb(frames), original["latest_capture"]["id"]),
            )
        elif change == "running":
            connection.execute(
                "UPDATE counterpart_jobs SET status='running' WHERE capture_id=%s",
                (original["latest_capture"]["id"],),
            )
    response = recheck(system, created)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 1


def test_recheck_revalidates_revision_after_pixel_work(system, monkeypatch):
    import counterpart.recheck as module

    created = saved_pair(system)
    repo = system[1]
    original = module.verify
    changed = False

    def racing_verify(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            saved = created["session"]
            repo.reset_capture(saved["id"], saved["revision"], saved["epoch"], uuid4().hex)
        return result

    monkeypatch.setattr(module, "verify", racing_verify)
    response = recheck(system, created)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "stale_revision"
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 1


def test_concurrent_recheck_creates_one_child_and_no_plaintext_receipt(system):
    client, repo, _ = system
    created = saved_pair(system)
    body = arguments(created)
    token = derived_token(created["token"], created["session"]["id"], body["idempotency_key"])
    service = PairRecheck(repo, client.app.state.media)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda _: service.run(created["session"]["id"], token=token, **body), range(3))
        )
    assert len({row["session"]["id"] for row in results}) == 1
    assert all(row["token"] == token for row in results)
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 2
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM counterpart_events WHERE kind='saved_pair_rechecked'"
            ).fetchone()["n"]
            == 1
        )


def test_derived_group_cannot_claim_self_or_operator_label():
    source_id, child_id = str(uuid4()), str(uuid4())
    group = {
        "schema_version": "counterpart.split-group.v1",
        "id": source_id,
        "kind": "derived_session",
        "name": None,
    }
    assert validate_split_group(group, child_id) == group
    with pytest.raises(ValueError):
        validate_split_group(group, source_id)
    with pytest.raises(ValueError):
        validate_split_group({**group, "name": "invented operator group"}, child_id)
