"""Content-addressed immutable media; DB references follow durable file writes."""

import errno
import hashlib
import io
import os
import re
import stat
import tempfile
import warnings
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


class InvalidImage(ValueError):
    pass


def inspect_image(data: bytes, max_asset_bytes: int = 2 * 1024 * 1024) -> dict:
    if not data or len(data) > max_asset_bytes:
        raise InvalidImage("Each image must contain between 1 byte and 2 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in ("JPEG", "PNG"):
                    raise InvalidImage("Use a JPEG or PNG image.")
                width, height = im.size
                if min(width, height) < 80 or max(width, height) > 2560 or width * height > 4_000_000:
                    raise InvalidImage("Image dimensions must be 80–2560 pixels, at most 4 megapixels.")
                if getattr(im, "n_frames", 1) != 1 or im.getexif().get(274, 1) != 1:
                    raise InvalidImage("Use a single, upright camera frame without rotation metadata.")
                media_type = "image/jpeg" if im.format == "JPEG" else "image/png"
            # Metadata access (notably PNG EXIF) can load the image. Pillow's
            # verify must start from a fresh decoder, before pixel/EXIF reads.
            with Image.open(io.BytesIO(data)) as header:
                header.verify()
            # verify() does not decode all truncated pixel payloads.
            with Image.open(io.BytesIO(data)) as decoded:
                decoded.load()
    except InvalidImage:
        raise
    except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidImage("The image could not be safely decoded.") from None
    asset_id = hashlib.sha256(data).hexdigest()
    return {
        "asset_id": asset_id,
        "width": width,
        "height": height,
        "media_type": media_type,
        "bytes": len(data),
    }


class MediaStore:
    def __init__(self, root: Path, max_asset_bytes: int = 2 * 1024 * 1024):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_asset_bytes = max_asset_bytes

    def put(self, data: bytes) -> dict:
        metadata = inspect_image(data, self.max_asset_bytes)
        asset_id = metadata["asset_id"]
        target = self.path(asset_id)
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Both paths are on the same filesystem. link() creates the
                # target only if absent, unlike replace(). Concurrent writers
                # never overwrite evidence or silently repair corrupted bytes.
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                self.read(asset_id)
        finally:
            os.unlink(temporary)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return metadata

    def path(self, asset_id: str) -> Path:
        if not ASSET_ID.fullmatch(asset_id):
            raise InvalidImage("Invalid asset identifier.")
        return self.root / asset_id

    def read(self, asset_id: str) -> bytes:
        target = self.path(asset_id)
        try:
            # Nonblocking also prevents a planted FIFO/device from hanging a
            # request before fstat can reject its type. Evidence roots are
            # operator-controlled; callers cannot supply paths or filenames.
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise InvalidImage("Refusing to follow an evidence symlink.") from None
            raise
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise InvalidImage("Refusing to read a nonregular evidence file.")
            if not 0 < info.st_size <= self.max_asset_bytes:
                raise InvalidImage("Stored evidence has an invalid size.")
        except BaseException:
            os.close(descriptor)
            raise
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(self.max_asset_bytes + 1)
        if len(data) != info.st_size or hashlib.sha256(data).hexdigest() != asset_id:
            raise InvalidImage("Stored evidence failed its integrity check.")
        return data

    def iter_assets(self) -> Iterator[dict]:
        """Yield canonical regular files in digest order; never follow symlinks."""
        with os.scandir(self.root) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            if not ASSET_ID.fullmatch(entry.name):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            yield {
                "asset_id": entry.name,
                "modified_at": datetime.fromtimestamp(info.st_mtime, UTC),
                "bytes": info.st_size,
            }

    def delete(self, asset_id: str) -> bool:
        """Maintenance-only removal; caller must exclude DB reference writers."""
        target = self.path(asset_id)
        try:
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise InvalidImage("Refusing to remove a nonregular evidence file.")
            target.unlink()
        except FileNotFoundError:
            return False
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
