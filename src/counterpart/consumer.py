"""Bounded, offline validation and consumption of portable Counterpart archives.

This checks internal consistency, not who created the archive or whether a
model/human observation is true. No archive member is extracted to disk and no
model, database, cloud service, or training framework is loaded.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import stat
import struct
import zipfile
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from .dataset import examples as pair_examples
from .evidence_revisions import HUMAN_BASIS, evaluate, validate_adjudication
from .execution_contract import assert_observation_matches, validate_execution_contract
from .recipes import RecipeContractError, assert_runtime_compatible, validate_recipe_snapshot
from .repository import Conflict
from .splits import split_for_session, validate_split_group
from .storage import InvalidImage, inspect_image

_ASSET = re.compile(r"assets/([0-9a-f]{64})\Z")
_REVIEW_BASIS = (
    "Machine evidence accepted in an explicit human review; review is not independent ground truth."
)
_SPLITS = {"train", "valid", "test"}


class ArchiveValidationError(ValueError):
    """The portable dataset is malformed, unsupported, or internally inconsistent."""


@dataclass(frozen=True)
class ConsumerLimits:
    """Explicit admission bounds; increase deliberately for trusted large releases."""

    max_archive_bytes: int = 2 * 1024**3
    max_uncompressed_bytes: int = 4 * 1024**3
    max_central_directory_bytes: int = 32 * 1024**2
    max_entries: int = 100_000
    max_pairs: int = 10_000
    max_metadata_bytes: int = 32 * 1024**2
    max_json_line_bytes: int = 512 * 1024
    max_asset_bytes: int = 2 * 1024**2
    max_compression_ratio: int = 100

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("Every archive resource limit must be a positive integer.")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ArchiveValidationError(message)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in items:
            _require(key not in result, "JSON contains a duplicate object key.")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ArchiveValidationError("JSON numbers must be finite.")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        # 1e999 is legal JSON syntax but Python otherwise decodes it to infinity.
        _canonical(value)
        return value
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError, OverflowError) as error:
        if isinstance(error, ArchiveValidationError):
            raise
        raise ArchiveValidationError("Archive metadata must contain bounded, finite UTF-8 JSON.") from None


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _date(value: object) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _directory_preflight(source: BinaryIO, limits: ConsumerLimits) -> None:
    """Bound ZIP central-directory allocation *before* constructing ZipFile.

    ZIP64 directories are supported but multivolume archives and prepended
    executables are not. A strict final EOCD rejects trailing junk/ambiguity.
    """
    source.seek(0, io.SEEK_END)
    size = source.tell()
    _require(22 <= size <= limits.max_archive_bytes, "Archive byte size exceeds the admission limit.")
    source.seek(max(0, size - (65535 + 22)))
    tail = source.read(65535 + 22)
    offset = tail.rfind(b"PK\x05\x06")
    _require(offset >= 0 and len(tail) - offset >= 22, "ZIP end-of-directory record is missing.")
    end = tail[offset : offset + 22]
    _, disk, cd_disk, on_disk, count, cd_size, cd_offset, comment_size = struct.unpack("<4s4H2IH", end)
    eocd_offset = size - len(tail) + offset
    _require(offset + 22 + comment_size == len(tail), "ZIP has trailing or ambiguous directory data.")
    _require(disk == cd_disk == 0 and on_disk == count, "Multivolume ZIP archives are unsupported.")
    if 65535 in (on_disk, count) or 0xFFFFFFFF in (cd_size, cd_offset):
        _require(eocd_offset >= 20, "ZIP64 directory locator is missing.")
        source.seek(eocd_offset - 20)
        signature, directory_disk, record_offset, disk_count = struct.unpack("<4sIQI", source.read(20))
        _require(
            signature == b"PK\x06\x07" and directory_disk == 0 and disk_count == 1,
            "ZIP64 directory locator is invalid.",
        )
        _require(0 <= record_offset <= eocd_offset - 76, "ZIP64 directory offset is invalid.")
        source.seek(record_offset)
        record = struct.unpack("<4sQ2H2I4Q", source.read(56))
        signature, record_size, _, _, disk, cd_disk, on_disk, count, cd_size, cd_offset = record
        _require(
            signature == b"PK\x06\x06"
            and record_size >= 44
            and record_offset + record_size + 12 == eocd_offset - 20
            and disk == cd_disk == 0
            and on_disk == count,
            "ZIP64 directory record is invalid.",
        )
        directory_end = record_offset
    else:
        directory_end = eocd_offset
    _require(2 <= count <= limits.max_entries, "ZIP entry count exceeds the admission limit.")
    _require(0 < cd_size <= limits.max_central_directory_bytes, "ZIP directory exceeds the admission limit.")
    _require(0 <= cd_offset and cd_offset + cd_size == directory_end, "ZIP directory extent is invalid.")
    source.seek(0)
    _require(source.read(4) == b"PK\x03\x04", "ZIP must start with a member, without an executable prefix.")
    source.seek(0)


class PortableRelease:
    """Validate once, then iterate detached VQA rows and read hash-checked media.

    Pass a path or a seekable binary stream. Use as a context manager. The
    caller owns a supplied stream; this object owns streams it opens itself.
    Fixture archives require ``allow_replay=True`` so a pipeline cannot ingest
    generated demonstrations as real model evidence by accident.
    """

    def __init__(
        self,
        source: str | Path | BinaryIO,
        *,
        limits: ConsumerLimits | None = None,
        allow_replay: bool = False,
    ) -> None:
        self.limits = limits or ConsumerLimits()
        self._owned = isinstance(source, (str, Path))
        self._source = Path(source).open("rb") if self._owned else source
        self._archive: zipfile.ZipFile | None = None
        self._closed = False
        self._assets: dict[str, dict] = {}
        self._rows: list[dict] = []
        try:
            _directory_preflight(self._source, self.limits)
            self._archive = zipfile.ZipFile(self._source, "r")
            self._index_members()
            manifest = _json(self._read("manifest.json", self.limits.max_metadata_bytes))
            _require(isinstance(manifest, dict), "Manifest must be a JSON object.")
            self._manifest = manifest
            self._validate_manifest(allow_replay)
            self._validate_rows()
            expected_members = {"manifest.json", "examples.jsonl", *self._assets}
            _require(set(self._entries) == expected_members, "ZIP has missing or unreferenced members.")
            # Decode one bounded image at a time. Hash, dimensions, media type,
            # and original byte count all refer to the actual original bytes.
            for path, expected in self._assets.items():
                raw = self._read(path, self.limits.max_asset_bytes)
                try:
                    actual = inspect_image(raw, self.limits.max_asset_bytes)
                except InvalidImage:
                    raise ArchiveValidationError(
                        "An asset is not a supported bounded camera image."
                    ) from None
                _require(actual == expected, "An asset differs from its declared hash, size, or dimensions.")
        except (
            ArchiveValidationError,
            Conflict,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OverflowError,
            RecursionError,
            struct.error,
        ) as error:
            self.close()
            if isinstance(error, ArchiveValidationError):
                raise
            raise ArchiveValidationError(
                "Archive metadata or member data is malformed or unreadable."
            ) from None
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, NotImplementedError):
            self.close()
            raise ArchiveValidationError("ZIP member integrity or compression is invalid.") from None

    def _index_members(self) -> None:
        assert self._archive is not None
        infos = self._archive.infolist()
        _require(len(infos) <= self.limits.max_entries, "ZIP entry count exceeds the admission limit.")
        self._entries: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for info in infos:
            path = info.filename
            _require(
                info.orig_filename == path
                and (path in {"manifest.json", "examples.jsonl"} or _ASSET.fullmatch(path) is not None),
                "ZIP contains an unsafe or unsupported member path.",
            )
            _require(path not in self._entries, "ZIP contains duplicate member names.")
            mode = stat.S_IFMT(info.external_attr >> 16)
            _require(mode in {0, stat.S_IFREG}, "ZIP contains a nonregular member.")
            _require(not info.flag_bits & 1, "Encrypted ZIP members are unsupported.")
            _require(
                info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                "ZIP uses unsupported compression.",
            )
            maximum = (
                self.limits.max_asset_bytes if path.startswith("assets/") else self.limits.max_metadata_bytes
            )
            _require(0 < info.file_size <= maximum, "ZIP member size exceeds the admission limit.")
            _require(
                info.file_size <= max(1, info.compress_size) * self.limits.max_compression_ratio,
                "ZIP compression ratio exceeds the admission limit.",
            )
            total += info.file_size
            _require(
                total <= self.limits.max_uncompressed_bytes, "ZIP expanded size exceeds the admission limit."
            )
            self._entries[path] = info
        _require(
            {"manifest.json", "examples.jsonl"} <= self._entries.keys(), "ZIP is missing required metadata."
        )

    def _read(self, path: str, maximum: int) -> bytes:
        assert self._archive is not None
        _require(path in self._entries, "ZIP is missing a referenced member.")
        with self._archive.open(self._entries[path]) as stream:
            raw = stream.read(maximum + 1)
        _require(
            len(raw) == self._entries[path].file_size and len(raw) <= maximum,
            "ZIP member size differs from its declaration or exceeds the limit.",
        )
        return raw

    def _validate_manifest(self, allow_replay: bool) -> None:
        manifest = self._manifest
        schema = manifest.get("schema_version")
        self._release = schema == "counterpart.dataset.v1"
        _require(
            self._release or schema in {"counterpart.pair.v1", "counterpart.pair.v2"},
            "Unsupported manifest schema version.",
        )
        if self._release:
            required = {
                "id",
                "campaign_id",
                "schema_version",
                "created_at",
                "recipe_snapshot",
                "evidence_kind",
                "pair_count",
                "pairs",
                "split_counts",
                "content_sha256",
            }
            _require(set(manifest) == required, "Dataset release fields do not match its schema.")
            _require(_uuid(manifest["id"]) and _uuid(manifest["campaign_id"]), "Release identity is invalid.")
            _require(_date(manifest["created_at"]), "Release creation time must include its time zone.")
            payload = {k: v for k, v in manifest.items() if k not in {"id", "created_at", "content_sha256"}}
            digest = hashlib.sha256(_canonical(payload)).hexdigest()
            _require(
                manifest["content_sha256"] == digest, "Release content digest does not match its membership."
            )
            members = manifest["pairs"]
            _require(isinstance(members, list), "Release members must be a list.")
            _require(
                _integer(manifest["pair_count"], 1)
                and manifest["pair_count"] == len(members)
                and len(members) <= self.limits.max_pairs,
                "Release pair count is inconsistent or exceeds the admission limit.",
            )
            kind = manifest["evidence_kind"]
        else:
            members = [{"manifest": manifest}]
            provenance = manifest.get("provenance")
            _require(isinstance(provenance, dict), "Pair provenance must be an object.")
            kind = provenance.get("evidence_kind")
        _require(kind in {"roboflow_capture", "fixture_replay"}, "Unsupported evidence provenance.")
        _require(
            allow_replay or kind != "fixture_replay", "Fixture replay requires explicit allow_replay=True."
        )
        self.evidence_kind = kind
        pair_ids, session_ids, capture_ids = set(), set(), set()
        asset_splits: dict[str, str] = {}
        split_counts = dict.fromkeys(sorted(_SPLITS), 0)
        for member in members:
            _require(isinstance(member, dict), "Release member must be an object.")
            if self._release:
                _require(set(member) == {"manifest", "review"}, "Release member fields are invalid.")
                self._validate_review(member["review"])
            pair = member["manifest"]
            self._validate_pair(pair, kind)
            _require(pair["pair_id"] not in pair_ids, "Release repeats a pair identity.")
            _require(pair["session_id"] not in session_ids, "Release repeats a collection session.")
            pair_ids.add(pair["pair_id"])
            session_ids.add(pair["session_id"])
            if self._release:
                _require(
                    pair["campaign_id"] == manifest["campaign_id"], "Pair belongs to a different campaign."
                )
                _require(
                    pair["recipe_snapshot"] == manifest["recipe_snapshot"],
                    "Pair recipe differs from the frozen release recipe.",
                )
            split = pair["split"]
            split_counts[split] += 1
            for slot in ("baseline", "counterpart"):
                capture = pair[slot]
                _require(capture["id"] not in capture_ids, "Release repeats a capture identity.")
                capture_ids.add(capture["id"])
                for frame in capture["frames"]:
                    path = f"assets/{frame['asset_id']}"
                    expected = {
                        key: frame[key] for key in ("asset_id", "width", "height", "media_type", "bytes")
                    }
                    if path in self._assets:
                        _require(self._assets[path] == expected, "Repeated asset metadata is inconsistent.")
                        _require(
                            asset_splits[path] == split,
                            "Identical media appears in different dataset splits.",
                        )
                    else:
                        self._assets[path], asset_splits[path] = expected, split
            for row in pair_examples(pair):
                if self._release:
                    row.update(
                        human_reviewed=True,
                        review=member["review"],
                        release_id=manifest["id"],
                        label_basis=HUMAN_BASIS
                        if pair["schema_version"] == "counterpart.pair.v2"
                        else _REVIEW_BASIS,
                    )
                self._rows.append(row)
        if self._release:
            _require(
                isinstance(manifest["split_counts"], dict)
                and all(_integer(v) for v in manifest["split_counts"].values())
                and manifest["split_counts"] == split_counts,
                "Release split counts differ from its actual membership.",
            )
        self._split_counts = split_counts

    @staticmethod
    def _validate_review(review: object) -> None:
        _require(isinstance(review, dict), "Release member must include its frozen human review.")
        _require(
            set(review) == {"status", "version", "reviewer", "reason", "updated_at"}
            and review["status"] == "approved"
            and _integer(review["version"], 1)
            and isinstance(review["reviewer"], str)
            and bool(review["reviewer"].strip())
            and isinstance(review["reason"], str)
            and _date(review["updated_at"]),
            "Release requires an approved, versioned human review.",
        )

    def _validate_pair(self, pair: object, kind: str) -> None:
        _require(
            isinstance(pair, dict)
            and pair.get("schema_version") in {"counterpart.pair.v1", "counterpart.pair.v2"},
            "Invalid pair schema.",
        )
        human = pair["schema_version"] == "counterpart.pair.v2"
        try:
            snapshot = validate_recipe_snapshot(pair.get("recipe_snapshot"))
            assert_runtime_compatible(snapshot)
        except RecipeContractError:
            raise ArchiveValidationError(
                "Missing, altered, or unsupported frozen recipe; legacy exports are unsupported."
            ) from None
        required = {
            "schema_version",
            "pair_id",
            "session_id",
            "recipe",
            "mode",
            "revision",
            "objects",
            "recipe_snapshot",
            "campaign_id",
            "split_group",
            "split",
            "baseline",
            "counterpart",
            "provenance",
        }
        optional = {"execution_contract", "split_provenance", "baseline_authorization"}
        if human:
            required.add("evidence_adjudication")
        _require(required <= set(pair) <= required | optional, "Pair fields do not match its schema.")
        _require(_uuid(pair.get("pair_id")) and _uuid(pair.get("session_id")), "Pair identity is invalid.")
        _require(
            pair.get("campaign_id") is None or _uuid(pair["campaign_id"]),
            "Pair campaign identity is invalid.",
        )
        group = pair["session_id"]
        if pair.get("split_provenance") is not None:
            group = validate_split_group(pair["split_provenance"], pair["session_id"])["id"]
        _require(
            pair.get("split_group") == group and pair.get("split") == split_for_session(group),
            "Pair split does not match its stable collection session group.",
        )
        _require(_integer(pair.get("revision"), 1), "Pair revision is invalid.")
        _require(
            pair.get("recipe") == snapshot["recipe"]
            and pair.get("objects") == {"a": snapshot["object_a"], "b": snapshot["object_b"]},
            "Pair roles differ from its frozen recipe.",
        )
        provenance = pair.get("provenance")
        _require(
            isinstance(provenance, dict)
            and provenance.get("evidence_kind") == kind
            and provenance.get("human_reviewed") is human
            and provenance.get("legacy_recipe_snapshot_missing") is False
            and pair.get("mode") == ("replay" if kind == "fixture_replay" else "live"),
            "Pair provenance is inconsistent with the release or immutable machine evidence.",
        )
        if human:
            _require(
                provenance.get("label_origin") == "human_adjudication",
                "Human-derived labels must declare their separate origin.",
            )
        execution = pair.get("execution_contract")
        if execution is not None:
            execution = validate_execution_contract(execution, mode=pair["mode"])
        baseline = None
        for slot in ("baseline", "counterpart"):
            capture = pair.get(slot)
            _require(
                isinstance(capture, dict)
                and _uuid(capture.get("id"))
                and capture.get("slot") in ({"baseline", "counterpart"} if human else {slot})
                and capture.get("status")
                in ({"accepted", "rejected", "superseded"} if human else {"accepted"})
                and capture.get("interrupted") is False
                and capture.get("error") is None
                and _integer(capture.get("revision"), 1)
                and capture["revision"] <= pair["revision"],
                "Pair must contain two complete uninterrupted observations with valid revisions.",
            )
            _require(
                set(capture) - {"baseline_authorization", "human_review_required", "recovery_unresolved"}
                == {
                    "id",
                    "slot",
                    "status",
                    "revision",
                    "epoch",
                    "client_seq",
                    "created_at",
                    "completed_at",
                    "reference_capture_id",
                    "retry_of_capture_id",
                    "frames",
                    "result",
                    "error",
                    "interrupted",
                }
                and _integer(capture["epoch"])
                and _integer(capture["client_seq"])
                and _date(capture["created_at"])
                and _date(capture["completed_at"])
                and (capture["retry_of_capture_id"] is None or _uuid(capture["retry_of_capture_id"])),
                "Capture fields or recorded metadata do not match its schema.",
            )
            if capture["slot"] == "baseline":
                _require(
                    capture.get("reference_capture_id") is None,
                    "Baseline unexpectedly references another capture.",
                )
            else:
                _require(
                    capture.get("reference_capture_id") == pair["baseline"]["id"],
                    "Original counterpart refers to a different reference.",
                )
            if slot == "counterpart":
                _require(
                    pair["baseline"]["revision"] < capture["revision"]
                    and (human or capture["revision"] == pair["revision"]),
                    "Counterpart does not match the ordered pair revision.",
                )
            _require(
                capture.get("human_review_required", False)
                is (capture.get("baseline_authorization") is not None),
                "Capture human-review requirement differs from its saved authorization.",
            )
            _require(
                "recovery_unresolved" not in capture or capture["recovery_unresolved"] is True,
                "Capture recovery provenance must retain an explicit unresolved flag.",
            )
            if not human:
                _require(
                    capture.get("baseline_authorization") is None and not capture.get("recovery_unresolved"),
                    "Human-authorized evidence cannot impersonate an ordinary machine pair.",
                )
            frames = capture.get("frames")
            _require(isinstance(frames, list) and 1 <= len(frames) <= 48, "Capture frame count is invalid.")
            for frame in frames:
                self._validate_frame(frame, kind)
                if execution is not None:
                    assert_observation_matches(frame, execution, pair["mode"])
            result = evaluate(
                capture,
                snapshot,
                baseline if capture["slot"] == "counterpart" else None,
                media_reader=lambda asset: self._read(f"assets/{asset}", self.limits.max_asset_bytes),
            )
            _require(
                (human or result["accepted"] is True)
                and _canonical(result) == _canonical(capture.get("result")),
                "Recorded decision or answer differs from deterministic verification of its observations.",
            )
            if slot == "baseline":
                baseline = {**result, "frames": frames}
        if human:
            validate_adjudication(pair, replay_decisions=False)

    def _validate_frame(self, frame: object, kind: str) -> None:
        _require(isinstance(frame, dict), "Capture frame must be an object.")
        asset_id = frame.get("asset_id")
        _require(
            isinstance(asset_id, str) and _ASSET.fullmatch(f"assets/{asset_id}") is not None,
            "Frame asset hash is invalid.",
        )
        _require(
            _integer(frame.get("bytes"), 1)
            and frame["bytes"] <= self.limits.max_asset_bytes
            and _integer(frame.get("width"), 80)
            and _integer(frame.get("height"), 80)
            and max(frame["width"], frame["height"]) <= 2560
            and frame["width"] * frame["height"] <= 4_000_000
            and frame.get("media_type") in {"image/jpeg", "image/png"},
            "Frame original byte count, dimensions, or media type is invalid.",
        )
        t_ms = frame.get("t_ms")
        _require(
            type(t_ms) in (int, float) and math.isfinite(t_ms) and t_ms >= 0, "Frame timestamp is invalid."
        )
        model = frame.get("model")
        _require(
            isinstance(model, dict)
            and model.get("provider") == ("fixture" if kind == "fixture_replay" else "roboflow")
            and isinstance(model.get("model_id"), str)
            and bool(model["model_id"]),
            "Frame perception provenance does not match its declared evidence kind.",
        )

    def _validate_rows(self) -> None:
        assert self._archive is not None
        count = 0
        with self._archive.open(self._entries["examples.jsonl"]) as stream:
            while raw := stream.readline(self.limits.max_json_line_bytes + 1):
                _require(
                    len(raw) <= self.limits.max_json_line_bytes, "JSONL row exceeds its admission limit."
                )
                _require(count < len(self._rows), "JSONL contains extra rows.")
                row = _json(raw)
                _require(
                    _canonical(row) == _canonical(self._rows[count]),
                    "JSONL row differs from the pair evidence, review, release, or split.",
                )
                count += 1
        _require(count == len(self._rows), "JSONL is missing expected pair members.")

    @property
    def manifest(self) -> dict:
        """A detached copy: callers cannot alter the validator's trusted state."""
        return deepcopy(self._manifest)

    @property
    def summary(self) -> dict:
        return {
            "schema_version": self._manifest["schema_version"],
            "id": self._manifest.get("id", self._manifest.get("pair_id")),
            "content_sha256": self._manifest.get("content_sha256"),
            "evidence_kind": self.evidence_kind,
            "human_reviewed": self._release
            or any(row.get("label_origin") == "human_adjudication" for row in self._rows),
            "release_reviewed": self._release,
            "human_adjudicated_pairs": sum(
                row.get("label_origin") == "human_adjudication" for row in self._rows
            )
            // 2,
            "pair_count": len(self._rows) // 2,
            "example_count": len(self._rows),
            "asset_count": len(self._assets),
            "asset_bytes": sum(asset["bytes"] for asset in self._assets.values()),
            "split_counts": dict(self._split_counts),
        }

    def iter_examples(self, *, split: str | None = None) -> Iterator[dict]:
        """Yield one VQA example per pair member, retaining group and provenance."""
        _require(not self._closed, "Archive is closed.")
        _require(split is None or split in _SPLITS, "Choose train, valid, test, or no split filter.")
        for row in self._rows:
            if split is None or row["split"] == split:
                yield deepcopy(row)

    def read_media(self, path: str) -> bytes:
        """Read one original image by its validated JSONL media path."""
        _require(
            not self._closed and isinstance(path, str) and path in self._assets,
            "Media is not in this open, validated release.",
        )
        try:
            raw = self._read(path, self.limits.max_asset_bytes)
            expected = self._assets[path]
            _require(
                len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["asset_id"],
                "Media bytes changed after release validation.",
            )
            return raw
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, EOFError):
            raise ArchiveValidationError("Validated media is no longer readable or intact.") from None

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()
        if self._owned:
            self._source.close()
        self._closed = True

    def __enter__(self) -> PortableRelease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
