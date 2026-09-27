"""S3-compatible immutable evidence, with independent byte-integrity verification.

Only operator configuration can choose an endpoint. User assets are always digest
identifiers, never URLs. The S3 provider must implement conditional PutObject; we
intentionally do not fall back to an unconditional write on incompatible servers.
"""

import base64
import hashlib
import ipaddress
import re
from collections.abc import Iterator
from datetime import UTC
from typing import Protocol
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings
from .storage import ASSET_ID, InvalidImage, MediaStore, inspect_image


class MediaUnavailable(OSError):
    """Retryable transport/service problem, with no provider secrets in its text."""


class EvidenceStore(Protocol):
    def put(self, data: bytes) -> dict: ...

    def read(self, asset_id: str) -> bytes: ...

    def iter_assets(self) -> Iterator[dict]: ...

    def delete(self, asset_id: str) -> bool: ...


def _endpoint(value: str | None, allow_insecure: bool) -> str | None:
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid object storage endpoint.") from None
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port == 0
    ):
        raise ValueError("Use an object storage origin without credentials, paths, or query parameters.")
    # Test emulators can bind loopback without TLS. All remote endpoints require
    # HTTPS, even if this test-only flag is accidentally enabled in production.
    loopback = parsed.hostname == "localhost"
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        loopback = address.is_loopback
        if address.is_link_local or address.is_unspecified or address.is_multicast:
            raise ValueError("Unsafe object storage endpoint address.")
    if parsed.scheme != "https" and not (allow_insecure and loopback):
        raise ValueError("Object storage requires HTTPS; HTTP is restricted to loopback test emulators.")
    return value.rstrip("/")


class S3MediaStore:
    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "counterpart/media",
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        allow_insecure: bool = False,
        max_asset_bytes: int = 2 * 1024 * 1024,
        client=None,
    ):
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
            or ".." in bucket
            or bucket.endswith("--x-s3")
        ):
            raise ValueError("Use a standard S3 bucket name.")
        if prefix and not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", prefix):
            raise ValueError("Object prefix must contain simple, slash-separated path components.")
        if not 0 < max_asset_bytes <= 24 * 1024 * 1024:
            raise ValueError("Invalid maximum image size.")
        endpoint_url = _endpoint(endpoint_url, allow_insecure)
        self.bucket, self.prefix, self.max_asset_bytes = bucket, prefix, max_asset_bytes
        # Credentials use boto3's standard environment/workload-identity chain.
        # No bucket creation, public ACL, or presigned URL is exposed. Maintenance
        # listing/deletion requires a separately privileged identity and GC lock.
        self.client = client or boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(
                signature_version="s3v4",
                ignore_configured_endpoint_urls=True,
                connect_timeout=3,
                read_timeout=15,
                retries={"mode": "standard", "total_max_attempts": 3},
                s3={"addressing_style": "path" if endpoint_url else "auto"},
            ),
        )

    def key(self, asset_id: str) -> str:
        if not ASSET_ID.fullmatch(asset_id):
            raise InvalidImage("Invalid asset identifier.")
        relative = f"{asset_id[:2]}/{asset_id}"
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def put(self, data: bytes) -> dict:
        metadata = inspect_image(data, self.max_asset_bytes)
        asset_id = metadata["asset_id"]
        for attempt in range(3):
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self.key(asset_id),
                    Body=data,
                    ContentLength=len(data),
                    ContentType=metadata["media_type"],
                    Metadata={"sha256": asset_id},
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(asset_id)).decode("ascii"),
                    IfNoneMatch="*",
                )
                return metadata
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if code == "PreconditionFailed" or status == 412:
                    # Existing metadata/ETag cannot prove the bytes are intact.
                    # Verify the full bounded object before calling this a replay.
                    self.read(asset_id)
                    return metadata
                if (code == "ConditionalRequestConflict" or status == 409) and attempt < 2:
                    continue
                raise MediaUnavailable("Evidence storage could not complete the upload.") from None
            except BotoCoreError:
                # A timeout may follow a successful PUT. Its safe retry is another
                # conditional PUT, which subsequently verifies the existing object.
                raise MediaUnavailable("Evidence storage could not complete the upload.") from None
        raise MediaUnavailable("Evidence storage could not complete the upload.")

    def read(self, asset_id: str) -> bytes:
        key = self.key(asset_id)
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "NotFound", "404"}:
                raise FileNotFoundError("Evidence asset was not found.") from None
            raise MediaUnavailable("Evidence storage could not read this asset.") from None
        except BotoCoreError:
            raise MediaUnavailable("Evidence storage could not read this asset.") from None
        body = result["Body"]
        try:
            size = result.get("ContentLength")
            if not isinstance(size, int) or not 0 < size <= self.max_asset_bytes:
                raise InvalidImage("Stored evidence has an invalid size.")
            data = body.read(self.max_asset_bytes + 1)
        except BotoCoreError:
            raise MediaUnavailable("Evidence storage could not read this asset.") from None
        finally:
            body.close()
        if len(data) != size or hashlib.sha256(data).hexdigest() != asset_id:
            raise InvalidImage("Stored evidence failed its integrity check.")
        return data

    def iter_assets(self) -> Iterator[dict]:
        """List only this store's canonical digest keys, without loading images.

        Requires maintenance ListBucket permission and a standard (not directory)
        bucket with lexicographically ordered ListObjectsV2. The shard key retains
        digest order. Fail closed if a provider violates it, so cursor-based GC
        cannot silently skip objects. Callers must synchronize reference writers.
        """
        prefix = f"{self.prefix}/" if self.prefix else ""
        previous = ""
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key", "")
                    if not isinstance(key, str):
                        raise MediaUnavailable("Evidence inventory contains an invalid key.")
                    asset_id = key.rsplit("/", 1)[-1]
                    if not ASSET_ID.fullmatch(asset_id) or self.key(asset_id) != key:
                        continue
                    if asset_id <= previous:
                        raise MediaUnavailable("Evidence inventory order is inconsistent.")
                    previous = asset_id
                    modified = item.get("LastModified")
                    size = item.get("Size")
                    if (
                        not hasattr(modified, "utcoffset")
                        or modified.utcoffset() is None
                        or not isinstance(size, int)
                        or size < 0
                    ):
                        raise MediaUnavailable("Evidence inventory contains invalid metadata.")
                    yield {"asset_id": asset_id, "modified_at": modified.astimezone(UTC), "bytes": size}
        except (BotoCoreError, ClientError):
            raise MediaUnavailable("Evidence storage inventory is unavailable.") from None

    def delete(self, asset_id: str) -> bool:
        """Maintenance-only current-object removal, after exclusive reference lock.

        This deliberately does not delete historical S3 versions. A success means
        the idempotent deletion was accepted, including when already absent.
        """
        key = self.key(asset_id)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError):
            raise MediaUnavailable("Evidence storage could not remove this orphan.") from None
        return True


def create_media_store(config: Settings) -> EvidenceStore:
    if config.storage_backend == "filesystem":
        return MediaStore(config.media_dir, config.max_asset_bytes)
    if config.storage_backend != "s3":
        raise ValueError("Unknown evidence storage backend.")
    return S3MediaStore(
        config.s3_bucket,
        prefix=config.s3_prefix,
        region=config.s3_region,
        endpoint_url=config.s3_endpoint_url,
        allow_insecure=config.s3_allow_insecure,
        max_asset_bytes=config.max_asset_bytes,
    )
