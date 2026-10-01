#!/usr/bin/env python3
"""Recheck saved physical captures and labeled altered-original controls, offline.

Reads production PostgreSQL in a READ ONLY transaction and original media bytes.
It never invokes a detector, alters a session, or copies session capabilities.
The selected examples are repair/regression evidence, not a held-out benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import psycopg
from PIL import Image
from psycopg.rows import dict_row

from counterpart.recipes import build_recipe_snapshot
from counterpart.storage import MediaStore
from counterpart.verification import verify

VALID_PAIRS = ("b86342de", "132b7aa3", "be27aa3b", "df6c36ec", "f109c54f", "90b0720b", "9aa5f7a4")
INVALID_PAIRS = ("1fa24eac", "c538159a", "d347fe62")
VALID_BASELINES = ("18a1509c", "af10a576")
INVALID_BASELINES = ("499ec652", "32486e3a")
PREFIXES = VALID_PAIRS + INVALID_PAIRS + VALID_BASELINES + INVALID_BASELINES


def summarize(result):
    return {
        "accepted": result["accepted"],
        "code": result["code"],
        "checks": result["checks"],
    }


def evaluate(row, reference, version, reader, *, original=False, frames=None, slot=None):
    snapshot = (
        row["recipe_snapshot"]
        if original
        else build_recipe_snapshot(
            row["recipe"],
            row["object_a"],
            row["object_b"],
            recipe_version=version,
            protected_objects=row["recipe_snapshot"].get("protected_objects", []),
        )
    )
    baseline = None
    if reference is not None:
        prior = reference["result"] if original else evaluate(reference, None, version, reader)
        baseline = {**prior, "frames": reference["observed_frames"]}
    return verify(
        row["recipe"],
        slot or row["slot"],
        frames or row["observed_frames"],
        baseline,
        object_a=row["object_a"],
        object_b=row["object_b"],
        interrupted=row["interrupted"],
        recipe_snapshot=snapshot,
        media_reader=reader,
        baseline_authorization=row.get("baseline_authorization") if original else None,
    )


def altered_controls(frame, reader):
    """Synthetic negative controls derived from actual bytes, never physical labels."""
    raw = np.array(Image.open(BytesIO(reader(frame["asset_id"]))).convert("RGB"))
    height, width = raw.shape[:2]
    detections = {d["label"]: d for d in frame["detections"] if d["label"] in ("bottle", "cup")}
    boxes = {
        label: tuple(np.rint(np.array(d["box"]) * [width, height, width, height]).astype(int))
        for label, d in detections.items()
    }
    x1, y1, x2, y2 = boxes["bottle"]
    patch = raw[y1:y2, x1:x2]
    ph, pw = patch.shape[:2]
    names = (
        "green",
        "blue",
        "gray",
        "red",
        "shuffle",
        "gray_inner",
        "invert",
        "cap_red",
        "cap_blue",
        "cup",
        "background_replaced",
        "background_obstructed",
        "camera_shift",
    )
    colors = {"green": (64, 128, 64), "blue": (30, 30, 230), "gray": (128, 128, 128), "red": (230, 30, 30)}
    for name in names:
        rgb = raw.copy()
        changed = deepcopy(frame)
        if name in colors:
            rgb[y1:y2, x1:x2] = colors[name]
        elif name == "shuffle":
            rgb[y1:y2, x1:x2] = (
                np.random.default_rng(123).permutation(patch.reshape(-1, 3)).reshape(patch.shape)
            )
        elif name == "gray_inner":
            rgb[y1 + ph // 10 : y2 - ph // 10, x1 + pw // 10 : x2 - pw // 10] = patch.mean(
                axis=(0, 1)
            ).astype("uint8")
        elif name == "invert":
            rgb[y1:y2, x1:x2] = 255 - patch
        elif name.startswith("cap_"):
            rgb[y1 : y1 + ph // 6, x1 + pw // 5 : x2 - pw // 5] = (
                (230, 30, 30) if name == "cap_red" else (30, 30, 230)
            )
        elif name == "cup":
            a, b, c, d = boxes["cup"]
            rgb[y1:y2, x1:x2] = cv2.resize(raw[b:d, a:c], (pw, ph))
        elif name.startswith("background_"):
            background = np.ones(raw.shape[:2], dtype=bool)
            for a, b, c, d in boxes.values():
                background[max(0, b - 8) : min(height, d + 8), max(0, a - 8) : min(width, c + 8)] = False
            if name == "background_replaced":
                rgb[background] = (80, 165, 90)
            else:
                background[:, : width // 2] = False
                rgb[background] = (160, 40, 40)
        elif name == "camera_shift":
            dx = round(width * 0.08)
            rgb = cv2.warpAffine(
                raw, np.float32([[1, 0, dx], [0, 1, 0]]), (width, height), borderMode=cv2.BORDER_REFLECT
            )
            for detection in changed["detections"]:
                a, b, c, d = detection["box"]
                detection["box"] = [min(0.999, a + dx / width), b, min(1.0, c + dx / width), d]
            changed["detections"] = [d for d in changed["detections"] if d["box"][0] < d["box"][2]]
        stream = BytesIO()
        Image.fromarray(rgb).save(stream, format="PNG")
        data = stream.getvalue()
        key = hashlib.sha256(data).hexdigest()
        changed.update(asset_id=key, bytes=len(data))
        yield name, changed, data


def benign_render_controls(frame, reader):
    """Small rendering perturbations, explicitly not newly captured physical data."""
    raw = np.array(Image.open(BytesIO(reader(frame["asset_id"]))).convert("RGB"))
    height, width = raw.shape[:2]
    for name in ("exposure_plus5", "exposure_minus5", "jpeg_quality75", "sensor_noise2", "gamma_1p03"):
        rgb = raw.copy()
        changed = deepcopy(frame)
        if name.startswith("exposure_"):
            rgb = np.clip(raw.astype(float) + (5 if name.endswith("plus5") else -5), 0, 255).astype("uint8")
        elif name == "sensor_noise2":
            noise = np.random.default_rng(127).normal(0, 2, raw.shape)
            rgb = np.clip(raw.astype(float) + noise, 0, 255).astype("uint8")
        elif name == "gamma_1p03":
            rgb = np.rint(255 * (raw / 255) ** 1.03).astype("uint8")
        stream = BytesIO()
        if name == "jpeg_quality75":
            Image.fromarray(rgb).save(stream, format="JPEG", quality=75)
        else:
            Image.fromarray(rgb).save(stream, format="PNG")
        data = stream.getvalue()
        key = hashlib.sha256(data).hexdigest()
        changed.update(asset_id=key, bytes=len(data), width=width, height=height)
        yield name, changed, data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default="postgresql:///counterpart_dev")
    parser.add_argument("--media-dir", type=Path, default=Path("var/media"))
    parser.add_argument("--version", default="2.3.0")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/qa/background-repair/saved-evidence.json")
    )
    args = parser.parse_args()
    cv2.setNumThreads(1)
    store = MediaStore(args.media_dir)
    query = (
        "SELECT c.id,c.reference_capture_id,c.slot,c.interrupted,c.observed_frames,c.result,"
        "c.baseline_authorization,s.recipe,s.object_a,s.object_b,s.recipe_snapshot "
        "FROM counterpart_captures c JOIN counterpart_sessions s ON s.id=c.session_id "
    )
    with psycopg.connect(args.dsn, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        rows = connection.execute(query + "WHERE left(c.id::text,8)=ANY(%s)", (list(PREFIXES),)).fetchall()
        refs = [row["reference_capture_id"] for row in rows if row["reference_capture_id"]]
        references = connection.execute(query + "WHERE c.id=ANY(%s::uuid[])", (refs,)).fetchall()
    by_id = {row["id"]: row for row in rows + references}
    by_prefix = {str(row["id"])[:8]: row for row in rows}
    if set(by_prefix) != set(PREFIXES):
        raise SystemExit("Selected evidence is missing; refusing an incomplete report.")
    report = {
        "schema": "counterpart.background-repair-probe/1",
        "executed_at": datetime.now(UTC).isoformat(),
        "target_recipe_version": args.version,
        "verifier_source_sha256": {
            name: hashlib.sha256(Path("src/counterpart", name).read_bytes()).hexdigest()
            for name in (
                "verification.py",
                "recipes.py",
                "visual_checks.py",
                "background.py",
                "appearance.py",
                "appearance_overlap.py",
            )
            if Path("src/counterpart", name).exists()
        },
        "provider_calls": 0,
        "production_writes": 0,
        "limitations": [
            "Known saved repair cases, not held-out quality or accuracy evidence.",
            "Oct1 sources are two baseline captures, not an independently captured changed-side pair.",
            "Manipulated controls retain recorded detections except an explicitly transformed "
            "camera-shift box set; no detector was rerun.",
            "Near-duplicate recovery confirms these two original views, not arbitrary background invariance.",
        ],
        "physical_captures": [],
        "manipulated_controls": [],
        "render_robustness_controls": [],
    }
    for prefix in PREFIXES:
        row = by_prefix[prefix]
        reference = by_id.get(row["reference_capture_id"])
        original = evaluate(row, reference, args.version, store.read, original=True)
        current = evaluate(row, reference, args.version, store.read)
        expected = prefix in VALID_PAIRS + VALID_BASELINES
        report["physical_captures"].append(
            {
                "capture_id": str(row["id"]),
                "slot": row["slot"],
                "source_recipe_version": row["recipe_snapshot"]["recipe_version"],
                "asset_sha256": [f["asset_id"] for f in row["observed_frames"]],
                "reference_capture_id": str(row["reference_capture_id"]) if reference else None,
                "original_exactly_reproduced": original == row["result"],
                "expected_accepted": expected,
                "matches_expected": current["accepted"] == expected,
                "result": summarize(current),
            }
        )
    row = by_prefix["b86342de"]
    reference = by_id[row["reference_capture_id"]]
    for name, frame, data in altered_controls(row["observed_frames"][0], store.read):

        def read(asset, altered_frame=frame, altered_data=data):
            return altered_data if asset == altered_frame["asset_id"] else store.read(asset)

        current = evaluate(row, reference, args.version, read, frames=[frame])
        report["manipulated_controls"].append(
            {
                "kind": "manipulated_original_negative_control",
                "alteration": name,
                "source_capture_id": str(row["id"]),
                "altered_sha256": frame["asset_id"],
                "expected_accepted": False,
                "matches_expected": not current["accepted"],
                "result": summarize(current),
            }
        )
    for name, frame, data in benign_render_controls(row["observed_frames"][0], store.read):

        def read(asset, altered_frame=frame, altered_data=data):
            return altered_data if asset == altered_frame["asset_id"] else store.read(asset)

        current = evaluate(row, reference, args.version, read, frames=[frame])
        report["render_robustness_controls"].append(
            {
                "kind": "manipulated_original_render_robustness_control",
                "alteration": name,
                "source_capture_id": str(row["id"]),
                "altered_sha256": frame["asset_id"],
                "expected_accepted": True,
                "matches_expected": current["accepted"],
                "result": summarize(current),
            }
        )
    first, second = by_prefix["af10a576"], by_prefix["18a1509c"]
    recovery = evaluate(second, first, args.version, store.read, slot="recovery")
    report["same_scene_recovery"] = {
        "source_kind": "two_saved_physical_baselines_reused_only_for_recovery_probe",
        "reference_capture_id": str(first["id"]),
        "candidate_capture_id": str(second["id"]),
        "expected_accepted": True,
        "matches_expected": recovery["accepted"],
        "result": summarize(recovery),
    }
    report["summary"] = {
        "physical_expected": sum(r["matches_expected"] for r in report["physical_captures"]),
        "physical_total": len(report["physical_captures"]),
        "historical_exact": sum(r["original_exactly_reproduced"] for r in report["physical_captures"]),
        "altered_controls_blocked": sum(r["matches_expected"] for r in report["manipulated_controls"]),
        "altered_controls_total": len(report["manipulated_controls"]),
        "render_controls_accepted": sum(r["matches_expected"] for r in report["render_robustness_controls"]),
        "render_controls_total": len(report["render_robustness_controls"]),
        "recovery_accepted": recovery["accepted"],
    }
    report["summary"]["passes_expectations"] = (
        all(r["matches_expected"] and r["original_exactly_reproduced"] for r in report["physical_captures"])
        and all(r["matches_expected"] for r in report["manipulated_controls"])
        and all(r["matches_expected"] for r in report["render_robustness_controls"])
        and recovery["accepted"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], sort_keys=True))
    if not report["summary"]["passes_expectations"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
