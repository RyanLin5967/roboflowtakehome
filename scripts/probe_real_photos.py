#!/usr/bin/env python3
"""Six preselected original tabletop photos, hosted inference only, no retries/tuning.

Explicit invocation uses the existing durable included-credit allowance. A report
is persisted before dispatch and after every result. An interrupted dispatch is
never repeated by this script; its outcome is retained as unknown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from counterpart.config import settings
from counterpart.inference_runtime import InferenceRuntime
from counterpart.providers import ProviderError, RoboflowDetector
from counterpart.repository import Repository
from counterpart.storage import MediaStore
from counterpart.verification import THRESHOLDS, VERIFIER_VERSION, verify

PAIRS = (
    ("book", "cup", "book", "cup"),
    ("mug", "book", "cup", "book"),
    ("mug", "phone", "cup", "cell phone"),
)
RUN_ID = "whatsup-controlled-b-six-v1"


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".probe-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def used(repo: Repository) -> int:
    with repo._connection() as connection:
        row = connection.execute(
            "SELECT reserved_units FROM counterpart_budget_accounts WHERE provider='roboflow'"
        ).fetchone()
    return row["reserved_units"] if row else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("/tmp/counterpart-whatsup-smoke"))
    parser.add_argument("--output", type=Path, default=Path("research/real_photo_probe.json"))
    args = parser.parse_args()
    source = json.loads((args.source_dir / "sources.json").read_text())
    expected_names = {
        f"{a}_{direction}_of_{b}.jpeg" for a, b, _, _ in PAIRS for direction in ("left", "right")
    }
    sources = {row["path"]: row for row in source["files"]}
    if set(sources) != expected_names:
        raise SystemExit("The frozen six-image selection does not match.")
    config = settings()
    if not config.live_enabled or config.inference_limit != 512 or config.roboflow_model != "coco/39":
        raise SystemExit("Expected existing live coco/39 allowance of 512; no provider call made.")
    if config.database_url != "postgresql:///counterpart_dev":
        raise SystemExit("Expected existing development ledger; refusing a fresh or alternate budget.")
    repo = Repository(config.database_url)
    repo.initialize()
    media = MediaStore(config.media_dir, config.max_asset_bytes)
    frames = {}
    for name in sorted(expected_names):
        data = (args.source_dir / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != sources[name]["sha256"]:
            raise SystemExit("Original photo hash mismatch; no provider call made.")
        frames[name] = {**media.put(data), "t_ms": 0}
    # The exclusive lock prevents a second invocation from racing the durable per-image state.
    lock = args.output.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SystemExit("Probe lock exists; inspect prior process/report before removing it.") from None
    os.close(lock_fd)
    detector = None
    try:
        report = (
            json.loads(args.output.read_text())
            if args.output.exists()
            else {
                "schema_version": "counterpart.public-photo-probe.v1",
                "run_id": RUN_ID,
                "frozen_at": datetime.now(UTC).isoformat(),
                "status": "planned",
                "purpose": "Six-photo hosted relation diagnostic and fixed-anchor negative controls.",
                "source": source,
                "maximum_new_image_calls": 6,
                "retries_per_image": 0,
                "model_id": config.roboflow_model,
                "verifier_version": VERIFIER_VERSION,
                "thresholds": dict(THRESHOLDS),
                "ledger_allowance": config.inference_limit,
                "ledger_before": used(repo),
                "photos": {},
                "expected_pair_admission": "reject because reference object also changes sides",
                "limits": [
                    "Public images; not user captures or independent human collection validation.",
                    "Six examples from one scene cannot establish general CV accuracy or latency.",
                    "Source labels describe image-plane relation, not fixed-anchor controls.",
                    "No threshold tuning, image editing, private-image upload or Modal compute.",
                ],
            }
        )
        if report.get("run_id") != RUN_ID or report.get("source") != source:
            raise SystemExit("Existing report does not match this frozen source plan.")
        if report["status"] == "complete":
            print("Existing frozen probe is complete; no calls made.")
            return
        save(args.output, report)
        detector = RoboflowDetector(
            config.credentials()["ROBOFLOW_API_KEY"].get_secret_value(), config.roboflow_model
        )
        runtime = InferenceRuntime(repo, media, config, detector)
        for original_a, original_b, object_a, object_b in PAIRS:
            for direction in ("left", "right"):
                name = f"{original_a}_{direction}_of_{original_b}.jpeg"
                if name in report["photos"]:
                    continue  # Includes unknown dispatch outcomes: never silently retry.
                job_key = RUN_ID + ":" + name
                record = {
                    "source_file": name,
                    "sha256": sources[name]["sha256"],
                    "expected_relation": direction,
                    "object_a": object_a,
                    "object_b": object_b,
                    "job_key": job_key,
                    "status": "dispatching",
                }
                report["photos"][name] = record
                report["status"] = "running"
                save(args.output, report)
                try:
                    observed = runtime.observe_frames([frames[name]], session_id=RUN_ID, job_key=job_key)[0]
                    decision = verify("spatial", "baseline", [observed], object_a=object_a, object_b=object_b)
                    relation = decision.get("evidence", {}).get("relation")
                    record.update(
                        status="observed",
                        observation=observed,
                        diagnostic=decision,
                        observed_relation=relation,
                        relation_correct=relation == direction,
                    )
                except ProviderError as error:
                    record.update(status="provider_failed", error_code=error.code)
                except Exception as error:
                    # Never save exception text: transport exceptions may carry sensitive request data.
                    record.update(status="unknown_failure", error_type=type(error).__name__)
                save(args.output, report)
        pairs = []
        for original_a, original_b, object_a, object_b in PAIRS:
            left = report["photos"][f"{original_a}_left_of_{original_b}.jpeg"]
            right = report["photos"][f"{original_a}_right_of_{original_b}.jpeg"]
            pair = {"objects": [object_a, object_b], "expected_admission": "rejected_reference_moved"}
            if left["status"] == right["status"] == "observed":
                reference = {**left["diagnostic"], "frames": [left["observation"]]}
                decision = verify(
                    "spatial",
                    "counterpart",
                    [right["observation"]],
                    reference,
                    object_a=object_a,
                    object_b=object_b,
                )
                pair.update(decision=decision, expected_control_refusal=decision["code"] == "reference_moved")
            else:
                pair["status"] = "unavailable_provider_evidence"
            pairs.append(pair)
        records = list(report["photos"].values())
        report.update(
            status="complete",
            completed_at=datetime.now(UTC).isoformat(),
            ledger_after=used(repo),
            pairs=pairs,
            counts={
                "selected_original_photos": 6,
                "observed_photos": sum(record["status"] == "observed" for record in records),
                "source_relation_correct": sum(record.get("relation_correct") is True for record in records),
                "full_pair_reference_refusals": sum(
                    pair.get("expected_control_refusal") is True for pair in pairs
                ),
            },
        )
        save(args.output, report)
        print(
            json.dumps(
                {
                    "counts": report["counts"],
                    "ledger_before": report["ledger_before"],
                    "ledger_after": report["ledger_after"],
                    "report": str(args.output),
                },
                indent=2,
            )
        )
    finally:
        if detector:
            detector.close()
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
