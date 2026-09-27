"""Real localhost HTTP S3 emulator plus hostile transport/error contract tests.

The emulator is not evidence that every S3-compatible provider supports the
conditional-write API. No cloud account or model compute is involved.
"""

import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto.server import ThreadedMotoServer
from PIL import Image

from counterpart.config import Settings
from counterpart.object_storage import MediaUnavailable, S3MediaStore, create_media_store
from counterpart.storage import InvalidImage, MediaStore


def image_bytes(color="blue", image_format="JPEG"):
    output = io.BytesIO()
    Image.new("RGB", (160, 120), color).save(output, format=image_format)
    return output.getvalue()


@pytest.fixture(scope="module")
def s3_service():
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url=endpoint,
        aws_access_key_id="local-emulator",
        aws_secret_access_key="local-emulator",
    )
    client.create_bucket(Bucket="counterpart-tests")
    client.put_bucket_versioning(Bucket="counterpart-tests", VersioningConfiguration={"Status": "Enabled"})
    yield SimpleNamespace(client=client, endpoint=endpoint)
    server.stop()


@pytest.fixture
def remote(s3_service):
    return S3MediaStore(
        "counterpart-tests",
        client=s3_service.client,
        endpoint_url=s3_service.endpoint,
        allow_insecure=True,
    )


@pytest.mark.parametrize("image_format", ["JPEG", "PNG"])
def test_real_http_roundtrip_identical_metadata_and_replay(remote, tmp_path, image_format):
    data = image_bytes(image_format=image_format)
    metadata = remote.put(data)
    assert metadata == MediaStore(tmp_path).put(data)
    assert remote.read(metadata["asset_id"]) == data
    assert remote.put(data) == metadata
    versions = remote.client.list_object_versions(
        Bucket=remote.bucket,
        Prefix=remote.key(metadata["asset_id"]),
    )
    assert len(versions["Versions"]) == 1, "A replay must not overwrite even on a versioned bucket."


def test_real_http_concurrent_duplicate_writers_create_one_version(remote):
    data = image_bytes("green")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(remote.put, [data] * 16))
    assert all(metadata == results[0] for metadata in results)
    versions = remote.client.list_object_versions(
        Bucket=remote.bucket,
        Prefix=remote.key(results[0]["asset_id"]),
    )
    assert len(versions["Versions"]) == 1


def test_real_http_corrupt_existing_key_cannot_be_silently_repaired(remote):
    data = image_bytes("red")
    asset_id = hashlib.sha256(data).hexdigest()
    remote.client.put_object(Bucket=remote.bucket, Key=remote.key(asset_id), Body=b"corrupt")
    with pytest.raises(InvalidImage, match="integrity"):
        remote.read(asset_id)
    with pytest.raises(InvalidImage, match="integrity"):
        remote.put(data)
    body = remote.client.get_object(Bucket=remote.bucket, Key=remote.key(asset_id))["Body"]
    try:
        assert body.read() == b"corrupt"
    finally:
        body.close()


def test_real_http_missing_asset(remote):
    with pytest.raises(FileNotFoundError):
        remote.read("f" * 64)


def test_real_http_inventory_scopes_to_canonical_keys_and_deletes_current_only(s3_service):
    store = S3MediaStore("counterpart-tests", prefix="maintenance", client=s3_service.client)
    data = image_bytes("orange")
    metadata = store.put(data)
    asset_id = metadata["asset_id"]
    for key in [f"maintenance/ff/{asset_id}", f"maintenance/{asset_id}", "maintenance/unknown"]:
        store.client.put_object(Bucket=store.bucket, Key=key, Body=b"not ours")
    rows = list(store.iter_assets())
    assert len(rows) == 1
    assert rows[0]["asset_id"] == asset_id
    assert rows[0]["bytes"] == len(data)
    assert rows[0]["modified_at"].utcoffset().total_seconds() == 0
    assert store.delete(asset_id)
    assert list(store.iter_assets()) == []
    with pytest.raises(FileNotFoundError):
        store.read(asset_id)
    versions = store.client.list_object_versions(Bucket=store.bucket, Prefix=store.key(asset_id))
    assert len(versions["Versions"]) == 1  # Historical bytes intentionally remain.
    assert len(versions["DeleteMarkers"]) == 1


def test_maintenance_errors_are_sanitized_and_keys_validated():
    client = Mock()
    client.delete_object.side_effect = error("AccessDenied", 403)
    store = S3MediaStore("counterpart-tests", client=client)
    with pytest.raises(InvalidImage):
        store.delete("../somebody-elses-data")
    client.delete_object.assert_not_called()
    with pytest.raises(MediaUnavailable) as caught:
        store.delete("a" * 64)
    assert "secret" not in str(caught.value)
    client.get_paginator.side_effect = error("AccessDenied", 403)
    with pytest.raises(MediaUnavailable):
        list(store.iter_assets())


def test_inventory_enforces_order_across_pages_for_safe_gc_cursor():
    client = Mock()
    store = S3MediaStore("counterpart-tests", client=client)
    asset_ids = ["b" * 64, "a" * 64]
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": store.key(asset_id), "LastModified": datetime.now(UTC), "Size": 120}]}
        for asset_id in asset_ids
    ]
    iterator = store.iter_assets()
    assert next(iterator)["asset_id"] == asset_ids[0]
    with pytest.raises(MediaUnavailable, match="order"):
        next(iterator)


def test_invalid_input_never_touches_transport():
    client = Mock()
    store = S3MediaStore("counterpart-tests", client=client)
    for value in [b"", b"invalid", image_bytes()[:100]]:
        with pytest.raises(InvalidImage):
            store.put(value)
    for value in ["../secret", "https://example.com/private", "g" * 64, "F" * 64]:
        with pytest.raises(InvalidImage):
            store.read(value)
    assert client.mock_calls == []


def error(code, status):
    return ClientError(
        {
            "Error": {"Code": code, "Message": "secret provider details"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


def test_conflict_is_bounded_and_keeps_conditional_write():
    client = Mock()
    client.put_object.side_effect = [error("ConditionalRequestConflict", 409), {}]
    store = S3MediaStore("counterpart-tests", client=client)
    store.put(image_bytes())
    assert client.put_object.call_count == 2
    assert all(call.kwargs["IfNoneMatch"] == "*" for call in client.put_object.call_args_list)
    assert "ChecksumSHA256" in client.put_object.call_args.kwargs


def test_persistent_conflict_and_unsupported_condition_fail_closed():
    for failure, calls in [(error("ConditionalRequestConflict", 409), 3), (error("NotImplemented", 501), 1)]:
        client = Mock()
        client.put_object.side_effect = failure
        store = S3MediaStore("counterpart-tests", client=client)
        with pytest.raises(MediaUnavailable) as caught:
            store.put(image_bytes())
        assert "secret" not in str(caught.value)
        assert client.put_object.call_count == calls
        assert all(call.kwargs["IfNoneMatch"] == "*" for call in client.put_object.call_args_list)


def test_ambiguous_transport_failure_is_sanitized_and_safe_to_retry(remote):
    data = image_bytes("yellow")
    actual_put = remote.client.put_object

    def write_then_timeout(**arguments):
        actual_put(**arguments)
        raise EndpointConnectionError(endpoint_url="https://secret.example")

    client = Mock(wraps=remote.client)
    client.put_object.side_effect = write_then_timeout
    first = S3MediaStore(remote.bucket, client=client)
    with pytest.raises(MediaUnavailable) as caught:
        first.put(data)
    assert "secret.example" not in str(caught.value)
    metadata = remote.put(data)
    assert remote.read(metadata["asset_id"]) == data
    versions = remote.client.list_object_versions(
        Bucket=remote.bucket,
        Prefix=remote.key(metadata["asset_id"]),
    )
    assert len(versions["Versions"]) == 1


@pytest.mark.parametrize("length,payload", [(999999999, b"x"), (0, b""), (10, b"x"), (None, b"x")])
def test_read_enforces_bounded_size_and_always_closes_stream(length, payload):
    stream = Mock(wraps=io.BytesIO(payload))
    client = Mock()
    client.get_object.return_value = {"ContentLength": length, "Body": stream}
    store = S3MediaStore("counterpart-tests", client=client)
    with pytest.raises(InvalidImage):
        store.read("a" * 64)
    stream.close.assert_called_once()
    if stream.read.called:
        stream.read.assert_called_once_with(store.max_asset_bytes + 1)


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///etc/passwd",
        "https://user:secret@example.com",
        "https://example.com?key=secret",
        "https://example.com/private",
        "http://example.com",
        "http://169.254.169.254",
        "http://0.0.0.0",
        "https://example.com:0",
        "https://example.com:wrong",
    ],
)
def test_endpoint_is_operator_origin_not_asset_url(endpoint):
    with pytest.raises(ValueError):
        S3MediaStore("counterpart-tests", endpoint_url=endpoint, allow_insecure=True, client=Mock())


def test_loopback_http_requires_explicit_test_flag():
    with pytest.raises(ValueError, match="HTTPS"):
        S3MediaStore("counterpart-tests", endpoint_url="http://127.0.0.1:5000", client=Mock())
    S3MediaStore(
        "counterpart-tests",
        endpoint_url="http://127.0.0.1:5000",
        allow_insecure=True,
        client=Mock(),
    )


def test_generic_sdk_endpoint_environment_cannot_bypass_explicit_validation(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "local-test-only")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "local-test-only")
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "http://169.254.169.254")
    store = S3MediaStore("counterpart-tests")
    assert store.client.meta.endpoint_url.startswith("https://s3.")
    # Client construction does not make a storage request.


@pytest.mark.parametrize("prefix", ["../private", "/absolute", "path/../private", "https://host", "a//b"])
def test_rejects_ambiguous_key_prefix(prefix):
    with pytest.raises(ValueError):
        S3MediaStore("counterpart-tests", prefix=prefix, client=Mock())


def test_factory_preserves_filesystem_default(tmp_path):
    store = create_media_store(Settings(media_dir=tmp_path))
    assert isinstance(store, MediaStore)


def test_filesystem_inventory_and_delete_never_follow_links(tmp_path):
    store = MediaStore(tmp_path / "media")
    data = image_bytes("pink")
    metadata = store.put(data)
    external = tmp_path / "external"
    external.write_bytes(b"must remain untouched")
    (store.root / ("f" * 64)).symlink_to(external)
    (store.root / ("a" * 64)).mkdir()
    (store.root / ".upload-in-progress").write_bytes(b"not a stored asset")
    rows = list(store.iter_assets())
    assert [row["asset_id"] for row in rows] == [metadata["asset_id"]]
    assert rows[0]["modified_at"].utcoffset().total_seconds() == 0
    assert rows[0]["bytes"] == len(data)
    with pytest.raises(InvalidImage, match="nonregular"):
        store.delete("f" * 64)
    with pytest.raises(InvalidImage, match="nonregular"):
        store.delete("a" * 64)
    assert external.read_bytes() == b"must remain untouched"
    with pytest.raises(InvalidImage):
        store.delete("../external")
    assert store.delete(metadata["asset_id"])
    assert not store.delete(metadata["asset_id"])
    assert list(store.iter_assets()) == []


def test_filesystem_inventory_sorts_for_resumable_gc(tmp_path):
    store = MediaStore(tmp_path)
    for color in ["blue", "red", "green", "yellow"]:
        store.put(image_bytes(color))
    rows = list(store.iter_assets())
    assert [row["asset_id"] for row in rows] == sorted(row["asset_id"] for row in rows)


def test_filesystem_concurrent_writers_reuse_original_inode(tmp_path):
    store = MediaStore(tmp_path)
    data = image_bytes("brown")
    first = store.put(data)
    original = store.path(first["asset_id"]).stat()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(store.put, [data] * 24))
    assert all(result == first for result in results)
    current = store.path(first["asset_id"]).stat()
    assert (current.st_ino, current.st_mtime_ns) == (original.st_ino, original.st_mtime_ns)
    assert store.read(first["asset_id"]) == data
    assert not list(store.root.glob(".upload-*"))


def test_filesystem_concurrent_first_writes_create_one_original(tmp_path):
    store = MediaStore(tmp_path)
    data = image_bytes("cyan")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(store.put, [data] * 24))
    assert all(result == results[0] for result in results)
    assert len(list(store.iter_assets())) == 1
    assert store.read(results[0]["asset_id"]) == data


def test_filesystem_corrupt_duplicate_does_not_overwrite_or_leave_temporary(tmp_path):
    store = MediaStore(tmp_path)
    data = image_bytes()
    metadata = store.put(data)
    store.path(metadata["asset_id"]).write_bytes(b"corrupt")
    with pytest.raises(InvalidImage, match="integrity"):
        store.put(data)
    assert store.path(metadata["asset_id"]).read_bytes() == b"corrupt"
    assert not list(store.root.glob(".upload-*"))


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_filesystem_reads_and_replays_refuse_nonregular_evidence(tmp_path, kind):
    store = MediaStore(tmp_path / "media")
    data = image_bytes()
    asset_id = hashlib.sha256(data).hexdigest()
    target = store.path(asset_id)
    external = tmp_path / "outside"
    external.write_bytes(data)
    if kind == "symlink":
        target.symlink_to(external)
    elif kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)
    with pytest.raises(InvalidImage):
        store.read(asset_id)
    with pytest.raises(InvalidImage):
        store.put(data)
    assert external.read_bytes() == data
    assert not list(store.root.glob(".upload-*"))


def test_filesystem_read_bounds_even_when_stored_file_is_hostile(tmp_path):
    store = MediaStore(tmp_path, max_asset_bytes=1024)
    store.path("a" * 64).write_bytes(b"x" * 2048)
    with pytest.raises(InvalidImage, match="size"):
        store.read("a" * 64)


def test_factory_s3_uses_environment_credentials_only(s3_service, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "local-emulator")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "local-emulator")
    store = create_media_store(
        Settings(
            storage_backend="s3",
            s3_bucket="counterpart-tests",
            s3_endpoint_url=s3_service.endpoint,
            s3_allow_insecure=True,
        )
    )
    data = image_bytes("purple")
    assert store.read(store.put(data)["asset_id"]) == data
