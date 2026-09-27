"""Portable archive correctness and hostile input tests, without any ML compute."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import struct
import warnings
import zipfile
from copy import deepcopy
from dataclasses import replace
from functools import cache
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.archives import archive_response
from counterpart.collections import CollectionRepository
from counterpart.config import Settings
from counterpart.consumer import ArchiveValidationError, ConsumerLimits, PortableRelease
from counterpart.dataset import examples
from counterpart.fixtures import generate
from counterpart.recipes import build_recipe_snapshot
from counterpart.repository import Repository
from counterpart.splits import split_for_session
from counterpart.storage import MediaStore, inspect_image
from counterpart.verification import verify
from counterpart.worker import Worker


@cache
def generated(recipe, slot):
    return generate(recipe, slot)


def make_pair(recipe="spatial", *, session_id=None, campaign_id=None):
    session_id = session_id or str(uuid4())
    manifest = {
        "schema_version": "counterpart.pair.v1",
        "pair_id": str(uuid4()),
        "session_id": session_id,
        "recipe": recipe,
        "mode": "replay",
        "revision": 2,
        "objects": {"a": "bottle", "b": "cup"},
        "recipe_snapshot": build_recipe_snapshot(recipe),
        "campaign_id": campaign_id,
        "split_group": session_id,
        "split": split_for_session(session_id),
        "provenance": {
            "evidence_kind": "fixture_replay",
            "human_reviewed": False,
            "legacy_recipe_snapshot_missing": False,
        },
    }
    assets = {}
    baseline = None
    for revision, slot in enumerate(("baseline", "counterpart"), 1):
        frames = []
        for frame in deepcopy(generated(recipe, slot)):
            raw = frame.pop("bytes")
            metadata = inspect_image(raw)
            frames.append({**frame, **metadata})
            assets[metadata["asset_id"]] = raw
        result = verify(recipe, slot, frames, baseline)
        assert result["accepted"]
        manifest[slot] = {
            "id": str(uuid4()),
            "slot": slot,
            "status": "accepted",
            "revision": revision,
            "epoch": 0,
            "client_seq": 0,
            "created_at": "2026-09-26T00:00:00+00:00",
            "completed_at": "2026-09-26T00:00:01+00:00",
            "retry_of_capture_id": None,
            "reference_capture_id": manifest["baseline"]["id"] if baseline else None,
            "frames": frames,
            "result": result,
            "error": None,
            "interrupted": False,
        }
        baseline = {**result, "frames": frames}
    return manifest, assets


def sign_release(release):
    payload = {
        key: value for key, value in release.items() if key not in {"id", "created_at", "content_sha256"}
    }
    release["content_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return release


def make_release(pairs):
    campaign = pairs[0]["campaign_id"] or str(uuid4())
    for pair in pairs:
        pair["campaign_id"] = campaign
    return sign_release(
        {
            "id": str(uuid4()),
            "campaign_id": campaign,
            "schema_version": "counterpart.dataset.v1",
            "created_at": "2026-09-26T00:00:00+00:00",
            "recipe_snapshot": pairs[0]["recipe_snapshot"],
            "evidence_kind": pairs[0]["provenance"]["evidence_kind"],
            "pair_count": len(pairs),
            "pairs": [
                {
                    "manifest": pair,
                    "review": {
                        "status": "approved",
                        "version": 2,
                        "reviewer": "Human operator",
                        "reason": "Reviewed the pair",
                        "updated_at": "2026-09-26T00:00:00+00:00",
                    },
                }
                for pair in pairs
            ],
            "split_counts": {
                split: sum(pair["split"] == split for pair in pairs) for split in ("train", "valid", "test")
            },
        }
    )


def pack(manifest, assets):
    class Store:
        def read(self, asset_id):
            return assets[asset_id]

    response = archive_response(manifest, Store(), "test.zip")

    async def consume():
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(consume())


def rewrite(raw, edits=None, *, add=(), compress=zipfile.ZIP_STORED):
    edits = edits or {}
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as old, zipfile.ZipFile(out, "w", compression=compress) as new:
        for name in old.namelist():
            data = edits.get(name, old.read(name))
            if data is not None:
                new.writestr(name, data)
        for name, data in add:
            new.writestr(name, data)
    return out.getvalue()


def consume(raw, **kwargs):
    return PortableRelease(io.BytesIO(raw), allow_replay=True, **kwargs)


@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
@pytest.mark.parametrize("reviewed", [False, True])
def test_archive_response_roundtrip_preserves_evidence_and_readable_rows(recipe, reviewed, tmp_path):
    pair, assets = make_pair(recipe)
    manifest = make_release([pair]) if reviewed else pair
    path = tmp_path / "release.zip"
    path.write_bytes(pack(manifest, assets))
    with PortableRelease(path, allow_replay=True) as release:
        rows = list(release.iter_examples())
        assert len(rows) == 2
        assert rows[0]["answer"] != rows[1]["answer"]
        assert all(row["human_reviewed"] is reviewed for row in rows)
        assert release.summary["asset_count"] == len(assets)
        assert release.summary["human_reviewed"] is reviewed
        assert release.summary["pair_count"] == 1
        assert len(list(release.iter_examples(split=pair["split"]))) == 2
        other = next(split for split in ("train", "valid", "test") if split != pair["split"])
        assert list(release.iter_examples(split=other)) == []
        for row in rows:
            for frame in row["media"]:
                assert release.read_media(frame["path"]) == assets[frame["sha256"]]
        if reviewed:
            assert rows[0]["review"]["version"] == 2
            assert "not independent ground truth" in rows[0]["label_basis"]
        rows[0]["answer"] = "corrupted by caller"
        release.manifest["schema_version"] = "caller edit"
        assert list(release.iter_examples())[0]["answer"] != "corrupted by caller"
        assert release.manifest == manifest
    with pytest.raises(ArchiveValidationError, match="closed"):
        list(release.iter_examples())


def test_fixture_pipeline_requires_explicit_opt_in():
    pair, assets = make_pair()
    with pytest.raises(ArchiveValidationError, match="allow_replay"):
        PortableRelease(io.BytesIO(pack(pair, assets)))


def test_supplied_stream_remains_owned_by_caller_and_bad_input_closes_owned_file(tmp_path):
    pair, assets = make_pair()
    source = io.BytesIO(pack(pair, assets))
    with PortableRelease(source, allow_replay=True):
        pass
    assert not source.closed
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"unreadable")
    with pytest.raises(ArchiveValidationError):
        PortableRelease(invalid)
    invalid.unlink()


def test_release_digest_excludes_release_identity_and_creation_time_but_binds_reviews():
    pair, assets = make_pair()
    release = make_release([pair])
    digest = release["content_sha256"]
    release["id"] = str(uuid4())
    release["created_at"] = "2026-09-27T00:00:00+00:00"
    with consume(pack(release, assets)) as parsed:
        assert parsed.summary["content_sha256"] == digest
    release["pairs"][0]["review"]["reason"] = "Changed reason without a new membership digest"
    with pytest.raises(ArchiveValidationError, match="digest"):
        consume(pack(release, assets))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "rejected"),
        ("version", 0),
        ("version", True),
        ("reviewer", ""),
        ("updated_at", "2026-09-26"),
    ],
)
def test_release_rejects_invalid_review_even_if_digest_recomputed(field, value):
    pair, assets = make_pair()
    release = make_release([pair])
    release["pairs"][0]["review"][field] = value
    sign_release(release)
    with pytest.raises(ArchiveValidationError, match="approved, versioned"):
        consume(pack(release, assets))


def test_release_rejects_pair_from_another_campaign():
    pair, assets = make_pair()
    release = make_release([pair])
    pair["campaign_id"] = str(uuid4())
    sign_release(release)
    with pytest.raises(ArchiveValidationError, match="different campaign"):
        consume(pack(release, assets))


def test_release_rejects_duplicate_members():
    pair, assets = make_pair()
    release = make_release([pair, deepcopy(pair)])
    with pytest.raises(ArchiveValidationError, match="repeats a pair"):
        consume(pack(release, assets))


def test_release_rejects_split_count_mismatch():
    pair, assets = make_pair()
    release = make_release([pair])
    release["split_counts"][pair["split"]] += 1
    sign_release(release)
    with pytest.raises(ArchiveValidationError, match="split counts"):
        consume(pack(release, assets))


def test_pair_rejects_altered_session_split():
    pair, assets = make_pair()
    pair["split"] = next(split for split in ("train", "valid", "test") if split != pair["split"])
    with pytest.raises(ArchiveValidationError, match="stable collection session"):
        consume(pack(pair, assets))


def test_same_assets_in_different_splits_are_rejected_but_same_split_is_supported():
    first, assets = make_pair()
    ids = {}
    while len(ids) < 2 or first["split"] not in ids:
        candidate = str(uuid4())
        ids[split_for_session(candidate)] = candidate
    same, _ = make_pair(session_id=ids[first["split"]])
    with consume(pack(make_release([first, same]), assets)) as release:
        assert release.summary["pair_count"] == 2
        assert release.summary["asset_count"] == 2
    other_id = next(value for key, value in ids.items() if key != first["split"])
    other, _ = make_pair(session_id=other_id)
    with pytest.raises(ArchiveValidationError, match="different dataset splits"):
        consume(pack(make_release([first, other]), assets))


@pytest.mark.parametrize(
    "change",
    ["legacy", "threshold", "wrong_reference", "wrong_decision", "detector", "byte_size", "human_claim"],
)
def test_tampered_pair_is_not_rescued_by_recomputed_release_digest(change):
    pair, assets = make_pair()
    if change == "legacy":
        pair["recipe_snapshot"] = None
    elif change == "threshold":
        pair["recipe_snapshot"]["verifier"]["thresholds"]["minimum_confidence"] = 0.1
    elif change == "wrong_reference":
        pair["counterpart"]["reference_capture_id"] = str(uuid4())
    elif change == "wrong_decision":
        pair["baseline"]["result"]["evidence"]["relation"] = "right"
    elif change == "detector":
        pair["baseline"]["frames"][0]["model"]["provider"] = "roboflow"
    elif change == "byte_size":
        pair["baseline"]["frames"][0]["bytes"] += 1
    elif change == "human_claim":
        pair["provenance"]["human_reviewed"] = True
    release = make_release([pair])
    # Wrong answer makes the writer correctly fail as well; use its original
    # rows to exercise the reader against a malicious manually rewritten ZIP.
    clean, _ = make_pair()
    rows = "".join(json.dumps(row) + "\n" for row in examples(clean)).encode()
    raw = pack(clean, assets)
    raw = rewrite(raw, {"manifest.json": json.dumps(release).encode(), "examples.jsonl": rows})
    with pytest.raises(ArchiveValidationError):
        consume(raw)


def test_verifier_rechecks_recorded_boxes_without_a_model_call():
    pair, assets = make_pair()
    raw = pack(pair, assets)
    pair["counterpart"]["frames"][0]["detections"][0]["box"] = [0.1, 0.33, 0.23, 0.71]
    raw = rewrite(raw, {"manifest.json": json.dumps(pair).encode()})
    with pytest.raises(ArchiveValidationError, match="deterministic verification"):
        consume(raw)


@pytest.mark.parametrize(
    "change",
    [
        "answer",
        "question",
        "human_reviewed",
        "review_version",
        "split",
        "media_path",
        "missing",
        "extra",
        "duplicate_key",
    ],
)
def test_rows_must_match_exact_frozen_evidence_and_review(change):
    pair, assets = make_pair()
    release = make_release([pair])
    raw = pack(release, assets)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = [json.loads(line) for line in archive.read("examples.jsonl").splitlines()]
    if change in {"answer", "question", "split"}:
        rows[0][change] = "incorrect"
    elif change == "human_reviewed":
        rows[0]["human_reviewed"] = False
    elif change == "review_version":
        rows[0]["review"]["version"] += 1
    elif change == "media_path":
        rows[0]["media"][0]["path"] = "../secrets"
    elif change == "missing":
        rows.pop()
    elif change == "extra":
        rows.append(deepcopy(rows[0]))
    new_rows = "".join(json.dumps(row) + "\n" for row in rows).encode()
    if change == "duplicate_key":
        new_rows = new_rows.replace(b"{", b'{"answer":"overwritten",', 1)
    with pytest.raises(ArchiveValidationError):
        consume(rewrite(raw, {"examples.jsonl": new_rows}))


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/tmp/outside",
        "assets/../../outside",
        "assets\\outside",
        "assets/" + "A" * 64,
        "notes.txt",
        "manifest.json\0hidden",
    ],
)
def test_archive_never_accepts_unsafe_or_extra_member_paths(name):
    pair, assets = make_pair()
    with pytest.raises(ArchiveValidationError, match="unsafe|duplicate"):
        consume(rewrite(pack(pair, assets), add=[(name, b"bad")]))


def test_zip_symlink_is_rejected_without_extracting():
    pair, assets = make_pair()
    info = zipfile.ZipInfo("assets/" + "a" * 64)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ArchiveValidationError, match="nonregular"):
        consume(rewrite(pack(pair, assets), add=[(info, b"/etc/passwd")]))


def test_duplicate_member_rejected_instead_of_last_writer_winning():
    pair, assets = make_pair()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        raw = rewrite(pack(pair, assets), add=[("manifest.json", b"{}")])
    with pytest.raises(ArchiveValidationError, match="duplicate"):
        consume(raw)


@pytest.mark.parametrize("change", ["missing", "extra", "hash", "crc"])
def test_original_media_integrity_and_exact_archive_membership(change):
    pair, assets = make_pair()
    raw = pack(pair, assets)
    path, data = next(iter(assets.items()))
    if change == "missing":
        raw = rewrite(raw, {"assets/" + path: None})
    elif change == "extra":
        raw = rewrite(raw, add=[("assets/" + "0" * 64, data)])
    elif change == "hash":
        other = next(value for key, value in assets.items() if key != path)
        raw = rewrite(raw, {"assets/" + path: other})
    else:
        index = raw.index(data)
        raw = raw[: index + 100] + bytes([raw[index + 100] ^ 1]) + raw[index + 101 :]
    with pytest.raises(ArchiveValidationError):
        consume(raw)


@pytest.mark.parametrize(
    "limit",
    [
        "max_archive_bytes",
        "max_uncompressed_bytes",
        "max_central_directory_bytes",
        "max_entries",
        "max_pairs",
        "max_metadata_bytes",
        "max_json_line_bytes",
        "max_asset_bytes",
    ],
)
def test_resource_limits_are_enforced(limit):
    pair, assets = make_pair()
    if limit == "max_pairs":
        second, _ = make_pair(session_id=pair["session_id"])
        raw = pack(make_release([pair, second]), assets)
    else:
        raw = pack(make_release([pair]), assets)
    with pytest.raises(ArchiveValidationError):
        consume(raw, limits=replace(ConsumerLimits(), **{limit: 1}))


def test_compression_bomb_is_rejected_before_reading_member_content():
    pair, assets = make_pair()
    raw = rewrite(pack(pair, assets), {"examples.jsonl": b" " * 1024 * 1024}, compress=zipfile.ZIP_DEFLATED)
    with pytest.raises(ArchiveValidationError, match="compression ratio"):
        consume(raw)


def test_ordinary_deflate_is_supported():
    pair, assets = make_pair()
    with consume(rewrite(pack(pair, assets), compress=zipfile.ZIP_DEFLATED)) as release:
        assert release.summary["example_count"] == 2


def test_malicious_directory_count_and_size_fail_before_zipfile_allocation(monkeypatch):
    pair, assets = make_pair()
    raw = bytearray(pack(pair, assets))
    eocd = len(raw) - 22
    struct.pack_into("<I", raw, eocd + 12, 128 * 1024**2)
    called = False

    def no_zipfile(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not allocate central directory")

    monkeypatch.setattr("counterpart.consumer.zipfile.ZipFile", no_zipfile)
    with pytest.raises(ArchiveValidationError, match="directory exceeds"):
        consume(raw)
    assert not called


@pytest.mark.parametrize(
    "payload",
    [
        b'{"x":NaN}',
        b'{"x":1e999}',
        b'{"schema_version":1,"schema_version":2}',
        b"\xff",
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_invalid_or_ambiguous_json_is_rejected(payload):
    pair, assets = make_pair()
    with pytest.raises(ArchiveValidationError, match="JSON"):
        consume(rewrite(pack(pair, assets), {"manifest.json": payload}))


@pytest.mark.postgres
@pytest.mark.parametrize("recipe", ["spatial", "temporal"])
def test_actual_collection_worker_review_archive_consumer_roundtrip(tmp_path, recipe):
    """Real PG transitions and writer; generated replay evidence, never ML truth."""
    dsn = os.environ.get("COUNTERPART_TEST_DSN", "postgresql:///counterpart_test")
    schema = "consumer_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(dsn, options=f"-c search_path={schema}")
    try:
        repo = Repository(scoped)
        repo.initialize()
        collections = CollectionRepository(repo)
        media = MediaStore(tmp_path / "media")
        config = Settings(database_url=scoped, media_dir=tmp_path / "media", live_enabled=False)
        worker = Worker(repo, media, config)
        campaign = collections.create_campaign("Portable dataset", recipe, mode="replay")
        invite = collections.create_invite(campaign["id"])
        session = collections.create_session_from_invite(invite["token"], uuid4().hex)["session"]
        for slot in ("baseline", "counterpart"):
            frames = [
                {
                    **media.put(frame["bytes"]),
                    "t_ms": frame["t_ms"],
                    "fixture_detections": frame["detections"],
                }
                for frame in generated(recipe, slot)
            ]
            key = uuid4().hex
            repo.submit(
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
            assert worker.once()
            session = repo.snapshot(session["id"])
            assert session[slot]["status"] == "accepted"
        pair = repo.publish(session["id"], session["revision"])
        collections.review_pair(
            campaign["id"],
            pair["id"],
            decision="approved",
            expected_version=0,
            reviewer="Roundtrip test",
            reason="Generated fixture",
            idempotency_key=uuid4().hex,
        )
        manifest = collections.export_manifests(campaign["id"], include_replay=True)
        assets = {
            frame["asset_id"]: media.read(frame["asset_id"])
            for slot in ("baseline", "counterpart")
            for frame in pair["manifest"][slot]["frames"]
        }
        with consume(pack(manifest, assets)) as release:
            assert release.manifest == manifest
            assert release.summary["pair_count"] == 1
            assert all(row["review"]["version"] == 1 for row in release.iter_examples())
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_declared_original_byte_count_must_match_actual_media():
    pair, assets = make_pair()
    raw = pack(pair, assets)
    pair["baseline"]["frames"][0]["bytes"] += 10
    with pytest.raises(ArchiveValidationError, match="declared hash, size, or dimensions"):
        consume(rewrite(raw, {"manifest.json": json.dumps(pair).encode()}))


def test_jsonl_cannot_substitute_requested_answer_for_observed_result():
    pair, assets = make_pair()
    raw = pack(pair, assets)
    pair["baseline"]["result"]["evidence"]["relation"] = "right"
    # Existing JSONL still says left, the requested action. The altered actual
    # evidence fails its own deterministic checks before JSONL can be consumed.
    with pytest.raises(ArchiveValidationError, match="deterministic verification"):
        consume(rewrite(raw, {"manifest.json": json.dumps(pair).encode()}))


def test_current_live_provenance_is_supported_without_network_calls():
    # Generated bytes and declared observations exercise schema only. This test
    # does not claim that these declarations came from an actual provider.
    pair, assets = make_pair()
    pair["mode"] = "live"
    pair["provenance"]["evidence_kind"] = "roboflow_capture"
    for slot in ("baseline", "counterpart"):
        for frame in pair[slot]["frames"]:
            frame["model"] = {"provider": "roboflow", "model_id": "declared-test-only"}
    with PortableRelease(io.BytesIO(pack(make_release([pair]), assets))) as release:
        assert release.evidence_kind == "roboflow_capture"


@pytest.mark.parametrize("field", ["provenance", "objects", "recipe_snapshot", "baseline", "split"])
def test_malformed_nested_types_raise_validation_error(field):
    pair, assets = make_pair()
    raw = pack(pair, assets)
    pair[field] = ["malformed"]
    with pytest.raises(ArchiveValidationError):
        consume(rewrite(raw, {"manifest.json": json.dumps(pair).encode()}))


def test_zip64_directory_is_validated_before_normal_consumption():
    pair, assets = make_pair()
    raw = pack(pair, assets)
    end = bytearray(raw[-22:])
    _, _, _, count, _, directory_size, directory_offset, _ = struct.unpack("<4s4H2IH", end)
    record_offset = len(raw) - 22
    record = struct.pack(
        "<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, directory_size, directory_offset
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, record_offset, 1)
    struct.pack_into("<2H2I", end, 8, 65535, 65535, 0xFFFFFFFF, 0xFFFFFFFF)
    archive = raw[:-22] + record + locator + end
    with consume(archive) as release:
        assert release.summary["pair_count"] == 1
    bad = bytearray(archive)
    struct.pack_into("<Q", bad, record_offset + 32, 999999)
    with pytest.raises(ArchiveValidationError):
        consume(bad)


def test_member_modified_after_validation_cannot_return_unchecked_bytes():
    pair, assets = make_pair()
    source = io.BytesIO(pack(pair, assets))
    with PortableRelease(source, allow_replay=True) as release:
        asset_id, data = next(iter(assets.items()))
        offset = source.getvalue().index(data) + 100
        source.seek(offset)
        source.write(bytes([data[100] ^ 1]))
        with pytest.raises(ArchiveValidationError, match="no longer readable"):
            release.read_media("assets/" + asset_id)
