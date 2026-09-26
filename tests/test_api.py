import hashlib
import io
import json
import os
import zipfile
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.api import create_app
from counterpart.config import Settings
from counterpart.repository import Repository
from counterpart.worker import Worker


@pytest.fixture
def system(tmp_path):
    dsn = os.getenv("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "test_api_" + uuid4().hex
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    except psycopg.OperationalError:
        pytest.skip("An isolated PostgreSQL test database is required")
    scoped = make_conninfo(dsn, options=f"-c search_path={schema}")
    config = Settings(
        database_url=scoped, media_dir=tmp_path / "media", web_dir=tmp_path / "web", live_enabled=False
    )
    repo = Repository(scoped)
    app = create_app(config, repo)
    with TestClient(app) as client:
        worker = Worker(repo, app.state.media, config)
        yield client, repo, worker
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def new_session(client, recipe="spatial"):
    response = client.post("/api/sessions", json={"recipe": recipe, "mode": "replay"})
    assert response.status_code == 201, response.text
    data = response.json()
    return data["session"], {"Authorization": f"Bearer {data['token']}"}


def replay(client, session, auth, scenario, key=None):
    response = client.post(
        f"/api/sessions/{session['id']}/replay",
        headers=auth,
        json={
            "scenario": scenario,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "idempotency_key": key or str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_complete_pair_archive_has_exact_hashed_evidence(system, recipe):
    client, repo, worker = system
    session, auth = new_session(client, recipe)
    for scenario, state in [("baseline", "baseline_ready"), ("counterpart", "ready")]:
        replay(client, session, auth, scenario)
        assert worker.once()
        session = repo.snapshot(session["id"])
        assert session["state"] == state, session["latest_capture"]
    url = f"/api/sessions/{session['id']}"
    body = {"expected_revision": session["revision"]}
    first = client.post(url + "/publish", headers=auth, json=body)
    assert first.status_code == 200, first.text
    second = client.post(url + "/publish", headers=auth, json=body)
    assert first.json()["pair"]["id"] == second.json()["pair"]["id"]
    archive = client.get(url + "/archive", headers=auth)
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["mode"] == "replay"
        assert manifest["split"] in {"train", "valid", "test"}
        assert manifest["split_group"] == session["id"]
        assert "fixture" in json.dumps(manifest)
        examples = [json.loads(line) for line in zf.read("examples.jsonl").decode().splitlines()]
        assert len(examples) == 2
        assert examples[0]["answer"] != examples[1]["answer"]
        assert examples[0]["split"] == examples[1]["split"] == manifest["split"]
        assert all(row["human_reviewed"] is False for row in examples)
        assert all(row["evidence_source"] == "fixture_replay" for row in examples)
        for slot in ("baseline", "counterpart"):
            for frame in manifest[slot]["frames"]:
                raw = zf.read("assets/" + frame["asset_id"])
                assert hashlib.sha256(raw).hexdigest() == frame["asset_id"]
                assert frame["detections"]
    assert not worker.once()


def test_targeted_repair_and_reconnect_preserves_first_capture(system):
    client, repo, worker = system
    session, auth = new_session(client)
    replay(client, session, auth, "baseline")
    worker.once()
    session = repo.snapshot(session["id"])
    baseline = session["baseline"]["id"]
    replay(client, session, auth, "camera_moved")
    worker.once()
    session = repo.snapshot(session["id"])
    assert session["latest_capture"]["status"] == "rejected"
    assert session["baseline"]["id"] == baseline
    url = f"/api/sessions/{session['id']}"
    resumed = client.post(url + "/resume", headers=auth, json={"expected_epoch": session["epoch"]})
    session = resumed.json()
    assert session["recovery_required"]
    blocked = client.post(
        url + "/replay",
        headers=auth,
        json={
            "scenario": "counterpart",
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "idempotency_key": str(uuid4()),
        },
    )
    assert blocked.status_code == 409
    replay(client, session, auth, "recovery")
    worker.once()
    session = repo.snapshot(session["id"])
    assert not session["recovery_required"]
    replay(client, session, auth, "counterpart")
    worker.once()
    assert repo.snapshot(session["id"])["state"] == "ready"


def test_capability_and_cross_session_media_isolation(system):
    client, repo, worker = system
    a, auth_a = new_session(client)
    b, auth_b = new_session(client)
    replay(client, a, auth_a, "baseline")
    worker.once()
    frame = repo.snapshot(a["id"])["baseline"]["frames"][0]
    url = f"/api/sessions/{a['id']}"
    assert client.get(url).status_code == 404
    assert client.get(url, headers=auth_b).status_code == 404
    assert client.get(url + "/assets/" + frame["asset_id"], headers=auth_a).status_code == 200
    assert (
        client.get(f"/api/sessions/{b['id']}/assets/{frame['asset_id']}", headers=auth_b).status_code == 404
    )
    assert client.get("/api/sessions/not-a-uuid", headers=auth_a).status_code == 404
    assert "capability_hash" not in client.get(url, headers=auth_a).text


def test_http_retry_is_idempotent_and_mismatch_rejected(system):
    client, repo, worker = system
    session, auth = new_session(client)
    key = str(uuid4())
    first = replay(client, session, auth, "baseline", key)
    second = replay(client, session, auth, "baseline", key)
    assert first["capture"]["id"] == second["capture"]["id"]
    changed = client.post(
        f"/api/sessions/{session['id']}/replay",
        headers=auth,
        json={
            "scenario": "counterpart",
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "idempotency_key": key,
        },
    )
    assert changed.status_code == 409
    assert worker.once()
    assert not worker.once()


def test_live_disabled_is_explicit_and_no_synthetic_fallback(system):
    client, repo, worker = system
    response = client.post("/api/sessions", json={"mode": "live"})
    assert response.status_code == 503
    assert not worker.once()


def test_preview_cannot_publish_a_pair(system):
    client, repo, worker = system
    session, auth = new_session(client)
    response = client.post(
        f"/api/sessions/{session['id']}/publish", headers=auth, json={"expected_revision": 0}
    )
    assert response.status_code == 409
