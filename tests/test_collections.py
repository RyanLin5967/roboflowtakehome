"""Real PostgreSQL collection/review invariants; generated observations, no ML."""

from __future__ import annotations

import copy
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.collections import CollectionRepository
from counterpart.repository import Conflict, NotFound, Repository

pytestmark = pytest.mark.postgres


@pytest.fixture
def system():
    dsn = os.environ.get("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "collection_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    repository = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    repository.initialize()
    try:
        yield repository, CollectionRepository(repository)
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def collection(system, mode="live", target=20):
    return system[1].create_campaign("Desk collection", "spatial", mode, target_pairs=target)


def start(system, campaign=None):
    campaign = campaign or collection(system)
    invite = system[1].create_invite(campaign["id"])
    return system[1].create_session_from_invite(invite["token"], uuid4().hex)["session"]


def submit(repository, session, slot="baseline"):
    key = uuid4().hex
    frames = [
        {
            "asset_id": hashlib.sha256(key.encode()).hexdigest(),
            "width": 640,
            "height": 480,
            "t_ms": 0,
            "fixture_detections": [],
        }
    ]
    return repository.submit(
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


def accept(repository, session, slot="baseline", accepted=True):
    result = submit(repository, session, slot)
    job = repository.claim("generated-verifier-test")
    assert job["capture"]["id"] == result["capture"]["id"]
    frames = [
        {**frame, "model": {"provider": "roboflow", "model_id": "coco/39"}}
        for frame in job["capture"]["frames"]
    ]
    repository.finish(
        job["id"],
        job["lease_token"],
        {
            "accepted": accepted,
            "code": "fixture",
            "message": "Generated test evidence",
            "checks": [],
            "evidence": {},
            "verifier_version": "test-fixture",
        },
        frames,
    )
    return repository.snapshot(session["id"])


def publish(system, campaign=None):
    repo, _ = system
    saved = accept(repo, start(system, campaign))
    saved = accept(repo, saved, "counterpart")
    return repo.publish(saved["id"], saved["revision"])


def review(system, campaign, pair, decision="approved", version=0, key=None):
    return system[1].review_pair(
        campaign["id"],
        pair["id"],
        decision=decision,
        expected_version=version,
        reviewer="operator",
        reason="Independent human review",
        idempotency_key=key or uuid4().hex,
    )


def test_campaign_snapshot_is_frozen_and_progress_counts_reviewed_pairs(system):
    repo, collections = system
    campaign = collection(system, target=2)
    assert campaign["recipe_snapshot"]["recipe_version"] == "2.1.0"
    assert campaign["progress"]["remaining_pairs"] == 2
    pair = publish(system, campaign)
    snapshot = repo.snapshot(pair["session_id"])
    assert snapshot["recipe_snapshot"] == campaign["recipe_snapshot"] == pair["manifest"]["recipe_snapshot"]
    assert pair["manifest"]["campaign_id"] == campaign["id"]
    assert collections.get_campaign(campaign["id"])["progress"] == {
        "sessions": 1,
        "published_pairs": 1,
        "approved_pairs": 0,
        "rejected_pairs": 0,
        "needs_revision_pairs": 0,
        "pending_review_pairs": 1,
        "target_pairs": 2,
        "remaining_pairs": 2,
    }
    review(system, campaign, pair)
    current = collections.get_campaign(campaign["id"])["progress"]
    assert current["approved_pairs"] == 1 and current["remaining_pairs"] == 1
    assert collections.list_campaigns()[0]["id"] == campaign["id"]


def test_invite_hash_only_and_atomic_idempotent_join(system):
    repo, collections = system
    campaign = collection(system)
    invitation = collections.create_invite(campaign["id"], max_sessions=1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: collections.create_session_from_invite(invitation["token"], "lost-response-retry"),
                range(16),
            )
        )
    assert len({result["session"]["id"] for result in results}) == 1
    assert len({result["token"] for result in results}) == 1
    assert repo.authenticate(results[0]["session"]["id"], results[0]["token"])
    with repo._connection() as connection:
        row = connection.execute("SELECT * FROM counterpart_collection_invites").fetchone()
        assert row["token_hash"] == hashlib.sha256(invitation["token"].encode()).hexdigest()
        assert invitation["token"] not in str(row)
        assert row["sessions_created"] == 1
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 1
    assert "token" not in collections.list_invites(campaign["id"])[0]
    assert "token_hash" not in str(collections.invite_info(invitation["token"]))
    assert collections.invite_info(invitation["token"])["accepting"] is False


def test_concurrent_invite_quota_has_no_orphan_sessions(system):
    repo, collections = system
    campaign = collection(system)
    invitation = collections.create_invite(campaign["id"], max_sessions=3)

    def attempt(index):
        try:
            return collections.create_session_from_invite(invitation["token"], f"collector-{index}")
        except Conflict as error:
            assert error.code == "invite_quota_reached"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(16)))
    assert sum(result is not None for result in results) == 3
    assert len(collections.list_sessions(campaign["id"])) == 3
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 3
        assert (
            connection.execute("SELECT count(*) AS n FROM counterpart_invite_sessions").fetchone()["n"] == 3
        )


@pytest.mark.parametrize("method", ["expire", "revoke"])
def test_expired_or_revoked_invite_cannot_admit_or_recover_capability(system, method):
    repo, collections = system
    campaign = collection(system)
    invite = collections.create_invite(campaign["id"])
    saved = collections.create_session_from_invite(invite["token"], "already-created")
    if method == "expire":
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_collection_invites SET expires_at=clock_timestamp()-interval '1 second'"
            )
    else:
        collections.revoke_invite(campaign["id"], invite["id"])
    assert collections.invite_info(invite["token"])["accepting"] is False
    for key in ("already-created", "new-session-key"):
        with pytest.raises(Conflict, match="expired or was revoked"):
            collections.create_session_from_invite(invite["token"], key)
    # Revocation controls invitation admission, not existing session capabilities.
    assert repo.authenticate(saved["session"]["id"], saved["token"])


def test_collection_namespace_is_checked_for_invites_pairs_and_releases(system):
    _, collections = system
    a, b = collection(system), collection(system)
    invite = collections.create_invite(a["id"])
    with pytest.raises(NotFound):
        collections.revoke_invite(b["id"], invite["id"])
    pair = publish(system, a)
    with pytest.raises(NotFound):
        review(system, b, pair)
    with pytest.raises(NotFound):
        collections.review_history(b["id"], pair["id"])
    review(system, a, pair)
    release = collections.export_manifests(a["id"])
    with pytest.raises(NotFound):
        collections.get_release(b["id"], release["id"])
    with pytest.raises(Conflict, match="Every selected"):
        collections.export_manifests(b["id"], pair_ids=[pair["id"]])


def test_atomic_join_rolls_back_quota_and_session_on_recipe_failure(system):
    repo, collections = system
    campaign = collection(system)
    invite = collections.create_invite(campaign["id"])
    with repo._connection() as connection:
        connection.execute("UPDATE counterpart_campaigns SET recipe_snapshot='{}'::jsonb")
    with pytest.raises(Conflict):
        collections.create_session_from_invite(invite["token"], "valid-request-key")
    assert collections.list_sessions(campaign["id"]) == []
    assert collections.list_invites(campaign["id"])[0]["sessions_created"] == 0


def test_review_is_audit_separate_from_immutable_pair_and_idempotent(system):
    repo, collections = system
    campaign = collection(system)
    pair = publish(system, campaign)
    original = copy.deepcopy(pair["manifest"])
    first = review(system, campaign, pair, key="human-review-once")
    with ThreadPoolExecutor(max_workers=4) as pool:
        repeats = list(pool.map(lambda _: review(system, campaign, pair, key="human-review-once"), range(8)))
    assert all(item == first for item in repeats)
    assert len(collections.review_history(campaign["id"], pair["id"])) == 1
    second = review(system, campaign, pair, "needs_revision", version=1)
    assert second["version"] == 2
    assert repo.snapshot(pair["session_id"])["pair"]["manifest"] == original
    assert original["provenance"]["human_reviewed"] is False
    with pytest.raises(Conflict) as error:
        review(system, campaign, pair, "rejected", version=2, key="human-review-once")
    assert error.value.code == "idempotency_mismatch"
    assert collections.list_pairs(campaign["id"], "needs_revision")[0]["review"]["version"] == 2


def test_review_optimistic_concurrency_one_winner(system):
    _, collections = system
    campaign = collection(system)
    pair = publish(system, campaign)

    def attempt(index):
        try:
            return review(system, campaign, pair, "approved" if index % 2 else "rejected")
        except Conflict as error:
            assert error.code == "stale_review"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(12)))
    assert sum(item is not None for item in results) == 1
    assert len(collections.review_history(campaign["id"], pair["id"])) == 1


def test_target_counts_approved_not_machine_accepted_and_stops_new_admission(system):
    _, collections = system
    campaign = collection(system, target=1)
    pair = publish(system, campaign)
    invite = collections.create_invite(campaign["id"])
    assert collections.invite_info(invite["token"])["accepting"]
    review(system, campaign, pair)
    assert not collections.invite_info(invite["token"])["accepting"]
    with pytest.raises(Conflict, match="reviewed-pair target"):
        collections.create_session_from_invite(invite["token"], "new-session-key")
    review(system, campaign, pair, "rejected", version=1)
    assert collections.invite_info(invite["token"])["accepting"]


def test_release_is_frozen_reviewed_selection_and_preserves_splits(system):
    _, collections = system
    campaign = collection(system)
    first, second = publish(system, campaign), publish(system, campaign)
    review(system, campaign, first)
    with pytest.raises(Conflict, match="Every selected"):
        collections.export_manifests(campaign["id"], pair_ids=[first["id"], second["id"]])
    release = collections.export_manifests(campaign["id"])
    assert release["pair_count"] == 1 and len(release["pairs"]) == 1
    assert sum(release["split_counts"].values()) == 1
    manifest = release["pairs"][0]["manifest"]
    assert manifest["split_group"] == manifest["session_id"]
    assert manifest["split"] == first["manifest"]["split"]
    review(system, campaign, first, "rejected", version=1)
    assert collections.get_release(campaign["id"], release["id"]) == release
    assert len(collections.list_releases(campaign["id"])) == 1
    with pytest.raises(Conflict):
        collections.export_manifests(campaign["id"])


def test_replay_release_requires_opt_in_and_never_exports_to_real_provider(system):
    _, collections = system
    campaign = collection(system, "replay")
    pair = publish(system, campaign)
    review(system, campaign, pair)
    with pytest.raises(Conflict, match="Replay fixtures"):
        collections.export_manifests(campaign["id"])
    release = collections.export_manifests(campaign["id"], include_replay=True)
    assert release["evidence_kind"] == "fixture_replay"
    with pytest.raises(Conflict, match="approved live"):
        collections.request_export(
            pair["id"], {"workspace": "test", "project": "private", "visibility": "private"}, "operator"
        )


def test_local_publish_never_enqueues_cloud_export_and_requires_frozen_private_destination(system):
    repo, collections = system
    campaign = collection(system)
    pair = publish(system, campaign)
    assert pair["export_status"] == "local_only"
    assert repo.export_claim("not-authorized") is None
    destination = {"workspace": "test", "project": "private", "visibility": "private"}
    with pytest.raises(Conflict, match="approved live"):
        collections.request_export(pair["id"], destination, "operator")
    review(system, campaign, pair)
    with pytest.raises(Conflict, match="private Roboflow"):
        collections.request_export(pair["id"], {**destination, "visibility": "public"}, "operator")
    requested = collections.request_export(pair["id"], destination, "operator")
    assert requested == collections.request_export(pair["id"], destination, "operator")
    with pytest.raises(Conflict, match="cannot be changed"):
        collections.request_export(pair["id"], {**destination, "project": "other"}, "operator")
    job = repo.export_claim("explicit-export")
    assert job["destination"] == destination


def test_legacy_null_recipe_remains_explicit_and_no_snapshot_reconstruction(system):
    repo, _ = system
    saved = repo.create_session("spatial", "live", "bottle", "cup")["session"]
    with repo._connection() as connection:
        connection.execute("UPDATE counterpart_sessions SET recipe_snapshot=NULL WHERE id=%s", (saved["id"],))
    repo.initialize()
    evidence = repo.evaluation_snapshot(saved["id"])
    assert evidence["recipe_snapshot"] is None
    assert evidence["provenance"]["legacy_recipe_snapshot_missing"] is True


def test_evaluation_snapshot_includes_failed_rejected_and_superseded_attempts(system):
    repo, _ = system
    saved = start(system)
    saved = accept(repo, saved, accepted=False)
    failed = submit(repo, saved)
    job = repo.claim("temporary-failure")
    repo.fail(job["id"], job["lease_token"], "transport_failure")
    saved = repo.snapshot(saved["id"])
    replacement = submit(repo, saved)
    evidence = repo.evaluation_snapshot(saved["id"])
    assert evidence["schema_version"] == "counterpart.evaluation.session.v1"
    assert evidence["captures_complete"] is True
    assert [item["status"] for item in evidence["captures"]] == ["rejected", "failed", "queued"]
    assert evidence["captures"][1]["id"] == failed["capture"]["id"]
    assert evidence["captures"][2]["id"] == replacement["capture"]["id"]
    assert "fixture_detections" not in str(evidence)
    assert "capability_hash" not in str(evidence)


def test_retry_saved_failed_evidence_preserves_frames_epochs_and_original_attempt(system):
    repo, _ = system
    saved = start(system)
    failed = submit(repo, saved)
    job = repo.claim("transient-worker")
    repo.fail(job["id"], job["lease_token"], "transport_failure")
    resumed = repo.resume(saved["id"], 0)
    retried = repo.retry_capture(
        saved["id"], failed["capture"]["id"], resumed["revision"], resumed["epoch"], "retry-original"
    )
    assert retried["capture"]["id"] != failed["capture"]["id"]
    assert retried["capture"]["frames"] == failed["capture"]["frames"]
    assert retried["capture"]["epoch"] == 0 and resumed["epoch"] == 1
    again = repo.retry_capture(
        saved["id"], failed["capture"]["id"], resumed["revision"], resumed["epoch"], "retry-original"
    )
    assert again["capture"]["id"] == retried["capture"]["id"]
    retry_job = repo.claim("healthy-worker")
    repo.finish(retry_job["id"], retry_job["lease_token"], {"accepted": True}, retry_job["capture"]["frames"])
    current = repo.snapshot(saved["id"])
    assert current["baseline"]["id"] == retried["capture"]["id"] and current["recovery_required"] is True
    assert repo.evaluation_snapshot(saved["id"])["captures"][0]["status"] == "failed"


def test_retry_rejects_foreign_stale_rejected_and_epoch_mismatch(system):
    repo, _ = system
    a, b = start(system), start(system)
    attempted = submit(repo, a)
    job = repo.claim("failed")
    repo.fail(job["id"], job["lease_token"], "transport_failure")
    with pytest.raises(NotFound):
        repo.retry_capture(b["id"], attempted["capture"]["id"], 0, 0, "foreign-capture")
    with pytest.raises(Conflict) as error:
        repo.retry_capture(a["id"], attempted["capture"]["id"], 1, 1, "incorrect-epoch")
    assert error.value.code == "stale_epoch"
    newer = accept(repo, repo.snapshot(a["id"]), accepted=False)
    with pytest.raises(Conflict) as error:
        repo.retry_capture(a["id"], attempted["capture"]["id"], newer["revision"], 0, "stale-capture")
    assert error.value.code == "stale_capture"
    with pytest.raises(Conflict) as error:
        repo.retry_capture(a["id"], newer["latest_capture"]["id"], newer["revision"], 0, "rejected-capture")
    assert error.value.code == "capture_not_retryable"


@pytest.mark.parametrize(
    "values",
    [
        {"name": ""},
        {"target_pairs": 0},
        {"target_pairs": True},
        {"mode": "fake"},
        {"object_a": "helmet"},
        {"object_b": "bottle"},
        {"recipe": "invented"},
    ],
)
def test_invalid_campaign_contract_is_rejected(system, values):
    with pytest.raises(Conflict):
        system[1].create_campaign(**{"name": "Valid name", "recipe": "spatial", **values})


@pytest.mark.parametrize(
    "values",
    [
        {"max_sessions": 0},
        {"max_sessions": True},
        {"expires_in_hours": 0},
        {"expires_in_hours": 721},
        {"label": "x" * 121},
    ],
)
def test_invites_have_bounded_admission(system, values):
    campaign = collection(system)
    with pytest.raises(Conflict):
        system[1].create_invite(campaign["id"], **values)


def test_release_request_race_is_idempotent_and_content_hash_reproducible(system):
    _, collections = system
    campaign = collection(system)
    pair = publish(system, campaign)
    review(system, campaign, pair)

    def release(_):
        return collections.export_manifests(campaign["id"], idempotency_key="release-one-time")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(release, range(16)))
    assert len({item["id"] for item in results}) == 1
    assert len(collections.list_releases(campaign["id"])) == 1
    same_membership = collections.export_manifests(campaign["id"], idempotency_key="release-second")
    assert same_membership["id"] != results[0]["id"]
    assert same_membership["content_sha256"] == results[0]["content_sha256"]
    with pytest.raises(Conflict) as error:
        collections.export_manifests(
            campaign["id"], pair_ids=[pair["id"]], idempotency_key="release-one-time"
        )
    assert error.value.code == "idempotency_mismatch"
    review(system, campaign, pair, "rejected", version=1)
    assert release(None) == results[0]
    assert collections.get_release_by_id(results[0]["id"]) == results[0]
    assert collections.get_pair(pair["id"])["review"]["status"] == "rejected"


def test_replay_collection_rejects_unimplemented_fixture_classes(system):
    with pytest.raises(Conflict, match="original roles"):
        system[1].create_campaign("Fixture mismatch", "spatial", "replay", "book", "mouse")


def test_attempt_reference_survives_baseline_replacement(system):
    repo, _ = system
    initial = accept(repo, start(system))
    original_baseline = initial["baseline"]["id"]
    rejected = accept(repo, initial, "counterpart", accepted=False)
    rejected_id = rejected["latest_capture"]["id"]
    replaced = accept(repo, rejected, "baseline")
    assert replaced["baseline"]["id"] != original_baseline
    audit = repo.evaluation_snapshot(initial["id"])
    historical = {capture["id"]: capture for capture in audit["captures"]}
    assert historical[rejected_id]["reference_capture_id"] == original_baseline
    assert historical[original_baseline]["status"] == "accepted"
    assert historical[rejected_id]["status"] == "rejected"
    assert historical[rejected_id]["completed_at"] is not None


def test_saved_retry_has_explicit_original_capture_link(system):
    repo, _ = system
    saved = start(system)
    failed = submit(repo, saved)
    job = repo.claim("temporary")
    repo.fail(job["id"], job["lease_token"], "transport_failure")
    current = repo.snapshot(saved["id"])
    retried = repo.retry_capture(
        saved["id"], failed["capture"]["id"], current["revision"], 0, "explicit-retry-link"
    )
    assert retried["capture"]["retry_of_capture_id"] == failed["capture"]["id"]
    assert retried["session"]["latest_capture"]["retry_of_capture_id"] == failed["capture"]["id"]


def test_legacy_export_without_destination_is_never_claimed(system):
    repo, _ = system
    campaign = collection(system)
    pair = publish(system, campaign)
    with repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_export_intents SET status='pending' WHERE pair_id=%s", (pair["id"],)
        )
    assert repo.export_claim("legacy-global-toggle") is None


def test_pause_blocks_new_work_but_preserves_retries_readback_and_inflight_finish(system):
    repo, collections = system
    campaign = collection(system)
    invite = collections.create_invite(campaign["id"])
    created = collections.create_session_from_invite(invite["token"], "existing-collector")
    saved = created["session"]
    pending = submit(repo, saved)
    job = repo.claim("already-admitted")
    paused = collections.set_campaign_active(campaign["id"], False)
    assert paused["active"] is False
    assert not collections.invite_info(invite["token"])["accepting"]
    retried_join = collections.create_session_from_invite(invite["token"], "existing-collector")
    assert retried_join["token"] == created["token"]
    with pytest.raises(Conflict) as error:
        collections.create_session_from_invite(invite["token"], "new-collector-key")
    assert error.value.code == "collection_paused"
    assert repo.finish(job["id"], job["lease_token"], {"accepted": True}, job["capture"]["frames"])
    current = repo.snapshot(saved["id"])
    assert current["baseline"]["id"] == pending["capture"]["id"]
    with pytest.raises(Conflict) as error:
        submit(repo, current, "counterpart")
    assert error.value.code == "collection_paused"
    with pytest.raises(Conflict):
        collections.assert_collection_open(campaign["id"])
    assert collections.set_campaign_active(campaign["id"], True)["active"]
    collections.assert_collection_open(campaign["id"])
    assert submit(repo, current, "counterpart")["capture"]["status"] == "queued"


def test_pause_is_checked_inside_admission_transaction(system):
    import threading

    repo, collections = system
    campaign = collection(system)
    saved = start(system, campaign)
    started = threading.Event()

    def submit_while_pausing():
        started.set()
        try:
            submit(repo, saved)
        except Conflict as error:
            return error.code
        return "wrongly_admitted"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with repo._connection() as connection:
            connection.execute(
                "SELECT id FROM counterpart_campaigns WHERE id=%s FOR UPDATE", (campaign["id"],)
            )
            connection.execute("UPDATE counterpart_campaigns SET active=false WHERE id=%s", (campaign["id"],))
            pending = pool.submit(submit_while_pausing)
            assert started.wait(timeout=2)
        assert pending.result(timeout=5) == "collection_paused"
    assert repo.snapshot(saved["id"])["revision"] == 0
    assert repo.metrics()["jobs"] == {}


@pytest.mark.parametrize("same_partition", [True, False])
def test_release_exact_asset_leakage_gate_preserves_recorded_splits(system, same_partition):
    from counterpart.splits import split_for_session

    repo, collections = system
    campaign = collection(system)
    pairs = []
    for desired_split in ("train", "train" if same_partition else "test"):
        session_id = str(uuid4())
        while split_for_session(session_id) != desired_split:
            session_id = str(uuid4())
        with repo._connection() as connection:
            saved = repo._create_session(
                connection,
                "spatial",
                "live",
                "bottle",
                "cup",
                campaign_id=campaign["id"],
                session_id=session_id,
                recipe_snapshot=campaign["recipe_snapshot"],
            )["session"]
        for slot in ("baseline", "counterpart"):
            key = uuid4().hex
            asset = "a" * 64 if slot == "baseline" else hashlib.sha256(key.encode()).hexdigest()
            evidence = [{"asset_id": asset, "width": 640, "height": 480, "t_ms": 0}]
            repo.submit(
                saved["id"],
                {
                    "slot": slot,
                    "expected_revision": saved["revision"],
                    "epoch": 0,
                    "idempotency_key": key,
                },
                evidence,
                key,
            )
            job = repo.claim("test-fixture-not-ML")
            repo.finish(job["id"], job["lease_token"], {"accepted": True}, evidence)
            saved = repo.snapshot(saved["id"])
        pair = repo.publish(saved["id"], saved["revision"])
        assert pair["manifest"]["split"] == desired_split
        pairs.append(pair)
        review(system, campaign, pair)
    if same_partition:
        release = collections.export_manifests(campaign["id"])
        assert release["pair_count"] == 2
        assert collections.list_releases(campaign["id"])[0]["split_counts"] == {
            "train": 2,
            "valid": 0,
            "test": 0,
        }
    else:
        with pytest.raises(Conflict) as error:
            collections.export_manifests(campaign["id"])
        assert error.value.code == "split_leakage"
        assert collections.list_releases(campaign["id"]) == []
        assert [repo.snapshot(pair["session_id"])["pair"]["manifest"]["split"] for pair in pairs] == [
            "train",
            "test",
        ]


def test_named_scene_group_spans_campaigns_without_merging_display_names(system):
    from counterpart.splits import split_for_group

    repo, collections = system
    first, second = collection(system), collection(system)
    group = collections.create_split_group(" Kitchen table ", "Same room, lighting and physical objects")
    different = collections.create_split_group("Kitchen table")
    assert group["id"] != different["id"]
    assert group["name"] == different["name"] == "Kitchen table"
    assert len(collections.list_split_groups()) == 2
    collected = []
    for campaign in (first, second):
        invite = collections.create_invite(campaign["id"], split_group_id=group["id"])
        assert invite["split_group_id"] == group["id"]
        assert invite["split_group_name"] == group["name"]
        assert collections.list_invites(campaign["id"])[0]["split_group_id"] == group["id"]
        info = collections.invite_info(invite["token"])["invite"]
        assert info["split_group_id"] == group["id"]
        for _ in range(2):
            saved = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
            expected = {
                "schema_version": "counterpart.split-group.v1",
                "kind": "operator_group",
                "id": group["id"],
                "name": group["name"],
            }
            assert saved["split_group_snapshot"] == expected
            saved = accept(repo, accept(repo, saved), "counterpart")
            pair = repo.publish(saved["id"], saved["revision"])
            assert pair["manifest"]["split_group"] == group["id"]
            assert pair["manifest"]["split_provenance"] == expected
            assert pair["manifest"]["split"] == split_for_group(group["id"])
            review(system, campaign, pair)
            collected.append(pair)
        release = collections.export_manifests(campaign["id"])
        assert release["pair_count"] == 2
        assert release["split_counts"][split_for_group(group["id"])] == 2
    assert len({pair["session_id"] for pair in collected}) == 4
    assert len({pair["manifest"]["split"] for pair in collected}) == 1


def test_independent_invite_default_never_overgroups_a_campaign(system):
    _, collections = system
    campaign = collection(system)
    invitation = collections.create_invite(campaign["id"])
    assert invitation["split_group_id"] is None and invitation["split_group_name"] is None
    sessions = [
        collections.create_session_from_invite(invitation["token"], uuid4().hex)["session"] for _ in range(3)
    ]
    assert {s["split_group_snapshot"]["id"] for s in sessions} == {s["id"] for s in sessions}
    assert all(s["split_group_snapshot"]["kind"] == "session" for s in sessions)


def test_grouped_concurrent_lost_response_joins_preserve_assignment_and_quota(system):
    repo, collections = system
    campaign = collection(system)
    group = collections.create_split_group("Repeated captures in the same room")
    invitation = collections.create_invite(campaign["id"], max_sessions=4, split_group_id=group["id"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda index: collections.create_session_from_invite(
                    invitation["token"], f"same-attempt-{index % 4}"
                ),
                range(20),
            )
        )
    assert len({r["session"]["id"] for r in results}) == 4
    assert {r["session"]["split_group_snapshot"]["id"] for r in results} == {group["id"]}
    assert collections.list_invites(campaign["id"])[0]["sessions_created"] == 4
    with repo._connection() as connection:
        assert connection.execute("SELECT count(*) AS n FROM counterpart_sessions").fetchone()["n"] == 4


def test_session_and_invitation_group_assignment_cannot_be_retargeted(system):
    from psycopg.types.json import Jsonb

    repo, collections = system
    campaign = collection(system)
    first = collections.create_split_group("Original scene")
    other = collections.create_split_group("Another scene")
    invitation = collections.create_invite(campaign["id"], split_group_id=first["id"])
    saved = collections.create_session_from_invite(invitation["token"], uuid4().hex)["session"]
    original = copy.deepcopy(saved["split_group_snapshot"])
    saved = accept(repo, saved)
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"), repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_sessions SET split_group_snapshot=%s WHERE id=%s",
            (Jsonb({**original, "id": other["id"], "name": other["name"]}), saved["id"]),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"), repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_collection_invites SET split_group_id=%s WHERE id=%s",
            (other["id"], invitation["id"]),
        )
    assert repo.snapshot(saved["id"])["split_group_snapshot"] == original
    assert collections.revoke_invite(campaign["id"], invitation["id"])["split_group_id"] == first["id"]


@pytest.mark.parametrize("value", ["not-a-uuid", str(uuid4())])
def test_unknown_group_never_creates_an_invitation(system, value):
    _, collections = system
    campaign = collection(system)
    with pytest.raises(NotFound):
        collections.create_invite(campaign["id"], split_group_id=value)
    assert collections.list_invites(campaign["id"]) == []


def test_group_metadata_changes_cannot_rewrite_session_pair_or_release_provenance(system):
    repo, collections = system
    campaign = collection(system)
    group = collections.create_split_group("Frozen name")
    invitation = collections.create_invite(campaign["id"], split_group_id=group["id"])
    saved = collections.create_session_from_invite(invitation["token"], uuid4().hex)["session"]
    saved = accept(repo, accept(repo, saved), "counterpart")
    pair = repo.publish(saved["id"], saved["revision"])
    review(system, campaign, pair)
    release = collections.export_manifests(campaign["id"])
    # Simulate a future display-name editor. The exported meaning is immutable.
    with repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_split_groups SET name='Changed display' WHERE id=%s", (group["id"],)
        )
    repo.initialize()
    assert repo.snapshot(saved["id"])["split_group_snapshot"]["name"] == "Frozen name"
    assert collections.get_release(campaign["id"], release["id"]) == release
    assert collections.get_pair(pair["id"])["manifest"] == pair["manifest"]


def test_campaign_execution_contract_is_frozen_and_inherited_by_sessions(system):
    from psycopg.types.json import Jsonb

    repo, collections = system
    campaign = collection(system)
    session = start(system, campaign)
    assert session["execution_contract"] == campaign["execution_contract"]
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"), repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_campaigns SET execution_contract=%s WHERE id=%s",
            (Jsonb({}), campaign["id"]),
        )


def test_release_refuses_split_group_hash_mismatch_without_persisting_release(system):
    from psycopg.types.json import Jsonb

    repo, collections = system
    campaign = collection(system)
    pair = publish(system, campaign)
    review(system, campaign, pair)
    altered = copy.deepcopy(pair["manifest"])
    altered["split"] = "test" if altered["split"] != "test" else "train"
    with repo._connection() as connection:
        connection.execute(
            "UPDATE counterpart_pairs SET manifest=%s WHERE id=%s", (Jsonb(altered), pair["id"])
        )
    with pytest.raises(Conflict) as error:
        collections.export_manifests(campaign["id"])
    assert error.value.code == "invalid_split_provenance"
    assert collections.list_releases(campaign["id"]) == []


def test_remote_export_and_local_release_accept_identical_scene_group_provenance(system):
    from counterpart.exporting import InvalidExport, _assets, export_split

    repo, collections = system
    campaign = collection(system)
    group = collections.create_split_group("A scene reused across sessions")
    invite = collections.create_invite(campaign["id"], split_group_id=group["id"])
    saved = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
    saved = accept(repo, accept(repo, saved), "counterpart")
    pair = repo.publish(saved["id"], saved["revision"])
    manifest = copy.deepcopy(pair["manifest"])
    for slot in ("baseline", "counterpart"):
        for frame in manifest[slot]["frames"]:
            frame.pop("fixture_detections", None)
            frame["detections"] = []
            frame["media_type"] = "image/png"
    assert len(_assets(manifest, pair["id"])) == 2
    assert export_split(group["id"]) == manifest["split"]
    manifest["split_provenance"]["id"] = str(uuid4())
    with pytest.raises(InvalidExport):
        _assets(manifest, pair["id"])


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "client_group"},
        {"name": " "},
        {"id": "not-a-uuid"},
        {"id": "ABCD1234-0000-0000-0000-000000000000"},
        {"schema_version": "counterpart.split-group.v2"},
        {"extra": True},
    ],
)
def test_frozen_group_provenance_rejects_ambiguous_or_forged_shapes(change):
    from counterpart.splits import validate_split_group

    snapshot = {
        "schema_version": "counterpart.split-group.v1",
        "id": str(uuid4()),
        "kind": "operator_group",
        "name": "Desk scene",
    }
    with pytest.raises(ValueError):
        validate_split_group({**snapshot, **change}, str(uuid4()))


def test_legacy_null_group_projection_preserves_original_partition():
    from counterpart.splits import manifest_split, split_for_session

    session_id = str(uuid4())
    assert manifest_split({"id": session_id, "split_group_snapshot": None}) == {
        "split_group": session_id,
        "split": split_for_session(session_id),
    }
