import io

import pytest
from PIL import Image

from counterpart.storage import InvalidImage, MediaStore


def photo(size=(160, 120), image_format="JPEG"):
    data = io.BytesIO()
    Image.new("RGB", size, "blue").save(data, format=image_format)
    return data.getvalue()


@pytest.mark.parametrize("image_format", ["JPEG", "PNG"])
def test_immutable_storage_round_trip_and_integrity(tmp_path, image_format):
    store = MediaStore(tmp_path)
    raw = photo(image_format=image_format)
    first = store.put(raw)
    assert store.put(raw) == first
    assert store.read(first["asset_id"]) == raw
    store.path(first["asset_id"]).write_bytes(b"corrupt")
    with pytest.raises(InvalidImage, match="integrity"):
        store.read(first["asset_id"])


@pytest.mark.parametrize("data", [b"", b"not an image", photo((2561, 100)), photo()[:100]])
def test_rejects_invalid_media(tmp_path, data):
    with pytest.raises(InvalidImage):
        MediaStore(tmp_path).put(data)


def test_no_traversal_or_oversized_payload(tmp_path):
    store = MediaStore(tmp_path, max_asset_bytes=10)
    with pytest.raises(InvalidImage):
        store.path("../secret")
    with pytest.raises(InvalidImage):
        store.put(photo())
