"""Actual database/media maintenance and restore; no model compute or cloud I/O."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from PIL import Image
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from counterpart.collections import CollectionRepository
from counterpart.fixtures import generate
from counterpart.lifecycle import (
    LifecycleError,
    MaintenanceBusy,
    collect_orphan_media,
    create_backup,
    media_reference_guard,
    postgres_tool_environment,
    prune_previews,
    run_maintenance,
)
from counterpart.repository import Repository
from counterpart.storage import MediaStore
from counterpart.verification import verify

pytestmark = pytest.mark.postgres
NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=3)


@pytest.fixture
def repository():
    dsn = os.environ.get("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "cp_lifecycle_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    instance = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    instance.initialize()
    try:
        yield instance
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def media(tmp_path):
    return MediaStore(tmp_path / "media")


def asset(media, index=0, *, old=True):
    image = io.BytesIO()
    Image.new("RGB", (160, 120), (index % 256, (index // 256) % 256, 90)).save(image, format="PNG")
    value = media.put(image.getvalue())
    if old:
        os.utime(media.path(value["asset_id"]), (OLD.timestamp(), OLD.timestamp()))
    return value


def session(repository):
    return repository.create_session("spatial", "replay", "bottle", "cup")["session"]


def capture(
    repository,
    saved,
    value,
    *,
    slot="preview",
    status="superseded",
    job_status="superseded",
    created_at=OLD,
    completed_at=OLD,
):
    capture_id, job_id = str(uuid4()), str(uuid4())
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "INSERT INTO counterpart_captures "
            "(id,session_id,idempotency_key,fingerprint,slot,status,revision,epoch,frames,"
            "created_at,completed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s)",
            (
                capture_id,
                saved["id"],
                capture_id,
                capture_id,
                slot,
                status,
                Jsonb([{**value, "t_ms": 0}]),
                created_at,
                completed_at,
            ),
        )
        if job_status is not None:
            connection.execute(
                "INSERT INTO counterpart_jobs(id,capture_id,session_id,status) VALUES (%s,%s,%s,%s)",
                (job_id, capture_id, saved["id"], job_status),
            )
    return capture_id


def table_count(repository, table):
    with psycopg.connect(repository.dsn) as connection:
        return connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()[0]


def test_gc_defaults_to_dry_run_and_grace_preserves_new_uploads(repository, media):
    old, recent = asset(media), asset(media, 1, old=False)
    report = collect_orphan_media(repository.dsn, media, now=NOW)
    assert report["dry_run"]
    assert report["candidate_assets"] == [old["asset_id"]]
    assert report["protected_recent"] == 1
    assert report["deleted_assets"] == []
    assert media.read(old["asset_id"])
    applied = collect_orphan_media(repository.dsn, media, dry_run=False, now=NOW)
    assert applied["deleted_assets"] == [old["asset_id"]]
    assert media.read(recent["asset_id"])
    with pytest.raises(FileNotFoundError):
        media.read(old["asset_id"])


def test_all_evidence_roots_protect_original_bytes(repository, media):
    values = [asset(media, index) for index in range(6)]
    saved = session(repository)
    capture_id = capture(repository, saved, values[0], slot="baseline")
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_captures SET observed_frames=%s,result=%s WHERE id=%s",
            (Jsonb([values[1]]), Jsonb({"evidence": {"observations": [values[2]]}}), capture_id),
        )
        connection.execute(
            "INSERT INTO counterpart_pairs(id,session_id,manifest,export_status) "
            "VALUES (%s,%s,%s,'local_only')",
            (str(uuid4()), saved["id"], Jsonb({"baseline": {"frames": [values[3]]}})),
        )
        campaign_id = str(uuid4())
        connection.execute(
            "INSERT INTO counterpart_campaigns "
            "(id,name,recipe,mode,object_a,object_b,recipe_snapshot,target_pairs) "
            "SELECT %s,'Retention test',recipe,mode,object_a,object_b,recipe_snapshot,1 "
            "FROM counterpart_sessions WHERE id=%s",
            (campaign_id, saved["id"]),
        )
        connection.execute(
            "INSERT INTO counterpart_dataset_releases(id,campaign_id,manifest) VALUES (%s,%s,%s)",
            (str(uuid4()), campaign_id, Jsonb({"pairs": [{"manifest": {"frames": [values[4]]}}]})),
        )
    report = collect_orphan_media(repository.dsn, media, dry_run=False, now=NOW)
    assert report["protected_referenced"] == 5
    assert report["deleted_assets"] == [values[5]["asset_id"]]
    for value in values[:5]:
        assert media.read(value["asset_id"])


def test_prune_only_terminal_unpointed_previews_and_retains_committed_failures(repository, media):
    saved = session(repository)
    value = asset(media)
    removed = {
        capture(repository, saved, value, status=status)
        for status in ("accepted", "rejected", "failed", "superseded")
    }
    for slot in ("baseline", "counterpart", "recovery"):
        for status in ("accepted", "rejected", "failed", "superseded"):
            capture(repository, saved, value, slot=slot, status=status)
    for status, job_status in (("queued", "pending"), ("running", "running"), ("failed", "retry_wait")):
        capture(repository, saved, value, status=status, job_status=job_status)
    capture(repository, saved, value, completed_at=NOW)
    capture(repository, saved, value, created_at=NOW)
    for pointer in ("baseline_id", "counterpart_id", "latest_capture_id", "feedback_capture_id"):
        pointed = capture(repository, saved, value)
        with psycopg.connect(repository.dsn) as connection:
            connection.execute(
                sql.SQL("UPDATE counterpart_sessions SET {}=%s WHERE id=%s").format(sql.Identifier(pointer)),
                (pointed, saved["id"]),
            )
    before = table_count(repository, "counterpart_captures")
    dry = prune_previews(repository.dsn, now=NOW)
    assert set(dry["candidate_capture_ids"]) == removed
    assert table_count(repository, "counterpart_captures") == before
    applied = prune_previews(repository.dsn, dry_run=False, now=NOW)
    assert set(applied["deleted_capture_ids"]) == removed
    assert table_count(repository, "counterpart_captures") == before - len(removed)
    assert table_count(repository, "counterpart_jobs") == before - len(removed)
    with psycopg.connect(repository.dsn) as connection:
        event = connection.execute(
            "SELECT data FROM counterpart_events WHERE kind='previews_pruned'"
        ).fetchone()
    assert set(event[0]["capture_ids"]) == removed


def test_shared_media_survives_pruning_in_another_session(repository, media):
    shared = asset(media)
    capture(repository, session(repository), shared)
    capture(repository, session(repository), shared, slot="baseline", status="rejected")
    report = run_maintenance(repository.dsn, media, dry_run=False, now=NOW)
    assert len(report["previews"]["deleted_capture_ids"]) == 1
    assert report["media"]["deleted_assets"] == []
    assert media.read(shared["asset_id"])


def test_prune_transaction_rolls_back_jobs_when_capture_delete_fails(repository, media):
    capture(repository, session(repository), asset(media))
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "CREATE FUNCTION fail_preview_delete() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'injected failure'; END $$"
        )
        connection.execute(
            "CREATE TRIGGER test_fail_delete BEFORE DELETE ON counterpart_captures "
            "FOR EACH ROW EXECUTE FUNCTION fail_preview_delete()"
        )
    with pytest.raises(psycopg.errors.RaiseException):
        prune_previews(repository.dsn, dry_run=False, now=NOW)
    assert table_count(repository, "counterpart_jobs") == 1
    assert table_count(repository, "counterpart_captures") == 1


def test_inventory_cursor_reaches_orphans_after_referenced_prefix(repository, media):
    values = sorted([asset(media, index) for index in range(8)], key=lambda value: value["asset_id"])
    saved = session(repository)
    for value in values[:3]:
        capture(repository, saved, value, slot="baseline")
    cursor, deleted = None, []
    for _ in range(4):
        report = collect_orphan_media(
            repository.dsn, media, dry_run=False, limit=2, after_asset_id=cursor, now=NOW
        )
        assert report["scanned"] <= 2
        deleted.extend(report["deleted_assets"])
        cursor = report["next_cursor"]
        if not report["has_more"]:
            break
        assert cursor
    assert set(deleted) == {value["asset_id"] for value in values[3:]}
    assert cursor is None


def test_preview_pruning_is_bounded_per_run(repository, media):
    saved = session(repository)
    for index in range(8):
        capture(repository, saved, asset(media, index))
    assert len(prune_previews(repository.dsn, dry_run=False, limit=3, now=NOW)["deleted_capture_ids"]) == 3
    assert table_count(repository, "counterpart_captures") == 5


def test_shared_guard_protects_reused_old_orphan_before_db_reference(repository, media):
    value = asset(media)
    saved = session(repository)
    entered, finish = Event(), Event()

    def upload():
        with media_reference_guard(repository.dsn):
            # Deduplicated PUT does not refresh the old object's timestamp.
            media.put(media.read(value["asset_id"]))
            entered.set()
            assert finish.wait(5)
            capture(repository, saved, value, slot="baseline")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(upload)
        assert entered.wait(5)
        try:
            with pytest.raises(MaintenanceBusy):
                collect_orphan_media(repository.dsn, media, dry_run=False, now=NOW)
        finally:
            finish.set()
        pending.result(timeout=5)
    report = collect_orphan_media(repository.dsn, media, dry_run=False, now=NOW)
    assert report["protected_referenced"] == 1
    assert report["deleted_assets"] == []
    assert media.read(value["asset_id"])


def test_shared_guards_allow_concurrent_collectors_and_release_on_exception(repository, media):
    with media_reference_guard(repository.dsn):
        with media_reference_guard(repository.dsn):
            with pytest.raises(MaintenanceBusy):
                collect_orphan_media(repository.dsn, media)
    with pytest.raises(RuntimeError), media_reference_guard(repository.dsn):
        raise RuntimeError("injected upload failure")
    assert collect_orphan_media(repository.dsn, media)["scanned"] == 0


def test_out_of_order_inventory_fails_before_any_deletion(repository, media, monkeypatch):
    asset(media, 1)
    asset(media, 2)
    rows = list(media.iter_assets())
    monkeypatch.setattr(media, "iter_assets", lambda: iter(reversed(rows)))
    with pytest.raises(LifecycleError, match="ordered"):
        collect_orphan_media(repository.dsn, media, dry_run=False, now=NOW)
    assert all(media.path(row["asset_id"]).exists() for row in rows)


@pytest.mark.parametrize("seconds", [0, 3599, -1, True, 1.2, "3600", None, 10**30])
def test_too_small_or_invalid_grace_never_touches_storage(repository, media, seconds):
    with pytest.raises(ValueError):
        collect_orphan_media(repository.dsn, media, grace_seconds=seconds)


@pytest.mark.parametrize("limit", [0, 1001, -1, True, 1.1, "5"])
def test_unbounded_or_invalid_batches_fail_closed(repository, media, limit):
    with pytest.raises(ValueError):
        collect_orphan_media(repository.dsn, media, limit=limit)


def test_non_boolean_apply_and_naive_time_are_rejected(repository, media):
    with pytest.raises(ValueError):
        collect_orphan_media(repository.dsn, media, dry_run=None)
    with pytest.raises(ValueError):
        prune_previews(repository.dsn, dry_run=0)
    with pytest.raises(ValueError):
        collect_orphan_media(repository.dsn, media, now=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        collect_orphan_media(repository.dsn, media, now=False)
    with pytest.raises(ValueError):
        run_maintenance(repository.dsn, media, invented=True)


def test_delete_transport_failure_is_reported_without_leaking_exception(repository, media, monkeypatch):
    value = asset(media)

    def fail(_asset_id):
        raise OSError("secret provider diagnostic must not appear in the report")

    monkeypatch.setattr(media, "delete", fail)
    report = collect_orphan_media(repository.dsn, media, dry_run=False, now=NOW)
    assert report["delete_failed_assets"] == [value["asset_id"]]
    assert "secret" not in str(report)
    assert report["deleted_assets"] == []


def test_real_pg_dump_restore_preserves_capability_evidence_media_and_budget(repository, media, tmp_path):
    assert shutil.which("pg_dump") and shutil.which("pg_restore"), "PostgreSQL restore tools are required"
    collections = CollectionRepository(repository)
    campaign = collections.create_campaign("Restore exercise", "spatial", "replay", target_pairs=1)
    created = repository.create_session("spatial", "replay", "bottle", "cup")
    saved = created["session"]
    with psycopg.connect(repository.dsn) as connection:
        connection.execute(
            "UPDATE counterpart_sessions SET campaign_id=%s WHERE id=%s", (campaign["id"], saved["id"])
        )
        connection.execute("INSERT INTO counterpart_budget_accounts VALUES ('roboflow',17)")
        connection.execute(
            "INSERT INTO counterpart_budget_reservations VALUES ('roboflow','prior-call',17,now())"
        )
    reference = None
    original_bytes = {}
    for slot in ("baseline", "counterpart"):
        frame = generate("spatial", slot)[0]
        value = {**media.put(frame["bytes"]), "t_ms": frame["t_ms"]}
        original_bytes[value["asset_id"]] = frame["bytes"]
        metadata = {
            "slot": slot,
            "idempotency_key": uuid4().hex,
            "expected_revision": saved["revision"],
            "epoch": saved["epoch"],
        }
        repository.submit(saved["id"], metadata, [value], metadata["idempotency_key"])
        job = repository.claim("restore-test")
        observed = [{**value, "detections": frame["detections"]}]
        decision = verify("spatial", slot, observed, reference)
        assert decision["accepted"]
        assert repository.finish(job["id"], job["lease_token"], decision, observed)
        if slot == "baseline":
            reference = {**decision, "frames": observed}
        saved = repository.snapshot(saved["id"])
    pair = repository.publish(saved["id"], saved["revision"])
    collections.review_pair(
        campaign["id"],
        pair["id"],
        decision="approved",
        expected_version=0,
        reviewer="Restore test",
        idempotency_key="restore-review",
    )
    release = collections.export_manifests(campaign["id"], pair_ids=[pair["id"]], include_replay=True)
    before = repository.snapshot(saved["id"])
    destination = tmp_path / "backup"
    manifest = create_backup(repository.dsn, media, destination)
    assert manifest == json.loads((destination / "backup.json").read_text())
    assert not (destination / ".incomplete").exists()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE((destination / "database.dump").stat().st_mode) == 0o600
    assert (
        hashlib.sha256((destination / "database.dump").read_bytes()).hexdigest()
        == manifest["database"]["sha256"]
    )
    # Destruction is restricted to this test's unique schema, never a real app.
    with psycopg.connect(repository.dsn, autocommit=True) as connection:
        schema = connection.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("cp_lifecycle_")
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    with postgres_tool_environment(repository.dsn) as environment:
        restored = subprocess.run(
            [
                "pg_restore",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--dbname=service=counterpart_maintenance",
                str(destination / "database.dump"),
            ],
            env=environment,
            capture_output=True,
            timeout=60,
            check=False,
        )
    assert restored.returncode == 0, "The isolated PostgreSQL restore failed"
    assert repository.snapshot(saved["id"]) == before
    assert repository.authenticate(saved["id"], created["token"])
    assert collections.get_release(campaign["id"], release["id"]) == release
    assert collections.review_history(campaign["id"], pair["id"])[0]["decision"] == "approved"
    with psycopg.connect(repository.dsn) as connection:
        assert (
            connection.execute("SELECT reserved_units FROM counterpart_budget_accounts").fetchone()[0] == 17
        )
        assert connection.execute("SELECT units FROM counterpart_budget_reservations").fetchone()[0] == 17
    restored_media = MediaStore(tmp_path / "restored-media")
    for entry in manifest["assets"]:
        restored_media.put((destination / "assets" / entry["asset_id"]).read_bytes())
    for asset_id, content in original_bytes.items():
        assert restored_media.read(asset_id) == content


def test_backup_refuses_to_report_complete_when_evidence_is_missing(repository, media, tmp_path):
    value = asset(media)
    capture(repository, session(repository), value, slot="baseline")
    media.delete(value["asset_id"])
    destination = tmp_path / "incomplete-backup"
    with pytest.raises(LifecycleError, match="Referenced media is missing"):
        create_backup(repository.dsn, media, destination)
    assert (destination / ".incomplete").exists()
    assert not (destination / "backup.json").exists()


def test_backup_subprocess_failure_is_sanitized_and_existing_path_is_not_overwritten(
    repository, media, tmp_path
):
    destination = tmp_path / "failed-backup"
    with pytest.raises(LifecycleError, match="did not finish"):
        create_backup(repository.dsn, media, destination, pg_dump="/does/not/exist")
    assert (destination / ".incomplete").exists()
    with pytest.raises(FileExistsError):
        create_backup(repository.dsn, media, destination)


def test_gc_real_http_s3_retains_referenced_objects_and_only_deletes_current_version(repository):
    import boto3
    from moto.server import ThreadedMotoServer

    from counterpart.object_storage import S3MediaStore

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            endpoint_url=f"http://{host}:{port}",
            aws_access_key_id="local-emulator",
            aws_secret_access_key="local-emulator",
        )
        client.create_bucket(Bucket="counterpart-lifecycle-tests")
        client.put_bucket_versioning(
            Bucket="counterpart-lifecycle-tests", VersioningConfiguration={"Status": "Enabled"}
        )
        remote = S3MediaStore("counterpart-lifecycle-tests", client=client)
        values = []
        for color in ("blue", "red"):
            image = io.BytesIO()
            Image.new("RGB", (160, 120), color).save(image, format="PNG")
            values.append(remote.put(image.getvalue()))
        capture(repository, session(repository), values[0], slot="baseline")
        report = collect_orphan_media(
            repository.dsn, remote, dry_run=False, now=datetime.now(UTC) + timedelta(days=2)
        )
        assert report["deleted_assets"] == [values[1]["asset_id"]]
        assert remote.read(values[0]["asset_id"])
        with pytest.raises(FileNotFoundError):
            remote.read(values[1]["asset_id"])
        versions = client.list_object_versions(Bucket=remote.bucket, Prefix=remote.key(values[1]["asset_id"]))
        assert len(versions["Versions"]) == 1
        assert len(versions["DeleteMarkers"]) == 1
    finally:
        server.stop()


def test_backup_service_credentials_never_enter_argv_or_durable_bundle(tmp_path):
    # An explicit test password verifies transport hygiene without using real credentials.
    dsn = make_conninfo("postgresql:///counterpart_test", password="fixture-password")
    with postgres_tool_environment(dsn) as environment:
        service = Path(environment["PGSERVICEFILE"])
        assert stat.S_IMODE(service.stat().st_mode) == 0o600
        assert "password=fixture-password" in service.read_text()
        assert environment["PGSERVICE"] == "counterpart_maintenance"
    assert not service.exists()


def test_invalid_later_gc_option_cannot_prune_before_failing(repository, media):
    capture(repository, session(repository), asset(media))
    with pytest.raises(ValueError):
        run_maintenance(repository.dsn, media, dry_run=False, after_asset_id="not-a-digest")
    assert table_count(repository, "counterpart_captures") == 1


def test_backup_cannot_write_private_data_into_source_checkout(repository, media):
    forbidden = Path(__file__).resolve().parents[1] / "forbidden-backup"
    with pytest.raises(LifecycleError, match="outside every source checkout"):
        create_backup(repository.dsn, media, forbidden)
    assert not forbidden.exists()


def test_ingestion_during_exclusive_maintenance_fails_fast_without_touching_media(repository, media):
    from counterpart.lifecycle import _maintenance_guard

    value = asset(media)
    with _maintenance_guard(repository.dsn):
        with pytest.raises(MaintenanceBusy, match="retry this saved upload"):
            with media_reference_guard(repository.dsn):
                pytest.fail("The upload must not enter its media-write section during maintenance")
    assert media.read(value["asset_id"])
    with media_reference_guard(repository.dsn):
        assert media.read(value["asset_id"])
