"""Content-addressed immutable media; DB references follow durable file writes."""

import hashlib
import io
import os
import re
import tempfile
import warnings
from pathlib import Path

from PIL import Image

ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


class InvalidImage(ValueError):
    pass


class MediaStore:
    def __init__(self, root: Path, max_asset_bytes: int = 2 * 1024 * 1024):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_asset_bytes = max_asset_bytes

    def put(self, data: bytes) -> dict:
        if not data or len(data) > self.max_asset_bytes:
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
        target = self.path(asset_id)
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {
            "asset_id": asset_id,
            "width": width,
            "height": height,
            "media_type": media_type,
            "bytes": len(data),
        }

    def path(self, asset_id: str) -> Path:
        if not ASSET_ID.fullmatch(asset_id):
            raise InvalidImage("Invalid asset identifier.")
        return self.root / asset_id

    def read(self, asset_id: str) -> bytes:
        data = self.path(asset_id).read_bytes()
        if hashlib.sha256(data).hexdigest() != asset_id:
            raise InvalidImage("Stored evidence failed its integrity check.")
        return data
