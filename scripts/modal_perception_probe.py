#!/usr/bin/env python3
"""Frozen, single-use Modal GPU diagnostic; never imports a model locally.

Use the separate existing Modal CLI environment. `prepare` freezes source and
selection; `run` reserves once before building; `collect` only retrieves the
recorded call. No provider key is needed: RF-DETR's official public weights are
loaded on the remote GPU. Images and weights never return to the laptop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "research/modal_perception_probe.plan.json"
STATE = ROOT / "research/modal_perception_probe.state.json"
REPORT = ROOT / "research/modal_perception_probe.json"
SNAPSHOT = Path.home() / ".cache/counterpart/modal-perception-v1"
RUN_ID = "counterpart-whatsup-coco58-v1"


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".modal-probe-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(annotation_path: Path) -> None:
    if PLAN.exists() or STATE.exists():
        raise SystemExit("Frozen plan already exists; do not create another reservation.")
    raw = annotation_path.read_bytes()
    annotations = json.loads(raw)
    mapping = {
        "mug": "cup",
        "cup": "cup",
        "phone": "cell phone",
        "book": "book",
        "bowl": "bowl",
        "knife": "knife",
        "fork": "fork",
        "spoon": "spoon",
        "scissors": "scissors",
        "remote": "remote",
    }
    selected = []
    for row in annotations:
        name = Path(row["image_path"]).name
        parsed = re.fullmatch(r"(.+)_(left|right)_of_(.+)\.jpeg", name)
        if not parsed:
            continue
        a, direction, b = parsed.groups()
        if a not in mapping or b not in mapping or mapping[a] == mapping[b]:
            continue
        correct_caption = f"A {a} to the {direction} of a {b}"
        if row["caption_options"][0] != correct_caption:
            raise SystemExit("Filename and independent first-caption source label disagree.")
        selected.append(
            {
                "filename": name,
                "object_a": mapping[a],
                "object_b": mapping[b],
                "expected_relation": direction,
                "source_caption": correct_caption,
                "source_member": "controlled_clevr/" + name,
            }
        )
    selected.sort(key=lambda x: x["filename"])
    if len(selected) != 58 or len({x["filename"] for x in selected}) != 58:
        raise SystemExit("Expected all 58 predeclared COCO-compatible left/right images.")
    prior = json.loads((ROOT / "research/real_photo_probe.json").read_text())
    prior_hashes = {x["path"]: x["sha256"] for x in prior["source"]["files"]}
    target = SNAPSHOT / "counterpart"
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").write_text("")
    source_hashes = {}
    for name in ("verification.py", "visual_checks.py", "recipes.py"):
        data = (ROOT / "src/counterpart" / name).read_bytes()
        (target / name).write_bytes(data)
        source_hashes[name] = hashlib.sha256(data).hexdigest()
    plan = {
        "schema_version": "counterpart.modal-photo-probe/1",
        "run_id": RUN_ID,
        "frozen_at": datetime.now(UTC).isoformat(),
        "selected": selected,
        "source": {
            "repository": "https://github.com/amitakamath/whatsup_vlms",
            "commit": "7c1f2550eace32e7b8c77de5a792347c402960d1",
            "annotations_sha256": hashlib.sha256(raw).hexdigest(),
            "archive_google_drive_id": "13jdBpg8t3NqW3jrL6FK8HO93vwsUjDxG",
            "source_rows": len(annotations),
            "horizontal_rows": 204,
            "selected_rows": 58,
            "prior_six_hashes": prior_hashes,
            "ontology_mapping": mapping,
        },
        "selection_rule": (
            "All source left/right rows whose names map to distinct COCO classes; no prediction selection."
        ),
        "model": {
            "package": "rfdetr==1.10.1",
            "class": "RFDETRSmall",
            "threshold": 0.25,
            "device": "cuda",
            "precision": "float32",
            "optimization": "none",
            "identity_note": (
                "Official RF-DETR Small checkpoint; not claimed byte-identical to hosted coco/39."
            ),
        },
        "source_hashes": source_hashes,
        "timing": {
            "warmup_images": 6,
            "repetitions_per_image": 20,
            "images": sorted(prior_hashes),
            "cuda_synchronize": True,
        },
        "budget": {
            "user_confirmed_remaining_usd": 3.39,
            "reservation_usd": 0.80,
            "cash_budget_usd": 0,
            "billing_before_metered_usd": 26.61,
            "billing_before_billed_usd": 0,
            "spend_limit": "Previously set to $0 by user; current value unavailable in Starter API.",
            "gpu": "L4",
            "cpu_cores_max": 2,
            "memory_gib_max": 8,
            "timeout_seconds": 1200,
            "startup_timeout_seconds": 120,
            "max_containers": 1,
            "retries": 0,
            "persistent_volumes": [],
            "gpu_hour_usd": 0.8,
            "cpu_core_hour_usd": 0.0473,
            "memory_gib_hour_usd": 0.008,
            "runtime_max_usd": (1200 + 120 + 2) / 3600 * (0.8 + 2 * 0.0473 + 8 * 0.008),
            "build_and_incident_headroom_usd": 0.80 - (1322 / 3600 * (0.8 + 2 * 0.0473 + 8 * 0.008)),
            "pricing_source": "https://modal.com/pricing",
        },
        "limits": [
            "Public benchmark photographs, not a physical Counterpart capture trial.",
            "52 photos are new to this diagnostic; six were previously used with hosted inference.",
            "Pretraining overlap unknown; small single-domain sample cannot establish general accuracy.",
            "Source relations are ground truth only for left/right, not scene-control validity.",
            "Visual-check outcomes are descriptive; no invented independent control annotations.",
            "No training, threshold tuning, persistent volumes, deployed service, or provider keys.",
        ],
    }
    save(PLAN, plan)
    print(json.dumps({"frozen_photos": 58, "frozen_pairs": 29, "reservation_usd": 0.80}))


def cloud_probe(plan: dict) -> dict:
    # All heavy imports and model/image operations run only in the Modal container.
    import copy
    import io
    import math
    import statistics
    import tarfile
    import time
    import urllib.request
    from collections import Counter
    from importlib.metadata import version

    import torch
    from PIL import Image
    from rfdetr import RFDETRSmall

    from counterpart.recipes import build_recipe_snapshot
    from counterpart.verification import verify
    from counterpart.visual_checks import evaluate_visual_controls

    if plan["run_id"] != "counterpart-whatsup-coco58-v1" or len(plan["selected"]) != 58:
        raise ValueError("Frozen experiment identity mismatch")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required; never fall back to CPU")
    torch.set_num_threads(2)
    torch.manual_seed(20260927)
    for name, digest in plan["source_hashes"].items():
        if hashlib.sha256((Path("/opt/counterpart") / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Source snapshot hash mismatch")
    report = {
        "run_id": plan["run_id"],
        "status": "running",
        "plan": copy.deepcopy({key: value for key, value in plan.items() if not key.startswith("_")}),
        "frozen_plan_sha256": plan.get("_frozen_plan_sha256"),
        "runner_sha256": plan.get("_runner_sha256"),
        "failures": [],
        "gpu": torch.cuda.get_device_name(),
        "photos": [],
        "pairs": [],
        "packages": {
            p: version(p)
            for p in ("rfdetr", "torch", "torchvision", "numpy", "opencv-python-headless", "pydantic")
        },
    }
    started = time.monotonic()
    url = "https://drive.usercontent.google.com/download?id=" + plan["source"]["archive_google_drive_id"]
    url += "&export=download&confirm=t"
    with urllib.request.urlopen(url, timeout=60) as response:
        archive_bytes = response.read(80_000_001)
    if len(archive_bytes) > 80_000_000:
        raise ValueError("Source archive exceeds frozen size bound")
    report["archive_sha256"] = hashlib.sha256(archive_bytes).hexdigest()
    report["archive_bytes"] = len(archive_bytes)
    media, images = {}, {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        for row in plan["selected"]:
            info = archive.getmember(row["source_member"])
            if not info.isfile() or info.size > 8_000_000:
                raise ValueError("Selected image exceeds bound")
            member = archive.extractfile(info)
            if member is None:
                raise ValueError("Selected original is not a regular archive member")
            raw = member.read(8_000_001)
            digest = hashlib.sha256(raw).hexdigest()
            if row["filename"] in plan["source"]["prior_six_hashes"]:
                if digest != plan["source"]["prior_six_hashes"][row["filename"]]:
                    raise ValueError("Original six-photo source changed")
            photo = Image.open(io.BytesIO(raw))
            if photo.width * photo.height > 4_000_000:
                raise ValueError("Original dimensions exceed verifier contract")
            images[row["filename"]] = photo.convert("RGB")
            media[digest] = raw
            row["asset_id"] = digest
            row["bytes"] = len(raw)
    report["source_loading_seconds"] = time.monotonic() - started
    model_started = time.monotonic()
    model = RFDETRSmall(device="cuda")
    weights = Path(model.model_config.pretrain_weights)
    report["weights"] = {
        "filename": weights.name,
        "bytes": weights.stat().st_size,
        "sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
    }
    report["model_loading_seconds"] = time.monotonic() - model_started
    frames = {}
    model_call_attempts = 0
    for row in plan["selected"]:
        image = images[row["filename"]]
        photo = {**row, "status": "dispatching"}
        report["photos"].append(photo)
        try:
            torch.cuda.synchronize()
            begin = time.perf_counter()
            model_call_attempts += 1
            detections = model.predict(image, threshold=plan["model"]["threshold"])
            torch.cuda.synchronize()
            elapsed_ms = (time.perf_counter() - begin) * 1000
            normalized = []
            for box, score, label in zip(
                detections.xyxy, detections.confidence, detections.data["class_name"], strict=True
            ):
                x1, y1, x2, y2 = [float(v) for v in box]
                normalized.append(
                    {
                        "label": str(label),
                        "confidence": float(score),
                        "box": [
                            max(0, x1 / image.width),
                            max(0, y1 / image.height),
                            min(1, x2 / image.width),
                            min(1, y2 / image.height),
                        ],
                    }
                )
            frame = {
                "asset_id": row["asset_id"],
                "bytes": row["bytes"],
                "t_ms": 0,
                "width": image.width,
                "height": image.height,
                "detections": normalized,
            }
            decision = verify(
                "spatial", "baseline", [frame], object_a=row["object_a"], object_b=row["object_b"]
            )
            relation = decision["evidence"].get("relation")
            photo.update(
                status="observed",
                inference_ms=elapsed_ms,
                observation=frame,
                diagnostic=decision,
                observed_relation=relation,
                relation_correct=relation == row["expected_relation"],
                abstained=relation not in ("left", "right"),
            )
            frames[row["filename"]] = frame
        except Exception as error:
            photo.update(
                status="failed", error_type=type(error).__name__, relation_correct=False, abstained=True
            )
    records = {row["filename"]: row for row in report["photos"]}
    product_roles = {"bottle", "cup", "book", "cell phone", "mouse"}
    for row in plan["selected"]:
        if row["expected_relation"] != "left":
            continue
        other_name = row["filename"].replace("_left_of_", "_right_of_")
        left, right = records[row["filename"]], records[other_name]
        pair = {
            "left": row["filename"],
            "right": other_name,
            "both_source_relations_correct": left["relation_correct"] and right["relation_correct"],
        }
        try:
            if left["status"] == right["status"] == "observed":
                baseline = {**left["diagnostic"], "frames": [left["observation"]]}
                pair["geometry_decision"] = verify(
                    "spatial",
                    "counterpart",
                    [right["observation"]],
                    baseline,
                    object_a=row["object_a"],
                    object_b=row["object_b"],
                )
                pair["visual_controls"] = evaluate_visual_controls(
                    [right["observation"]],
                    [left["observation"]],
                    object_a=row["object_a"],
                    object_b=row["object_b"],
                    media_reader=media.__getitem__,
                )
                if {row["object_a"], row["object_b"]} <= product_roles:
                    snapshot = build_recipe_snapshot(
                        "spatial", row["object_a"], row["object_b"], recipe_version="2.0.0"
                    )
                    initial = verify(
                        "spatial",
                        "baseline",
                        [left["observation"]],
                        object_a=row["object_a"],
                        object_b=row["object_b"],
                        recipe_snapshot=snapshot,
                        media_reader=media.__getitem__,
                    )
                    pair["product_v2_baseline"] = initial
                    pair["product_v2_counterpart"] = verify(
                        "spatial",
                        "counterpart",
                        [right["observation"]],
                        {**initial, "frames": [left["observation"]]},
                        object_a=row["object_a"],
                        object_b=row["object_b"],
                        recipe_snapshot=snapshot,
                        media_reader=media.__getitem__,
                    )
        except Exception as error:
            pair["diagnostic_failure"] = type(error).__name__
            report["failures"].append(
                {"stage": "pair_diagnostic", "left": row["filename"], "error_type": type(error).__name__}
            )
        report["pairs"].append(pair)
    timing_images = plan["timing"]["images"]
    for name in timing_images:
        try:
            model_call_attempts += 1
            model.predict(images[name], threshold=0.25)
        except Exception as error:
            report["failures"].append(
                {"stage": "warmup", "filename": name, "error_type": type(error).__name__}
            )
    # Each timed attempt synchronizes inside its exception boundary.
    timings = []
    for repetition in range(plan["timing"]["repetitions_per_image"]):
        for name in timing_images:
            try:
                torch.cuda.synchronize()
                begin = time.perf_counter()
                model_call_attempts += 1
                model.predict(images[name], threshold=0.25)
                torch.cuda.synchronize()
                timings.append(
                    {
                        "filename": name,
                        "repetition": repetition,
                        "milliseconds": (time.perf_counter() - begin) * 1000,
                    }
                )
            except Exception as error:
                report["failures"].append(
                    {
                        "stage": "timing",
                        "filename": name,
                        "repetition": repetition,
                        "error_type": type(error).__name__,
                    }
                )
    values = sorted(x["milliseconds"] for x in timings)
    report["warm_timing"] = {
        "samples": timings,
        "count": len(values),
        "p50_ms": statistics.median(values) if values else None,
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1] if values else None,
        "max_ms": max(values) if values else None,
        "scope": (
            "Local-in-cloud PIL input through synchronized prediction; "
            "excludes network, source download, cold start and queue."
        ),
    }
    report["counts"] = {
        "source_images": len(records),
        "observed": sum(r["status"] == "observed" for r in records.values()),
        "correct_source_relation": sum(r["relation_correct"] for r in records.values()),
        "abstentions": sum(r["abstained"] for r in records.values()),
        "wrong_answer": sum(not r["relation_correct"] and not r["abstained"] for r in records.values()),
        "pairs": len(report["pairs"]),
        "both_relations_correct": sum(p["both_source_relations_correct"] for p in report["pairs"]),
        "visual_outcomes": dict(
            Counter(p.get("visual_controls", {}).get("outcome", "unavailable") for p in report["pairs"])
        ),
        "total_model_calls": model_call_attempts,
    }
    report.update(
        status="complete_with_failures" if report["failures"] else "complete",
        completed_at=datetime.now(UTC).isoformat(),
        container_elapsed_seconds=time.monotonic() - started,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "collect", "repair-archive"))
    parser.add_argument(
        "--annotations", type=Path, default=Path("/tmp/counterpart-controlled-b-annotations.json")
    )
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.annotations)
        return
    import modal

    if args.action == "collect":
        state = json.loads(STATE.read_text())
        result = modal.FunctionCall.from_id(state["call_id"]).get(timeout=15)
        save(REPORT, result)
        save(STATE, {**state, "status": "complete", "collected_at": datetime.now(UTC).isoformat()})
        print(json.dumps(result["counts"]))
        return
    plan = json.loads(PLAN.read_text())
    prior_attempt = None
    if args.action == "repair-archive":
        prior_attempt = json.loads(STATE.read_text())
        if prior_attempt.get("error_type") != "BadZipFile" or prior_attempt.get("attempts"):
            raise SystemExit("Archive repair is limited to the one known, pre-inference failed call.")
        # Confirm the saved remote call really failed; never duplicate an unknown/running outcome.
        try:
            modal.FunctionCall.from_id(prior_attempt["call_id"]).get(timeout=5)
        except Exception as error:
            if type(error).__name__ != "BadZipFile":
                raise SystemExit("Saved remote failure differs; do not retry.") from None
        else:
            raise SystemExit("Saved remote call succeeded; collect it instead.")
    else:
        if STATE.exists():
            raise SystemExit("Existing reservation: collect its call; never resubmit automatically.")
        fd = os.open(STATE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    state = {
        "run_id": RUN_ID,
        "status": "reserved_before_build",
        "reserved_usd": 0.80,
        "created_at": datetime.now(UTC).isoformat(),
        "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
    }
    if prior_attempt:
        state["attempts"] = [prior_attempt]
        state["repair"] = "Official source has gzip TAR magic; fix reader before any model predictions."
        state["second_attempt_timeout_seconds"] = 900
        state["both_attempts_resource_envelope_usd"] = (1322 + 1022) / 3600 * (0.8 + 2 * 0.0473 + 8 * 0.008)
    state["runner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    save(STATE, state)
    plan["_frozen_plan_sha256"] = state["plan_sha256"]
    plan["_runner_sha256"] = state["runner_sha256"]
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("libgl1", "libglib2.0-0")
        .pip_install(
            "rfdetr==1.10.1",
            "torch==2.14.0",
            "torchvision==0.29.0",
            "opencv-python-headless==4.13.0.92",
            "numpy==2.3.5",
            "pydantic==2.13.5",
        )
        .env(
            {
                "PYTHONPATH": "/opt",
                "OMP_NUM_THREADS": "2",
                "MKL_NUM_THREADS": "2",
                "RF_HOME": "/tmp/counterpart-models",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            }
        )
        .add_local_dir(str(SNAPSHOT / "counterpart"), "/opt/counterpart")
    )
    app = modal.App("counterpart-perception-probe")
    remote = app.function(
        image=image,
        gpu="L4",
        cpu=(1, 2),
        memory=(4096, 8192),
        timeout=900 if prior_attempt else 1200,
        startup_timeout=120,
        max_containers=1,
        min_containers=0,
        scaledown_window=2,
        retries=0,
        serialized=True,
        include_source=False,
    )(cloud_probe)
    try:
        with modal.enable_output(), app.run(detach=True):
            state.update(status="app_created", app_id=app.app_id, image_id=image.object_id)
            save(STATE, state)
            call = remote.spawn(plan)
            state.update(
                status="submitted", call_id=call.object_id, submitted_at=datetime.now(UTC).isoformat()
            )
            save(STATE, state)
            print(json.dumps({"app_id": app.app_id, "call_id": call.object_id}), flush=True)
            result = call.get(timeout=1320)
            save(REPORT, result)
            state.update(status="complete", completed_at=datetime.now(UTC).isoformat())
            save(STATE, state)
            print(json.dumps(result["counts"]), flush=True)
    except Exception as error:
        save(STATE, {**state, "status": "inspect_before_any_retry", "error_type": type(error).__name__})
        raise SystemExit("Probe failed; see saved state and provider logs. No automatic retry.") from None


if __name__ == "__main__":
    main()
