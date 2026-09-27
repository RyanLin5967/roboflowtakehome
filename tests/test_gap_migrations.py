"""Upgrade populated historical databases, not hand-written facsimiles of old DDL.

Historical Python and SQL are read from Git into a private temporary directory.
Only generated images and random schemas in COUNTERPART_TEST_DSN are touched.
There is no provider call, detector execution, or application-database mutation.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from counterpart.archives import archive_response
from counterpart.collections import CollectionRepository
from counterpart.config import Settings
from counterpart.consumer import PortableRelease
from counterpart.lifecycle import create_backup, postgres_tool_environment
from counterpart.repository import Conflict, Repository
from counterpart.storage import MediaStore
from counterpart.worker import Worker

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_REVISIONS = (
    "a51fa31",  # Initial capture kernel, before campaigns and frozen recipes.
    "eb58c78",  # Complete pre-gap-closure product, including immutable releases.
)

# This is intentionally executed using the *historical* package in a child
# interpreter. New code cannot accidentally fabricate the legacy fixture.
LEGACY_SEED = r'''
import hashlib, json, os
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from counterpart.fixtures import generate
from counterpart.repository import Repository
from counterpart.storage import MediaStore
from counterpart.verification import verify

repo = Repository(os.environ["MIGRATION_TEST_DSN"])
repo.initialize()
media = MediaStore(Path(os.environ["MIGRATION_TEST_MEDIA"]))
try:
    from counterpart.collections import CollectionRepository
except ImportError:
    collections = None
else:
    collections = CollectionRepository(repo)
campaign = invite = release = None
if collections:
    campaign = collections.create_campaign("Historical collection", "spatial", "replay")
    invite = collections.create_invite(campaign["id"], max_sessions=4)
    created = collections.create_session_from_invite(invite["token"], "old-response-lost")
else:
    created = repo.create_session("spatial", "replay", "bottle", "cup")
saved = created["session"]
reference = None
original_assets = {}
for slot in ("baseline", "counterpart"):
    frame = generate("spatial", slot)[0]
    value = {**media.put(frame["bytes"]), "t_ms": frame["t_ms"],
             "fixture_detections": frame["detections"]}
    original_assets[value["asset_id"]] = hashlib.sha256(frame["bytes"]).hexdigest()
    metadata = {"slot": slot, "idempotency_key": uuid4().hex,
                "expected_revision": saved["revision"], "epoch": saved["epoch"]}
    repo.submit(saved["id"], metadata, [value], metadata["idempotency_key"])
    job = repo.claim("historical-generated-verifier")
    observed = [{**{k:v for k,v in value.items() if k != "fixture_detections"},
                 "detections": frame["detections"],
                 "model": {"provider": "fixture", "model_id": "illustrative-replay-v1"}}]
    decision = verify("spatial", slot, observed, reference)
    assert decision["accepted"]
    assert repo.finish(job["id"], job["lease_token"], decision, observed)
    reference = {**decision, "frames": observed}
    saved = repo.snapshot(saved["id"])
pair = repo.publish(saved["id"], saved["revision"])
if collections:
    collections.review_pair(campaign["id"], pair["id"], decision="approved", expected_version=0,
                            reviewer="Historical operator", reason="Generated fixture evidence",
                            idempotency_key="historical-review")
    release = collections.export_manifests(campaign["id"], pair_ids=[pair["id"]], include_replay=True)
assert repo.reserve_budget("roboflow", "historical-accounted-call", 17, 128)
# Synthetic receipt covers JSON preservation without representing a real upload.
receipt = {next(iter(original_assets)): {"state": "uncertain", "image_id": "synthetic-old-id",
                                        "reason": "historical-test-receipt"}}
with repo._connection() as connection:
    connection.execute("UPDATE counterpart_export_intents SET receipts=%s WHERE pair_id=%s",
                       (Jsonb(receipt), pair["id"]))
queued = repo.create_session("spatial", "live", "bottle", "cup")
frame = generate("spatial", "baseline")[0]
value = {**media.put(frame["bytes"]), "t_ms": 0}
metadata = {"slot": "baseline", "idempotency_key": "historical-queued-capture",
            "expected_revision": 0, "epoch": 0}
pending = repo.submit(queued["session"]["id"], metadata, [value], "historical-fingerprint")
print(json.dumps({"created": created, "pair": pair, "campaign": campaign, "invite": invite,
                  "release": release, "queued": queued, "pending": pending,
                  "metadata": metadata, "queued_frames": [value], "receipts": receipt,
                  "assets": original_assets}))
'''


def _historical_source(revision: str, destination: Path) -> Path:
    archive = subprocess.run(
        ["git", "archive", revision, "src/counterpart"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as members:
        members.extractall(destination, filter="data")
    return destination / "src"


def _table_snapshot(dsn: str, columns: dict | None = None) -> tuple[dict, dict]:
    """Compare every preexisting column and row, ignoring only newly added fields."""
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        if columns is None:
            columns = {}
            for row in connection.execute(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position"
            ):
                columns.setdefault(row["table_name"], []).append(row["column_name"])
        rows = {}
        for table, names in columns.items():
            result = connection.execute(sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(",").join(map(sql.Identifier, names)), sql.Identifier(table),
            )).fetchall()
            rows[table] = sorted(json.dumps(row, sort_keys=True, default=str) for row in result)
    return columns, rows


@pytest.fixture(params=HISTORICAL_REVISIONS)
def historical(request, tmp_path):
    base = os.environ.get("COUNTERPART_TEST_DSN")
    if not base:
        pytest.skip("Set COUNTERPART_TEST_DSN to an isolated PostgreSQL test database")
    schema = "cp_gap_migration_" + uuid4().hex
    with psycopg.connect(base, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = make_conninfo(base, options=f"-c search_path={schema}")
    try:
        source = _historical_source(request.param, tmp_path / "old-source")
        media = tmp_path / "media"
        environment = {
            **os.environ, "PYTHONPATH": str(source), "MIGRATION_TEST_DSN": dsn,
            "MIGRATION_TEST_MEDIA": str(media),
        }
        run = subprocess.run(
            [sys.executable, "-c", LEGACY_SEED], cwd=tmp_path, env=environment,
            capture_output=True, text=True, timeout=60,
        )
        assert run.returncode == 0, f"Historical seed {request.param} failed: {run.stderr}"
        data = json.loads(run.stdout)
        columns, before = _table_snapshot(dsn)
        yield {
            "revision": request.param, "dsn": dsn, "schema": schema, "base": base,
            "repo": Repository(dsn), "media": MediaStore(media), "data": data,
            "columns": columns, "before": before,
        }
    finally:
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _archive_bytes(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


def test_populated_historical_upgrade_is_repeatable_and_preserves_evidence(historical):
    repo, data = historical["repo"], historical["data"]
    repo.initialize()
    repo.initialize()
    assert _table_snapshot(repo.dsn, historical["columns"])[1] == historical["before"]
    for key in ("created", "queued"):
        created = data[key]
        assert repo.authenticate(created["session"]["id"], created["token"])
        saved = repo.snapshot(created["session"]["id"])
        assert saved["execution_contract"] is None
        assert saved["baseline_authorization"] is None
    with repo._connection() as connection:
        assert all(row["split_group_snapshot"] is None for row in connection.execute(
            "SELECT split_group_snapshot FROM counterpart_sessions"
        ))
        for capture in connection.execute(
            "SELECT baseline_authorization,recovery_unresolved FROM counterpart_captures"
        ):
            assert capture["baseline_authorization"] is None
            assert capture["recovery_unresolved"] is False
    pair = repo.snapshot(data["created"]["session"]["id"])["pair"]
    assert pair["manifest"] == data["pair"]["manifest"]
    for asset, digest in data["assets"].items():
        assert hashlib.sha256(historical["media"].read(asset)).hexdigest() == digest
    if data["release"] is not None:
        collections = CollectionRepository(repo)
        release = collections.get_release(data["campaign"]["id"], data["release"]["id"])
        assert release == data["release"]
        # Frozen, old release remains downloadable and readable by today's
        # independent offline consumer without inventing a retroactive model pin.
        raw = asyncio.run(_archive_bytes(archive_response(
            release, historical["media"], "historical.zip",
        )))
        with PortableRelease(io.BytesIO(raw), allow_replay=True) as consumer:
            assert consumer.summary["pair_count"] == 1
            assert consumer.summary["asset_count"] == 2
            assert len(list(consumer.iter_examples())) == 2
        recovered = collections.create_session_from_invite(data["invite"]["token"], "old-response-lost")
        assert recovered["token"] == data["created"]["token"]
        with pytest.raises(Conflict) as error:
            collections.create_session_from_invite(data["invite"]["token"], "new-historical-join")
        assert "migration" in error.value.code


class NeverCallDetector:
    model_id = "coco/39"

    def detect(self, *_args, **_kwargs):
        raise AssertionError("Legacy work must fail before provider inference")


def test_legacy_queued_work_is_idempotent_but_fails_before_compute(historical):
    repo, data = historical["repo"], historical["data"]
    repo.initialize()
    retry = repo.submit(
        data["queued"]["session"]["id"], data["metadata"], data["queued_frames"],
        "historical-fingerprint",
    )
    assert retry["capture"]["id"] == data["pending"]["capture"]["id"]
    configuration = Settings(
        database_url=repo.dsn, live_enabled=True, inference_limit=128,
        credentials_file=Path("/nonexistent-counterpart-test-credentials"),
    )
    worker = Worker(repo, historical["media"], configuration, detector=NeverCallDetector())
    assert worker.once()
    assert not worker.once()
    saved = repo.snapshot(data["queued"]["session"]["id"])
    assert saved["execution_contract"] is None
    assert saved["latest_capture"]["status"] == "failed"
    expected = {"execution_contract_migration_required"}
    if historical["revision"] == "a51fa31":
        expected.add("unsupported_recipe")
    assert saved["latest_capture"]["error"] in expected
    assert worker.runtime._initialized is False
    with repo._connection() as connection:
        assert connection.execute(
            "SELECT reserved_units FROM counterpart_budget_accounts WHERE provider='roboflow'"
        ).fetchone()["reserved_units"] == 17
        assert connection.execute(
            "SELECT count(*) AS n FROM counterpart_budget_reservations"
        ).fetchone()["n"] == 1
        assert connection.execute(
            "SELECT receipts FROM counterpart_export_intents WHERE pair_id=%s", (data["pair"]["id"],),
        ).fetchone()["receipts"] == data["receipts"]


def test_feature_migration_failure_rolls_back_ddl_and_every_historical_row(historical, monkeypatch):
    repo = historical["repo"]
    original = Path.read_text

    def fail_late(path, *args, **kwargs):
        if path.name == "evidence_schema.sql" and path.parent.name == "counterpart":
            return "SELECT 1 / 0;"
        return original(path, *args, **kwargs)

    with monkeypatch.context() as temporary:
        temporary.setattr(Path, "read_text", fail_late)
        with pytest.raises(psycopg.errors.DivisionByZero):
            repo.initialize()
    columns, rows = _table_snapshot(repo.dsn)
    assert columns == historical["columns"]
    assert rows == historical["before"]
    repo.initialize()
    assert _table_snapshot(repo.dsn, historical["columns"])[1] == historical["before"]


def _seed_current_evidence(historical):
    """Exercise current feature APIs on old immutable evidence and new fixtures."""
    from counterpart.evidence_revisions import CRITERIA, EvidenceRepository
    from counterpart.execution_contract import build_execution_contract
    from counterpart.fixtures import generate

    repo, media, data = historical["repo"], historical["media"], historical["data"]
    collections = CollectionRepository(repo)
    evidence = EvidenceRepository(repo, media)
    old_session = data["created"]["session"]["id"]
    detail = evidence.detail(old_session)
    capture = detail["captures"][0]
    revision = evidence.reverify(
        old_session, capture["capture"]["id"], expected_version=0,
        expected_evidence_sha256=capture["evidence_sha256"], recipe_version="1.0.0",
        idempotency_key="backup-reverify-historical", reviewer="Restore fixture operator",
    )
    evidence.review_attempt(
        old_session, capture["capture"]["id"], decision="needs_recapture", expected_version=0,
        expected_evidence_sha256=capture["evidence_sha256"],
        reason="Generated review record for the backup and restore test.",
        idempotency_key="backup-attempt-review", reviewer="Restore fixture operator",
    )
    # Explicit future-work migration is separately audited; the original queued
    # record never receives fabricated historical model-observation provenance.
    queued_session = data["queued"]["session"]["id"]
    repo.migrate_execution_contract(
        queued_session, expected_revision=1, reviewer="Restore fixture operator",
        reason="Explicitly pin future work in a synthetic restore test.",
        execution_contract=build_execution_contract(mode="live"),
    )
    worker = Worker(repo, media, Settings(database_url=repo.dsn, live_enabled=False))
    assert worker.once()  # Existing live work cannot execute on this offline worker.
    assert repo.snapshot(queued_session)["latest_capture"]["status"] == "failed"
    group = collections.create_split_group("Restore scene", "Generated tabletop fixture family")
    campaign = collections.create_campaign("Restore revision coverage", "spatial", "replay")
    invite = collections.create_invite(campaign["id"], split_group_id=group["id"])
    created = collections.create_session_from_invite(invite["token"], "restore-scene-join")
    saved = created["session"]
    for slot in ("baseline", "counterpart"):
        frame = generate("spatial", slot)[0]
        detections = frame["detections"]
        if slot == "counterpart":
            detections = [{**item, "confidence": 0.1} for item in detections]
        value = {**media.put(frame["bytes"]), "t_ms": frame["t_ms"],
                 "fixture_detections": detections}
        metadata = {"slot": slot, "idempotency_key": f"restore-{slot}",
                    "expected_revision": saved["revision"], "epoch": saved["epoch"]}
        repo.submit(saved["id"], metadata, [value], metadata["idempotency_key"])
        assert worker.once()
        saved = repo.snapshot(saved["id"])
    detail = evidence.detail(saved["id"])
    selected = detail["selected_pair"]
    adjudicated = evidence.adjudicate(
        saved["id"], baseline_id=selected["baseline_id"], counterpart_id=selected["counterpart_id"],
        expected_session_revision=saved["revision"],
        expected_evidence_sha256=selected["evidence_sha256"],
        labels=detail["recipe_snapshot"]["expected_answers"], criteria=dict.fromkeys(CRITERIA, True),
        reason="Synthetic reviewer confirms generated drawings only for restore testing.",
        idempotency_key="restore-adjudication", reviewer="Restore fixture operator",
    )
    return {"revision": revision, "group": group, "session": created,
            "adjudicated": adjudicated, "detail": evidence.detail(saved["id"])}


def _seed_delivery_storage_rows(repo, release_id):
    """Storage-only fixture for provider-effect records, never an upload simulation.

    Provider integration has its own semantic/transport suite. These explicitly
    synthetic payloads only establish that backup/restore includes every new
    table and preserves an in-doubt external effect without reissuing it.
    """
    snapshot_id, delivery_id = str(uuid4()), str(uuid4())
    record = {"synthetic_storage_test": True, "cloud_calls": 0, "identity": snapshot_id}
    with repo._connection() as connection:
        connection.execute(
            "INSERT INTO counterpart_annotation_snapshots "
            "(id,release_id,request_key,content_sha256,manifest) VALUES (%s,%s,%s,%s,%s)",
            (snapshot_id, release_id, "storage-test-sync", "a" * 64, Jsonb(record)),
        )
        connection.execute(
            "INSERT INTO counterpart_annotation_approvals "
            "(snapshot_id,request_key,reviewer,reason) VALUES (%s,%s,%s,%s)",
            (snapshot_id, "storage-test-approval", "Synthetic operator", "Storage roundtrip only"),
        )
        connection.execute(
            "INSERT INTO counterpart_roboflow_deliveries "
            "(id,snapshot_id,request_key,status,generation_reserved,budget_evidence_sha256,"
            "before_versions,receipt,last_error) VALUES (%s,%s,%s,'uncertain',true,%s,%s,%s,%s)",
            (delivery_id, snapshot_id, "storage-test-delivery", "b" * 64,
             Jsonb([1, 2]), Jsonb(record), "synthetic_response_lost"),
        )
        connection.execute(
            "INSERT INTO counterpart_roboflow_delivery_events (delivery_id,kind,data) VALUES (%s,%s,%s)",
            (delivery_id, "synthetic_response_lost", Jsonb(record)),
        )


def _seed_baseline_authorization(historical):
    """Real v2 verification and operator authority over generated flat images.

    Production replay remains v1. This explicitly frozen v2 fixture uses the
    internal transaction-owned session factory solely to test authority recovery
    without inventing hosted-model observations or making an inference call.
    """
    from PIL import Image, ImageDraw

    from counterpart.evidence_revisions import BASELINE_CRITERIA, EvidenceRepository
    from counterpart.recipes import build_recipe_snapshot

    repo, media = historical["repo"], historical["media"]
    frozen = build_recipe_snapshot("spatial", recipe_version="2.0.0")
    with repo._connection() as connection:
        created = repo._create_session(
            connection, "spatial", "replay", "bottle", "cup", recipe_snapshot=frozen,
        )
    saved = created["session"]
    worker = Worker(repo, media, Settings(database_url=repo.dsn, live_enabled=False))
    payloads = {}
    for slot, x in (("baseline", 0.14), ("counterpart", 0.74)):
        image = Image.new("RGB", (640, 480), "#d9dadb")
        draw = ImageDraw.Draw(image)
        detections = [
            {"label": "bottle", "confidence": 0.95, "box": [x, 0.40, x + 0.10, 0.68]},
            {"label": "cup", "confidence": 0.96, "box": [0.44, 0.40, 0.54, 0.68]},
        ]
        for detection, color in zip(detections, ("#208040", "#df8040"), strict=True):
            draw.rectangle(tuple(round(value * dimension) for value, dimension in zip(
                detection["box"], (640, 480, 640, 480), strict=True,
            )), fill=color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payloads[slot] = [{**media.put(buffer.getvalue()), "t_ms": 0,
                           "fixture_detections": detections}]
    metadata = {
        "slot": "baseline", "idempotency_key": "restore-flat-baseline",
        "expected_revision": saved["revision"], "epoch": saved["epoch"],
    }
    repo.submit(saved["id"], metadata, payloads["baseline"], metadata["idempotency_key"])
    assert worker.once()
    saved = repo.snapshot(saved["id"])
    original = saved["latest_capture"]
    assert original["status"] == "rejected"
    assert original["result"]["outcome"] == "unknown"
    assert original["result"]["code"] == "registration_uncertain"
    evidence = EvidenceRepository(repo, media)
    detail = evidence.detail(saved["id"])
    capture = detail["captures"][0]
    authorization = evidence.authorize_baseline(
        saved["id"], original["id"], expected_session_revision=saved["revision"],
        expected_evidence_sha256=capture["evidence_sha256"],
        answer=frozen["expected_answers"]["baseline"], criteria=dict.fromkeys(BASELINE_CRITERIA, True),
        reason="Generated geometry is valid; synthetic review covers the textureless background only.",
        idempotency_key="restore-baseline-authority", reviewer="Synthetic restore operator",
    )
    saved = repo.snapshot(saved["id"])
    assert saved["baseline"]["status"] == "rejected"
    assert saved["baseline"]["result"] == original["result"]
    assert saved["baseline_authorization"] == authorization
    resumed = repo.resume(saved["id"], saved["epoch"])
    assert resumed["recovery_required"] is True
    counterpart_metadata = {
        "slot": "counterpart", "idempotency_key": "restore-authorized-counterpart",
        "expected_revision": resumed["revision"], "epoch": resumed["epoch"],
    }
    pending = repo.submit(
        saved["id"], counterpart_metadata, payloads["counterpart"],
        counterpart_metadata["idempotency_key"],
    )
    assert pending["capture"]["baseline_authorization"] == authorization
    assert pending["capture"]["recovery_unresolved"] is True
    return {
        "created": created, "authorization": authorization, "original": original,
        "pending": pending, "metadata": counterpart_metadata, "frames": payloads["counterpart"],
        "snapshot": repo.snapshot(saved["id"]),
    }


@pytest.mark.parametrize("historical", ["eb58c78"], indirect=True)
def test_real_restore_preserves_migrated_history_and_every_new_feature_table(historical, tmp_path):
    from counterpart.evidence_revisions import EvidenceRepository

    repo = historical["repo"]
    repo.initialize()
    new = _seed_current_evidence(historical)
    authority = _seed_baseline_authorization(historical)
    _seed_delivery_storage_rows(repo, historical["data"]["release"]["id"])
    columns, before = _table_snapshot(repo.dsn)
    for table in (
        "counterpart_split_groups", "counterpart_evidence_revisions", "counterpart_attempt_reviews",
        "counterpart_evidence_adjudications", "counterpart_annotation_snapshots",
        "counterpart_annotation_approvals", "counterpart_roboflow_deliveries",
        "counterpart_roboflow_delivery_events", "counterpart_baseline_authorizations",
    ):
        assert before[table], f"Backup fixture must populate {table}"
    destination = tmp_path / "gap-feature-backup"
    manifest = create_backup(repo.dsn, historical["media"], destination)
    assert manifest["database"]["sha256"] == hashlib.sha256(
        (destination / "database.dump").read_bytes()
    ).hexdigest()
    with psycopg.connect(historical["base"], autocommit=True) as connection:
        assert historical["schema"].startswith("cp_gap_migration_")
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(historical["schema"])))
    with postgres_tool_environment(repo.dsn) as environment:
        result = subprocess.run(
            ["pg_restore", "--no-owner", "--no-acl", "--exit-on-error",
             "--dbname=service=counterpart_maintenance", str(destination / "database.dump")],
            env=environment, capture_output=True, timeout=60,
        )
    assert result.returncode == 0, "Isolated feature-schema restore failed"
    assert _table_snapshot(repo.dsn) == (columns, before)
    repo.initialize()
    repo.initialize()
    assert _table_snapshot(repo.dsn) == (columns, before)
    restored_media = MediaStore(tmp_path / "restored-media")
    for asset in manifest["assets"]:
        restored_media.put((destination / "assets" / asset["asset_id"]).read_bytes())
        assert restored_media.read(asset["asset_id"]) == historical["media"].read(asset["asset_id"])
    session_id = new["session"]["session"]["id"]
    assert repo.authenticate(session_id, new["session"]["token"])
    assert EvidenceRepository(repo, restored_media).detail(session_id) == new["detail"]
    assert CollectionRepository(repo).get_release(
        historical["data"]["campaign"]["id"], historical["data"]["release"]["id"],
    ) == historical["data"]["release"]
    # Restoring bytes without restoring mutation guards would be incomplete.
    with pytest.raises(psycopg.Error, match="immutable"):
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_sessions SET execution_contract=NULL WHERE id=%s", (session_id,),
            )
    with pytest.raises(psycopg.Error, match="immutable"):
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_sessions SET split_group_snapshot=NULL WHERE id=%s", (session_id,),
            )
    with pytest.raises(psycopg.Error, match="append-only"):
        with repo._connection() as connection:
            connection.execute(
                "UPDATE counterpart_baseline_authorizations SET record='{}'::jsonb WHERE id=%s",
                (authority["authorization"]["id"],),
            )
    with pytest.raises(psycopg.Error, match="append-only"):
        with repo._connection() as connection:
            connection.execute(
                "DELETE FROM counterpart_baseline_authorizations WHERE id=%s",
                (authority["authorization"]["id"],),
            )
    for table, row_id in (
        ("counterpart_sessions", authority["created"]["session"]["id"]),
        ("counterpart_captures", authority["pending"]["capture"]["id"]),
    ):
        with pytest.raises(psycopg.Error, match="bound to immutable evidence"):
            with repo._connection() as connection:
                connection.execute(
                    sql.SQL("UPDATE {} SET baseline_authorization=%s WHERE id=%s").format(
                        sql.Identifier(table),
                    ),
                    (Jsonb({**authority["authorization"], "reason": "forged copy"}), row_id),
                )
    authorized_id = authority["created"]["session"]["id"]
    assert repo.snapshot(authorized_id) == authority["snapshot"]
    assert repo.authenticate(authorized_id, authority["created"]["token"])
    recovered = repo.submit(
        authorized_id, authority["metadata"], authority["frames"],
        authority["metadata"]["idempotency_key"],
    )
    assert recovered["capture"] == authority["pending"]["capture"]
    worker = Worker(repo, restored_media, Settings(database_url=repo.dsn, live_enabled=False))
    assert worker.once()
    assert not worker.once()
    finished = repo.snapshot(authorized_id)
    assert finished["baseline"]["result"] == authority["original"]["result"]
    assert finished["baseline"]["status"] == "rejected"
    assert finished["baseline_authorization"] == authority["authorization"]
    assert finished["latest_capture"]["baseline_authorization"] == authority["authorization"]
    assert finished["latest_capture"]["recovery_unresolved"] is True
    assert finished["latest_capture"]["result"]["human_review_required"] is True
    assert finished["latest_capture"]["result"]["accepted"] is False
    with pytest.raises(Conflict):
        repo.publish(authorized_id, finished["revision"])
    with repo._connection() as connection:
        assert connection.execute(
            "SELECT reserved_units FROM counterpart_budget_accounts WHERE provider='roboflow'"
        ).fetchone()["reserved_units"] == 17
        assert connection.execute(
            "SELECT count(*) AS n FROM counterpart_budget_reservations"
        ).fetchone()["n"] == 1
