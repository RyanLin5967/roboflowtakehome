"""Operator/collector trust-boundary tests; real PostgreSQL, no model/provider calls."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.api import create_app
from counterpart.config import Settings
from counterpart.repository import Repository
from counterpart.security import COOKIE, OperatorAuth, OriginGuard
from counterpart.worker import Worker

OPERATOR_TOKEN = "test-only-operator-token-never-used-for-a-provider"
ORIGIN = "https://testserver"


@pytest.fixture
def system(tmp_path):
    dsn = os.environ.get("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "test_operator_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(dsn, options=f"-c search_path={schema}")
    config = Settings(
        database_url=scoped,
        media_dir=tmp_path / "media",
        web_dir=tmp_path / "missing-web",
        credentials_file=tmp_path / "missing-provider-credentials.json",
        live_enabled=False,
        require_operator=True,
        operator_token=OPERATOR_TOKEN,
        operator_cookie_secure=True,
        allowed_origins=[ORIGIN],
        trusted_hosts=["testserver"],
    )
    repo = Repository(scoped)
    app = create_app(config, repo)
    try:
        with TestClient(app, base_url=ORIGIN) as client:
            yield client, repo, Worker(repo, app.state.media, config), app, config
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def security_app(config: Settings):
    auth = OperatorAuth(config)
    app = FastAPI()
    app.add_middleware(OriginGuard, allowed_origins=config.allowed_origins)

    @app.post("/issue")
    def issue():
        return auth.response()

    @app.post("/api/protected")
    def protected(request: Request):
        return {"actor": auth.require(request)}

    @app.get("/api/state")
    def state():
        return {"ok": True}

    return app, auth


def test_public_operator_auth_requires_a_strong_server_secret():
    with pytest.raises(ValueError):
        OperatorAuth(Settings(require_operator=True, operator_token="short"))


def test_operator_cookie_is_signed_expiring_and_server_only(monkeypatch):
    config = Settings(
        require_operator=True,
        operator_token=OPERATOR_TOKEN,
        operator_cookie_secure=True,
        allowed_origins=[ORIGIN],
        operator_cookie_ttl=60,
    )
    app, auth = security_app(config)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/api/protected").status_code == 401
        response = client.post("/issue", headers={"Origin": ORIGIN})
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
        assert OPERATOR_TOKEN not in cookie
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/protected", headers={"Origin": ORIGIN}).status_code == 200
        signed = client.cookies.get(COOKIE)
        assert auth.valid(signed)
        assert not auth.valid(signed[:-1] + ("0" if signed[-1] != "0" else "1"))
        assert not auth.valid("9" * 500)
        assert not auth.valid("not.a.cookie")
        expires = int(signed.split(".")[0])
        monkeypatch.setattr("counterpart.security.time.time", lambda: expires + 1)
        assert not auth.valid(signed)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_site_mutation_is_rejected_before_handler(method, headers):
    app, _ = security_app(Settings(allowed_origins=[ORIGIN]))
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.request(method, "/api/protected", headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "origin_rejected"


def test_api_responses_have_browser_isolation_and_no_cache_headers():
    app, _ = security_app(Settings(allowed_origins=[ORIGIN]))
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/state")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["permissions-policy"] == "camera=(self), microphone=()"


def test_public_mode_never_grants_local_bootstrap_to_loopback_proxy_peer():
    auth = OperatorAuth(
        Settings(
            require_operator=True,
            operator_token=OPERATOR_TOKEN,
            operator_cookie_secure=True,
            allowed_origins=[ORIGIN],
        )
    )
    request = Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []})
    assert auth.local(request) is False


def test_login_rate_limit_is_applied_before_accepting_even_valid_token():
    auth = OperatorAuth(
        Settings(
            require_operator=True,
            operator_token=OPERATOR_TOKEN,
            operator_cookie_secure=True,
            allowed_origins=[ORIGIN],
        )
    )
    request = Request({"type": "http", "client": ("192.0.2.1", 1234), "headers": []})
    for _ in range(5):
        with pytest.raises(HTTPException) as error:
            auth.verify_login("incorrect", request)
        assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        auth.verify_login(OPERATOR_TOKEN, request)
    assert error.value.status_code == 429


@pytest.mark.parametrize(
    "configuration",
    [
        {"operator_cookie_secure": False, "allowed_origins": [ORIGIN]},
        {"operator_cookie_secure": True, "allowed_origins": ["http://public.example"]},
    ],
)
def test_public_mode_fails_closed_for_insecure_cookie_or_origin(configuration):
    with pytest.raises(ValueError):
        OperatorAuth(Settings(require_operator=True, operator_token=OPERATOR_TOKEN, **configuration))


def test_new_login_clients_cannot_reset_existing_rate_limit(monkeypatch):
    auth = OperatorAuth(Settings(operator_token=OPERATOR_TOKEN))
    monkeypatch.setattr("counterpart.security.time.monotonic", lambda: 1000)
    original = Request({"type": "http", "client": ("original", 1234), "headers": []})
    for _ in range(5):
        with pytest.raises(HTTPException):
            auth.verify_login("incorrect", original)
    for index in range(1025):
        other = Request({"type": "http", "client": (f"other-{index}", 1234), "headers": []})
        with pytest.raises(HTTPException):
            auth.verify_login("incorrect", other)
    with pytest.raises(HTTPException) as error:
        auth.verify_login(OPERATOR_TOKEN, original)
    assert error.value.status_code == 429
    monkeypatch.setattr("counterpart.security.time.monotonic", lambda: 1061)
    auth.verify_login(OPERATOR_TOKEN, original)


def login(client):
    response = client.post("/api/operator/login", json={"token": OPERATOR_TOKEN}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response


def campaign(client, *, mode="replay", recipe="spatial", target=20):
    response = client.post(
        "/api/operator/campaigns",
        json={
            "name": "Independently tested collection",
            "mode": mode,
            "recipe": recipe,
            "target_pairs": target,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def invitation(client, collection, *, maximum=20):
    response = client.post(
        f"/api/operator/campaigns/{collection['id']}/invites",
        json={"label": "Owned test capture", "max_sessions": maximum, "expires_in_hours": 1},
    )
    assert response.status_code == 201, response.text
    return response.json()


def join(client, invite, key=None):
    return client.post(
        "/api/invites/sessions",
        json={"token": invite["token"], "idempotency_key": key or uuid4().hex},
    )


def completed_pair(client, repo, worker, invite):
    joined = join(client, invite)
    assert joined.status_code == 201, joined.text
    credentials = joined.json()
    session = credentials["session"]
    headers = {"Authorization": f"Bearer {credentials['token']}"}
    for scenario in ("baseline", "counterpart"):
        response = client.post(
            f"/api/sessions/{session['id']}/replay",
            headers=headers,
            json={
                "scenario": scenario,
                "expected_revision": session["revision"],
                "epoch": session["epoch"],
                "idempotency_key": uuid4().hex,
            },
        )
        assert response.status_code == 202, response.text
        assert worker.once()
        session = repo.snapshot(session["id"])
        assert session["latest_capture"]["status"] == "accepted", (
            session["latest_capture"].get("error"),
            session["latest_capture"].get("result"),
        )
    response = client.post(
        f"/api/sessions/{session['id']}/publish",
        headers=headers,
        json={"expected_revision": session["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["pair"], credentials


def review(client, collection, pair, *, decision="approved", version=0, key=None):
    return client.post(
        f"/api/operator/campaigns/{collection['id']}/pairs/{pair['id']}/review",
        json={
            "decision": decision,
            "expected_version": version,
            "reason": "Manual fixture review; not a physical capture validation.",
            "idempotency_key": key or uuid4().hex,
        },
    )


def test_public_api_blocks_anonymous_creation_operator_access_and_local_bootstrap(system):
    client, repo, _, _, _ = system
    assert client.get("/api/operator/session").json() == {"authenticated": False, "local_mode": False}
    assert client.post("/api/operator/bootstrap").status_code == 403
    assert client.get("/api/operator/campaigns").status_code == 401
    response = client.post("/api/sessions", json={"mode": "replay"})
    assert response.status_code == 401
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 0
    assert client.post("/api/operator/login", json={"token": "wrong"}).status_code == 401
    login(client)
    assert client.get("/api/operator/session").json()["authenticated"] is True
    assert client.get("/api/operator/campaigns").status_code == 200
    assert client.post("/api/operator/logout").status_code == 200
    assert client.get("/api/operator/campaigns").status_code == 401


def test_real_api_rejects_foreign_origin_and_untrusted_host_before_mutation(system):
    client, repo, _, _, _ = system
    login(client)
    response = client.post(
        "/api/operator/campaigns",
        json={"name": "CSRF attempt", "mode": "replay"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/operator/campaigns").json()["campaigns"] == []
    assert client.get("/api/operator/session", headers={"Host": "attacker.example"}).status_code == 400
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_campaigns").fetchone()["n"] == 0


def test_collector_invite_is_idempotent_quota_bounded_and_not_operator_authority(system):
    client, _, _, app, _ = system
    login(client)
    collection = campaign(client)
    invite = invitation(client, collection, maximum=1)
    with TestClient(app, base_url=ORIGIN) as collector:
        info = collector.post("/api/invites/info", json={"token": invite["token"]})
        assert info.status_code == 200
        assert info.json()["accepting"] is True
        assert "token_hash" not in info.text
        first = join(collector, invite, "same-join-key")
        retried = join(collector, invite, "same-join-key")
        assert first.status_code == retried.status_code == 201
        assert first.json() == retried.json()
        assert join(collector, invite, "second-join-key").status_code == 409
        credentials = first.json()
        headers = {"Authorization": f"Bearer {credentials['token']}"}
        assert (
            collector.get(f"/api/sessions/{credentials['session']['id']}", headers=headers).status_code == 200
        )
        assert collector.get("/api/operator/campaigns", headers=headers).status_code == 401
        assert (
            collector.post("/api/operator/campaigns", headers=headers, json={"name": "attack"}).status_code
            == 401
        )
    listed = client.get(f"/api/operator/campaigns/{collection['id']}/invites")
    assert listed.json()["invites"][0]["sessions_created"] == 1
    assert invite["token"] not in listed.text
    assert "token_hash" not in listed.text


def test_invite_quota_survives_concurrent_http_joins(system):
    client, repo, _, _, _ = system
    login(client)
    collection = campaign(client)
    invite = invitation(client, collection, maximum=2)
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: join(client, invite), range(12)))
    assert sum(response.status_code == 201 for response in responses) == 2
    assert all(response.status_code in {201, 409} for response in responses)
    with repo._connection() as connection:
        count = connection.execute(
            "SELECT count(*) AS n FROM counterpart_sessions WHERE campaign_id=%s", (collection["id"],)
        ).fetchone()["n"]
    assert count == 2


def test_invite_revocation_blocks_new_joins_but_preserves_existing_capture_access(system):
    client, repo, _, _, _ = system
    login(client)
    collection = campaign(client)
    invite = invitation(client, collection)
    created = join(client, invite).json()
    path = f"/api/operator/campaigns/{collection['id']}/invites/{invite['id']}/revoke"
    assert client.post(path).status_code == 200
    assert client.post(path).status_code == 200
    assert join(client, invite).status_code == 409
    info = client.post("/api/invites/info", json={"token": invite["token"]}).json()
    assert info["accepting"] is False
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {created['token']}"}
    # Invitations admit new sessions. Revocation deliberately does not destroy
    # already-issued session capabilities or a collector's accepted evidence.
    assert client.get(f"/api/sessions/{created['session']['id']}", headers=headers).status_code == 200
    assert repo.authenticate(created["session"]["id"], created["token"])


def test_expired_invitation_cannot_admit_new_work(system):
    client, repo, _, _, _ = system
    login(client)
    collection = campaign(client)
    invite = invitation(client, collection)
    with repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_collection_invites SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",
            (invite["id"],),
        )
    assert join(client, invite).status_code == 409
    assert client.post("/api/invites/info", json={"token": invite["token"]}).json()["accepting"] is False


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_operator_collect_review_release_download_journey_preserves_truth_and_bytes(system, recipe):
    client, repo, worker, _, _ = system
    login(client)
    collection = campaign(client, recipe=recipe)
    invite = invitation(client, collection)
    pair, _ = completed_pair(client, repo, worker, invite)
    base = f"/api/operator/campaigns/{collection['id']}"
    assert client.get(base + "/sessions").json()["sessions"][0]["state"] == "published"
    assert client.get(base + "/pairs").json()["pairs"][0]["review"]["status"] == "pending"
    request = {"pair_ids": [pair["id"]], "include_replay": True, "idempotency_key": uuid4().hex}
    assert client.post(base + "/releases", json=request).status_code == 409
    approval = review(client, collection, pair)
    assert approval.status_code == 200, approval.text
    assert approval.json()["reviewer"] == "workspace-operator"
    released = client.post(base + "/releases", json=request)
    assert released.status_code == 201, released.text
    release = released.json()
    assert client.post(base + "/releases", json=request).json() == release
    assert release["pair_count"] == 1 and release["evidence_kind"] == "fixture_replay"
    assert release["pairs"][0]["manifest"]["provenance"]["human_reviewed"] is False
    assert release["pairs"][0]["review"]["status"] == "approved"
    archive = client.get(f"/api/operator/releases/{release['id']}/archive")
    assert archive.status_code == 200, archive.text
    assert len(archive.content) == int(archive.headers["content-length"])
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert json.loads(bundle.read("manifest.json")) == release
        examples = [json.loads(line) for line in bundle.read("examples.jsonl").splitlines()]
        assert len(examples) == 2
        assert examples[0]["answer"] != examples[1]["answer"]
        assert examples[0]["split"] == examples[1]["split"]
        assert all(row["human_reviewed"] is True for row in examples)
        assert all(row["evidence_source"] == "fixture_replay" for row in examples)
        for row in examples:
            for frame in row["media"]:
                assert hashlib.sha256(bundle.read(frame["path"])).hexdigest() == frame["sha256"]
    # A later rejection changes current eligibility, never a released artifact.
    assert review(client, collection, pair, decision="rejected", version=1).status_code == 200
    stored = client.get(f"/api/operator/releases/{release['id']}/manifest").json()
    assert stored == release
    assert (
        client.post(base + "/releases", json={**request, "idempotency_key": uuid4().hex}).status_code == 409
    )
    assert repo.snapshot(pair["session_id"])["pair"]["manifest"] == pair["manifest"]


def test_review_actor_cannot_be_spoofed_and_stale_reviews_do_not_overwrite(system):
    client, repo, worker, _, _ = system
    login(client)
    collection = campaign(client)
    pair, _ = completed_pair(client, repo, worker, invitation(client, collection))
    url = f"/api/operator/campaigns/{collection['id']}/pairs/{pair['id']}/review"
    forged = client.post(
        url,
        json={
            "decision": "approved",
            "expected_version": 0,
            "idempotency_key": uuid4().hex,
            "reviewer": "independent-external-expert",
        },
    )
    assert forged.status_code == 422
    key = uuid4().hex
    accepted = review(client, collection, pair, key=key)
    assert accepted.status_code == 200
    assert review(client, collection, pair, key=key).json() == accepted.json()
    assert review(client, collection, pair, decision="rejected", version=0).status_code == 409
    history = client.get(url + "s").json()["reviews"]
    assert len(history) == 1 and history[0]["reviewer"] == "workspace-operator"


def test_pair_review_media_and_release_membership_require_exact_collection(system):
    client, repo, worker, _, _ = system
    login(client)
    first, second = campaign(client), campaign(client)
    pair, credentials = completed_pair(client, repo, worker, invitation(client, first))
    assert review(client, second, pair).status_code == 404
    assert client.get(f"/api/operator/pairs/{pair['id']}/assets/{'0' * 64}").status_code == 404
    assert review(client, first, pair).status_code == 200
    response = client.post(
        f"/api/operator/campaigns/{second['id']}/releases",
        json={"pair_ids": [pair["id"]], "include_replay": True, "idempotency_key": uuid4().hex},
    )
    assert response.status_code == 409
    snapshot = client.get(f"/api/operator/sessions/{pair['session_id']}")
    assert snapshot.status_code == 200
    assert credentials["token"] not in snapshot.text
    assert "capability_hash" not in snapshot.text
    assert len(snapshot.json()["history"]) == 2


def test_replay_release_requires_explicit_label_and_never_becomes_live_export(system):
    client, repo, worker, app, _ = system
    login(client)
    collection = campaign(client)
    pair, _ = completed_pair(client, repo, worker, invitation(client, collection))
    assert review(client, collection, pair).status_code == 200
    base = f"/api/operator/campaigns/{collection['id']}"
    assert (
        client.post(
            base + "/releases", json={"pair_ids": [pair["id"]], "idempotency_key": uuid4().hex}
        ).status_code
        == 409
    )

    class Destination:
        def resolve(self, *, force=False):
            assert force
            return {"workspace": "test", "project": "private", "visibility": "private"}

    app.state.destination = Destination()
    response = client.post(base + "/exports", json={"pair_ids": [pair["id"]]})
    assert response.status_code == 409
    with repo._connection() as connection:
        row = connection.execute(
            "SELECT status,destination FROM counterpart_export_intents WHERE pair_id=%s", (pair["id"],)
        ).fetchone()
        assert row["status"] == "disabled" and row["destination"] is None


def test_export_destination_is_resolved_server_side_not_from_browser_privacy_assertion(system):
    from counterpart.destination import DestinationError

    client, _, _, app, _ = system
    login(client)
    collection = campaign(client)
    base = f"/api/operator/campaigns/{collection['id']}"
    calls = []

    class PublicDestination:
        def resolve(self, *, force=False):
            calls.append(force)
            raise DestinationError("private_destination_required")

    app.state.destination = PublicDestination()
    response = client.get("/api/operator/destination")
    assert response.json() == {"enabled": False, "reason": "private_destination_required"}
    body = {"pair_ids": [str(uuid4())], "destination_id": "configured"}
    rejected = client.post(base + "/exports", json=body)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "private_destination_required"
    assert calls == [False, True]
    forged = client.post(base + "/exports", json={**body, "visibility": "private", "project": "injected"})
    assert forged.status_code == 422
    assert calls == [False, True]


@pytest.mark.parametrize(
    ("origins", "hosts"),
    [
        (["http://public.example"], ["public.example"]),
        (["http://127.0.0.1:8080"], ["*"]),
        (["http://127.0.0.1:8080"], ["127.0.0.1", "public.example"]),
        (["http://localhost.attacker.example"], ["localhost"]),
        ([], ["localhost"]),
    ],
)
def test_explicit_local_http_exception_rejects_nonloopback_origins_and_hosts(origins, hosts):
    with pytest.raises(ValueError):
        OperatorAuth(
            Settings(
                require_operator=True,
                operator_token=OPERATOR_TOKEN,
                operator_local_http=True,
                operator_cookie_secure=False,
                allowed_origins=origins,
                trusted_hosts=hosts,
            )
        )


def test_explicit_loopback_http_deployment_still_requires_operator_login():
    auth = OperatorAuth(
        Settings(
            require_operator=True,
            operator_token=OPERATOR_TOKEN,
            operator_local_http=True,
            operator_cookie_secure=False,
            allowed_origins=["http://127.0.0.1:8080"],
            trusted_hosts=["localhost", "127.0.0.1", "api"],
        )
    )
    request = Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []})
    assert auth.local(request) is False
    with pytest.raises(HTTPException) as error:
        auth.require(request)
    assert error.value.status_code == 401


def test_retry_saved_evidence_needs_its_capability_and_never_recaptures_pixels(system):
    client, repo, worker, _, _ = system
    login(client)
    collection = campaign(client)
    invite = invitation(client, collection)
    original = join(client, invite).json()
    other = join(client, invite).json()
    session = original["session"]
    original_auth = {"Authorization": f"Bearer {original['token']}"}
    queued = client.post(
        f"/api/sessions/{session['id']}/replay",
        headers=original_auth,
        json={"scenario": "baseline", "expected_revision": 0, "epoch": 0, "idempotency_key": uuid4().hex},
    ).json()
    claimed = repo.claim("test-failed-worker")
    assert repo.fail(claimed["id"], claimed["lease_token"], "provider_unavailable")
    current = repo.snapshot(session["id"])
    body = {
        "capture_id": queued["capture"]["id"],
        "expected_revision": current["revision"],
        "epoch": current["epoch"],
        "idempotency_key": uuid4().hex,
    }
    url = f"/api/sessions/{session['id']}/retry"
    unauthorized = client.post(url, headers={"Authorization": f"Bearer {other['token']}"}, json=body)
    assert unauthorized.status_code == 404
    retried = client.post(url, headers=original_auth, json=body)
    assert retried.status_code == 202, retried.text
    new = retried.json()["capture"]
    assert new["id"] != queued["capture"]["id"]
    assert new["frames"] == queued["capture"]["frames"]
    repeated = client.post(url, headers=original_auth, json=body)
    assert repeated.status_code == 202
    assert repeated.json()["capture"]["id"] == new["id"]
    assert worker.once()
    assert repo.snapshot(session["id"])["baseline"]["id"] == new["id"]
    assert not worker.once()


def live_test_environment(system, monkeypatch):
    """Synthetic detector and images exercise live HTTP plumbing without a network model call."""
    import random

    from PIL import Image, ImageDraw

    from counterpart.execution_contract import build_execution_contract
    from counterpart.fixtures import generate

    client, repo, _, app, config = system
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-only-unused-provider-key")
    config.live_enabled = True
    scenes = {slot: generate("spatial", slot)[0] for slot in ("baseline", "counterpart")}
    # Real v2 registration requires distributed background texture. Draw the
    # same deterministic texture in both fixtures, outside every object's box.
    # The verifier still processes the actual saved image bytes without mocks.
    boxes = [d["box"] for frame in scenes.values() for d in frame["detections"]]
    for frame in scenes.values():
        picture = Image.open(io.BytesIO(frame["bytes"])).convert("RGB")
        draw = ImageDraw.Draw(picture)
        rng = random.Random(8027)
        for x in range(36, frame["width"] - 36, 32):
            for y in range(94, frame["height"] - 70, 32):
                if any(
                    x + 20 > box[0] * frame["width"] - 20
                    and x < box[2] * frame["width"] + 20
                    and y + 20 > box[1] * frame["height"] - 20
                    and y < box[3] * frame["height"] + 20
                    for box in boxes
                ):
                    continue
                for dx in range(0, 20, 4):
                    for dy in range(0, 20, 4):
                        tone = rng.choice((35, 205))
                        draw.rectangle((x + dx, y + dy, x + dx + 3, y + dy + 3), fill=(tone,) * 3)
        output = io.BytesIO()
        picture.save(output, "JPEG", quality=95)
        frame["bytes"] = output.getvalue()
    observations = {hashlib.sha256(frame["bytes"]).hexdigest(): frame for frame in scenes.values()}

    class SyntheticDetector:
        model_id = "coco/39"

        def detect(self, image, width, height):
            frame = observations[hashlib.sha256(image).hexdigest()]
            return {
                "detections": frame["detections"],
                # Mimic the provider wire identity; this adapter only returns
                # generated observations and never makes a network request.
                "model": {"provider": "roboflow", "model_id": self.model_id},
            }

    detector = SyntheticDetector()
    worker = Worker(repo, app.state.media, config, detector=detector)
    # Pin the deliberately injected test adapter too; production API pins its
    # actual Roboflow adapter. Never weaken the worker's identity check for tests.
    monkeypatch.setattr(
        "counterpart.operator_api.build_execution_contract",
        lambda configuration, *, mode: build_execution_contract(
            configuration, mode=mode, detector=detector if mode == "live" else None
        ),
    )
    login(client)
    collection = campaign(client, mode="live")
    invite = invitation(client, collection)
    return collection, invite, worker, scenes


def completed_live_pair(system, invite, worker, scenes):
    client, repo, _, _, _ = system
    credentials = join(client, invite).json()
    session = credentials["session"]
    headers = {"Authorization": f"Bearer {credentials['token']}"}
    for slot, frame in scenes.items():
        submitted = client.post(
            f"/api/sessions/{session['id']}/captures",
            headers=headers,
            data={
                "metadata": json.dumps(
                    {
                        "slot": slot,
                        "expected_revision": session["revision"],
                        "epoch": session["epoch"],
                        "idempotency_key": uuid4().hex,
                        "timestamps_ms": [0],
                    }
                )
            },
            files=[("frames", ("fixture.jpg", frame["bytes"], "image/jpeg"))],
        )
        assert submitted.status_code == 202, submitted.text
        assert worker.once()
        session = repo.snapshot(session["id"])
        assert session["latest_capture"]["status"] == "accepted", (
            session["latest_capture"].get("error"),
            session["latest_capture"].get("result"),
        )
    published = client.post(
        f"/api/sessions/{session['id']}/publish",
        headers=headers,
        json={"expected_revision": session["revision"]},
    )
    assert published.status_code == 200, published.text
    return published.json()["pair"]


def test_live_pair_stays_local_until_explicit_approved_destination_bound_export(system, monkeypatch):
    client, repo, _, app, _ = system
    collection, invite, worker, scenes = live_test_environment(system, monkeypatch)
    pair = completed_live_pair(system, invite, worker, scenes)
    assert pair["export_status"] == "local_only"
    assert repo.export_claim("must-not-see-unrequested-camera") is None
    calls = []

    class VerifiedDestination:
        project = "selected-private-project"

        def resolve(self, *, force=False):
            calls.append(force)
            return {"workspace": "owned-workspace", "project": self.project, "visibility": "private"}

    destination = VerifiedDestination()
    app.state.destination = destination
    export_url = f"/api/operator/campaigns/{collection['id']}/exports"
    assert client.post(export_url, json={"pair_ids": [pair["id"]]}).status_code == 409
    assert repo.export_claim("review-still-required") is None
    assert review(client, collection, pair).status_code == 200
    response = client.post(export_url, json={"pair_ids": [pair["id"]]})
    assert response.status_code == 202, response.text
    assert all(calls), "Export admission must force a fresh server-side privacy check."
    frozen = response.json()["exports"][0]["destination"]
    destination.project = "different-private-project"
    conflict = client.post(export_url, json={"pair_ids": [pair["id"]]})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "export_destination_frozen"
    with repo._connection() as connection:
        intent = connection.execute(
            "SELECT status,destination FROM counterpart_export_intents WHERE pair_id=%s", (pair["id"],)
        ).fetchone()
    assert intent["status"] == "pending" and intent["destination"] == frozen


def test_export_batch_destination_conflict_cannot_partially_enqueue_earlier_pairs(system, monkeypatch):
    client, repo, _, app, _ = system
    collection, invite, worker, scenes = live_test_environment(system, monkeypatch)
    first = completed_live_pair(system, invite, worker, scenes)
    second = completed_live_pair(system, invite, worker, scenes)
    for pair in (first, second):
        assert review(client, collection, pair).status_code == 200

    class Destination:
        project = "first-private-project"

        def resolve(self, *, force=False):
            assert force
            return {"workspace": "owned-workspace", "project": self.project, "visibility": "private"}

    destination = Destination()
    app.state.destination = destination
    url = f"/api/operator/campaigns/{collection['id']}/exports"
    assert client.post(url, json={"pair_ids": [second["id"]]}).status_code == 202
    destination.project = "second-private-project"
    response = client.post(url, json={"pair_ids": [first["id"], second["id"]]})
    assert response.status_code == 409
    with repo._connection() as connection:
        first_intent = connection.execute(
            "SELECT status,destination FROM counterpart_export_intents WHERE pair_id=%s", (first["id"],)
        ).fetchone()
        second_intent = connection.execute(
            "SELECT status,destination FROM counterpart_export_intents WHERE pair_id=%s", (second["id"],)
        ).fetchone()
    assert first_intent == {"status": "disabled", "destination": None}
    assert second_intent["destination"]["project"] == "first-private-project"


def test_gc_maintenance_returns_retryable_503_without_writing_or_losing_request(system):
    from counterpart.lifecycle import _maintenance_guard

    client, repo, worker, app, _ = system
    login(client)
    collection = campaign(client)
    credentials = join(client, invitation(client, collection)).json()
    session = credentials["session"]
    headers = {"Authorization": f"Bearer {credentials['token']}"}
    body = {"scenario": "baseline", "expected_revision": 0, "epoch": 0, "idempotency_key": uuid4().hex}
    before = list(app.state.media.root.iterdir())
    with _maintenance_guard(repo.dsn):
        busy = client.post(f"/api/sessions/{session['id']}/replay", headers=headers, json=body)
    assert busy.status_code == 503
    assert busy.headers["retry-after"] == "5"
    assert busy.json()["detail"]["code"] == "maintenance_busy"
    assert list(app.state.media.root.iterdir()) == before
    assert repo.snapshot(session["id"])["latest_capture"] is None
    accepted = client.post(f"/api/sessions/{session['id']}/replay", headers=headers, json=body)
    assert accepted.status_code == 202
    assert worker.once()
    assert repo.snapshot(session["id"])["baseline"]["id"] == accepted.json()["capture"]["id"]


def test_archive_declared_size_refusal_happens_before_media_reads(system, monkeypatch):
    client, repo, worker, app, config = system
    login(client)
    collection = campaign(client)
    pair, credentials = completed_pair(client, repo, worker, invitation(client, collection))
    assert review(client, collection, pair).status_code == 200
    released = client.post(
        f"/api/operator/campaigns/{collection['id']}/releases",
        json={"pair_ids": [pair["id"]], "include_replay": True, "idempotency_key": uuid4().hex},
    ).json()
    config.max_archive_bytes = 1

    def must_not_read(_asset_id):
        pytest.fail("An oversized declared archive must be refused before storage reads.")

    monkeypatch.setattr(app.state.media, "read", must_not_read)
    operator_response = client.get(f"/api/operator/releases/{released['id']}/archive")
    collector_response = client.get(
        f"/api/sessions/{pair['session_id']}/archive",
        headers={"Authorization": f"Bearer {credentials['token']}"},
    )
    for response in (operator_response, collector_response):
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "archive_too_large"


def test_archive_limit_also_counts_zip_footer_and_directory(system):
    client, repo, worker, _, config = system
    login(client)
    collection = campaign(client)
    pair, credentials = completed_pair(client, repo, worker, invitation(client, collection))
    url = f"/api/sessions/{pair['session_id']}/archive"
    auth = {"Authorization": f"Bearer {credentials['token']}"}
    initial = client.get(url, headers=auth)
    assert initial.status_code == 200
    config.max_archive_bytes = len(initial.content) - 1
    bounded = client.get(url, headers=auth)
    assert bounded.status_code == 413, (
        f"Archive emitted {len(bounded.content)} > cap {config.max_archive_bytes}"
    )


def test_archive_storage_outage_is_sanitized_retryable_503(system, monkeypatch):
    from counterpart.object_storage import MediaUnavailable

    client, repo, worker, app, _ = system
    login(client)
    collection = campaign(client)
    pair, credentials = completed_pair(client, repo, worker, invitation(client, collection))

    def unavailable(_asset_id):
        raise MediaUnavailable("untrusted-storage-url-and-secret-must-not-escape")

    monkeypatch.setattr(app.state.media, "read", unavailable)
    response = client.get(
        f"/api/sessions/{pair['session_id']}/archive",
        headers={"Authorization": f"Bearer {credentials['token']}"},
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json()["detail"]["code"] == "storage_unavailable"
    assert "untrusted-storage" not in response.text


def test_campaign_pause_blocks_new_admission_before_media_but_keeps_receipts_and_readback(
    system, monkeypatch
):
    client, repo, worker, app, _ = system
    login(client)
    collection = campaign(client)
    invite = invitation(client, collection)
    credentials = join(client, invite, "existing-collector").json()
    session = credentials["session"]
    headers = {"Authorization": f"Bearer {credentials['token']}"}
    url = f"/api/sessions/{session['id']}/replay"
    baseline = {"scenario": "baseline", "expected_revision": 0, "epoch": 0, "idempotency_key": uuid4().hex}
    admitted = client.post(url, headers=headers, json=baseline)
    assert admitted.status_code == 202
    pause = f"/api/operator/campaigns/{collection['id']}/activity"
    assert client.post(pause, json={"active": False}).status_code == 200
    # Work already admitted may finish; pausing does not erase its immutable input.
    assert worker.once()
    saved = repo.snapshot(session["id"])
    assert saved["baseline"]["id"] == admitted.json()["capture"]["id"]
    assert join(client, invite).status_code == 409
    assert join(client, invite, "existing-collector").json()["token"] == credentials["token"]
    assert client.get(f"/api/sessions/{session['id']}", headers=headers).status_code == 200
    puts = []
    original_put = app.state.media.put

    def record_put(data):
        puts.append(hashlib.sha256(data).hexdigest())
        return original_put(data)

    monkeypatch.setattr(app.state.media, "put", record_put)
    second = {
        "scenario": "counterpart",
        "expected_revision": saved["revision"],
        "epoch": saved["epoch"],
        "idempotency_key": uuid4().hex,
    }
    denied = client.post(url, headers=headers, json=second)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "collection_paused"
    assert puts == [], "Routine paused admissions should stop before writing orphan media."
    repeated = client.post(url, headers=headers, json=baseline)
    assert repeated.status_code == 202
    assert repeated.json()["capture"]["id"] == admitted.json()["capture"]["id"]
    assert client.post(pause, json={"active": True}).status_code == 200
    assert client.post(url, headers=headers, json=second).status_code == 202
    assert worker.once()
    assert repo.snapshot(session["id"])["state"] == "ready"


def test_collector_receives_latest_review_feedback_without_rewriting_original_manifest(system):
    client, repo, worker, _, _ = system
    login(client)
    collection = campaign(client)
    pair, credentials = completed_pair(client, repo, worker, invitation(client, collection))
    url = f"/api/sessions/{pair['session_id']}"
    auth = {"Authorization": f"Bearer {credentials['token']}"}
    original = client.get(url + "/manifest", headers=auth).json()
    pending = client.get(url, headers=auth).json()
    assert pending["review"]["status"] == "pending"
    requested = review(client, collection, pair, decision="needs_revision")
    assert requested.status_code == 200
    feedback = client.get(url, headers=auth).json()
    assert feedback["review"]["status"] == "needs_revision"
    assert feedback["review"]["reason"] == requested.json()["reason"]
    assert feedback["review"]["version"] == 1
    assert feedback["pair"]["manifest"] == original
    assert client.get(url + "/manifest", headers=auth).json() == original
    assert original["provenance"]["human_reviewed"] is False


def test_operator_runtime_reports_measured_worker_registry_without_exposing_it_publicly(system):
    client, _, _, app, _ = system
    assert client.get("/api/operator/runtime").status_code == 401
    assert "metrics" not in client.get("/api/health").json()
    login(client)
    runtime = client.get("/api/operator/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["workers"]["active_workers"] == 0
    identity = app.state.inference.worker_started("explicit-test-registry-entry")
    assert client.get("/api/operator/runtime").json()["workers"]["active_workers"] == 1
    assert app.state.inference.worker_stopped("explicit-test-registry-entry", identity)
    assert client.get("/api/operator/runtime").json()["workers"]["active_workers"] == 0


def test_live_disabled_blocks_new_join_but_does_not_hide_an_already_admitted_join_receipt(
    system, monkeypatch
):
    client, repo, _, _, config = system
    _collection, invite, _worker, _scenes = live_test_environment(system, monkeypatch)
    first = join(client, invite, "recover-this-join-receipt")
    assert first.status_code == 201
    config.live_enabled = False
    blocked = join(client, invite, "brand-new-offline-join")
    assert blocked.status_code == 503
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 1
    recovered = join(client, invite, "recover-this-join-receipt")
    assert recovered.status_code == 201, recovered.text
    assert recovered.json() == first.json()


def test_scene_groups_are_operator_owned_and_collectors_cannot_override_assignment(system):
    client, repo, _, _, _ = system
    assert client.get("/api/operator/split-groups").status_code == 401
    assert client.post("/api/operator/split-groups", json={"name": "My scene"}).status_code == 401
    login(client)
    created = client.post(
        "/api/operator/split-groups", json={"name": "Desk by the window", "description": "One physical scene"}
    )
    assert created.status_code == 201, created.text
    group = created.json()
    assert client.get("/api/operator/split-groups").json()["split_groups"] == [group]
    collection = campaign(client)
    invitation = client.post(
        f"/api/operator/campaigns/{collection['id']}/invites", json={"split_group_id": group["id"]}
    )
    assert invitation.status_code == 201, invitation.text
    invite = invitation.json()
    assert invite["split_group_id"] == group["id"]
    client.post("/api/operator/logout")
    forged = client.post(
        "/api/invites/sessions",
        json={"token": invite["token"], "idempotency_key": uuid4().hex, "split_group_id": str(uuid4())},
    )
    assert forged.status_code == 422
    created = join(client, invite)
    assert created.status_code == 201, created.text
    saved = created.json()["session"]
    assert saved["split_group_snapshot"]["id"] == group["id"]
    assert saved["execution_contract"] == collection["execution_contract"]
    assert repo.snapshot(saved["id"])["split_group_snapshot"] == saved["split_group_snapshot"]
    assert client.get("/api/operator/split-groups").status_code == 401


def test_scene_group_assignment_rejects_unknown_ids_and_model_contract_cannot_be_client_supplied(system):
    client, _, _, _, _ = system
    login(client)
    collection = campaign(client)
    missing = client.post(
        f"/api/operator/campaigns/{collection['id']}/invites", json={"split_group_id": str(uuid4())}
    )
    assert missing.status_code == 404
    forged = client.post(
        "/api/operator/campaigns", json={"name": "Forged", "mode": "replay", "execution_contract": {}}
    )
    assert forged.status_code == 422
    assert client.get(f"/api/operator/campaigns/{collection['id']}/invites").json()["invites"] == []


def test_new_live_campaign_freezes_protected_objects_and_visual_recipe(system, monkeypatch):
    client, _, _, _, _ = system
    live_test_environment(system, monkeypatch)
    response = client.post(
        "/api/operator/campaigns",
        json={"name": "Protected desk", "mode": "live", "protected_objects": ["book", "mouse"]},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    snapshot = created["recipe_snapshot"]
    assert snapshot["recipe_version"] == "2.1.0"
    assert snapshot["protected_objects"] == ["book", "mouse"]
    joined = join(client, invitation(client, created)).json()["session"]
    assert joined["recipe_snapshot"] == snapshot
    forged = client.post(
        "/api/operator/campaigns",
        json={"name": "Invalid protected role", "mode": "live", "protected_objects": ["bottle"]},
    )
    assert forged.status_code == 422
    replay = client.post(
        "/api/operator/campaigns",
        json={"name": "Unsupported replay controls", "mode": "replay", "protected_objects": ["book"]},
    )
    assert replay.status_code == 422


def legacy_session(repo):
    """Insert precisely the historical NULL contract state; do not bypass a frozen pin."""
    from psycopg.types.json import Jsonb

    from counterpart.recipes import build_recipe_snapshot

    identifier = str(uuid4())
    with repo._connection() as connection:
        connection.execute(
            "INSERT INTO counterpart_sessions "
            "(id,capability_hash,recipe,mode,object_a,object_b,recipe_snapshot) "
            "VALUES (%s,'unused-test-capability','spatial','live','bottle','cup',%s)",
            (identifier, Jsonb(build_recipe_snapshot("spatial"))),
        )
    return repo.snapshot(identifier)


def test_execution_migration_requires_operator_and_refuses_unknown_historical_model(system):
    client, repo, _, _, _ = system
    saved = legacy_session(repo)
    url = f"/api/operator/sessions/{saved['id']}/execution-contract/migrate"
    body = {"expected_revision": 0, "reason": "Try deriving the original configuration"}
    assert client.post(url, json=body).status_code == 401
    login(client)
    missing = client.post(url, json=body)
    assert missing.status_code == 409, missing.text
    assert missing.json()["detail"]["code"] == "legacy_model_unknown"
    assert repo.snapshot(saved["id"])["execution_contract"] is None
    assert not any(
        event["kind"] == "execution_contract_migrated" for event in repo.snapshot(saved["id"])["events"]
    )
    assert client.post(url, json={**body, "execution_contract": {}}).status_code == 422
    assert client.post(url, json={**body, "expected_revision": True}).status_code == 422
    assert client.post(url, json={**body, "reason": ""}).status_code == 422


def test_execution_migration_existing_pin_is_idempotent_and_stale_revision_refused(system):
    client, repo, _, _, _ = system
    login(client)
    saved = join(client, invitation(client, campaign(client))).json()["session"]
    url = f"/api/operator/sessions/{saved['id']}/execution-contract/migrate"
    body = {"expected_revision": saved["revision"], "reason": "Check the already pinned model"}
    first = client.post(url, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["execution_contract"] == saved["execution_contract"]
    assert client.post(url, json=body).json() == first.json()
    stale = client.post(url, json={**body, "expected_revision": saved["revision"] + 1})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_revision"
    assert not any(
        event["kind"] == "execution_contract_migrated" for event in repo.snapshot(saved["id"])["events"]
    )


def test_execution_migration_derives_recorded_proof_and_preserves_observations(system):
    from counterpart.execution_contract import build_execution_contract

    client, repo, _, _, config = system
    saved = legacy_session(repo)
    recorded = build_execution_contract(config)
    source = {
        "asset_id": hashlib.sha256(b"generated-test-evidence").hexdigest(),
        "width": 640,
        "height": 480,
        "t_ms": 0,
    }
    repo.submit(
        saved["id"],
        {"slot": "baseline", "expected_revision": 0, "epoch": 0, "idempotency_key": uuid4().hex},
        [source],
        uuid4().hex,
    )
    job = repo.claim("generated-history-no-model-call")
    observed = {
        **source,
        "detections": [],
        "model": {"provider": "roboflow", "model_id": "coco/39"},
        "inference": {"configuration": recorded["configuration"], "configuration_digest": recorded["digest"]},
    }
    repo.finish(job["id"], job["lease_token"], {"accepted": True, "checks": [], "evidence": {}}, [observed])
    before = repo.evaluation_snapshot(saved["id"])
    current = repo.snapshot(saved["id"])
    config.roboflow_model = "coco/40"
    login(client)
    url = f"/api/operator/sessions/{saved['id']}/execution-contract/migrate"
    body = {"expected_revision": current["revision"], "reason": "Recover the model from saved evidence"}
    migrated = client.post(url, json=body)
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["execution_contract"] == recorded
    assert migrated.json()["execution_contract"] != build_execution_contract(config)
    assert client.post(url, json=body).json() == migrated.json()
    after = repo.evaluation_snapshot(saved["id"])
    assert before["captures"] == after["captures"] and before["pairs"] == after["pairs"]
    events = [event for event in migrated.json()["events"] if event["kind"] == "execution_contract_migrated"]
    assert len(events) == 1
    assert events[0]["data"]["reviewer"] == "workspace-operator"
    assert events[0]["data"]["reason"] == body["reason"]
    assert events[0]["data"]["source"] == "recorded_observations"
