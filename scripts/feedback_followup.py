#!/usr/bin/env python3
"""Preserved-trace follow-up: stable FIFO control and active-preview poll cadence.

The original benchmark source/artifact remain unchanged. This runner reuses its
actual production queue/worker/read harness and documents the one extra FIFO
operation: a replacement retains the pending *queue slot's* age, not frame age.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import feedback_benchmark as benchmark


class StableFIFORepository(benchmark.ComparatorRepository):
    def _submit(self, connection, session_id, metadata, frames, fingerprint, **kwargs):
        queued_at = None
        if metadata["slot"] == "preview":
            # Match production lock order. A claimed in-flight job is not the
            # pending slot, and its age must never transfer to a later slot.
            self._session(connection, session_id, lock=True)
            prior = connection.execute(
                "SELECT j.created_at FROM counterpart_jobs j "
                "JOIN counterpart_captures c ON c.id=j.capture_id "
                "WHERE j.session_id=%s AND j.status IN ('pending','retry_wait') AND c.slot='preview'",
                (session_id,),
            ).fetchone()
            if prior:
                queued_at = prior["created_at"]
        result = super()._submit(connection, session_id, metadata, frames, fingerprint, **kwargs)
        if queued_at is not None:
            connection.execute(
                "UPDATE counterpart_jobs SET created_at=%s WHERE capture_id=%s AND status='pending'",
                (queued_at, result["capture"]["id"]),
            )
        return result


@contextmanager
def stable_comparator():
    """Explicit benchmark dependency substitution, restored before another trial."""
    original = benchmark.ComparatorRepository
    benchmark.ComparatorRepository = StableFIFORepository
    try:
        yield
    finally:
        benchmark.ComparatorRepository = original


def execute(dsn: str, case: benchmark.Case, policy: str, seed: int) -> dict:
    if policy == "stable_latest_fifo":
        with stable_comparator():
            result = benchmark.execute_trial(dsn, case, "latest_fifo", seed)
        result["policy"] = policy
        return result
    return benchmark.execute_trial(dsn, case, policy, seed)


def run(dsn: str, prior_path: Path, seed: int) -> dict:
    prior_bytes = prior_path.read_bytes()
    prior = json.loads(prior_bytes)
    base_source = Path(benchmark.__file__)
    if hashlib.sha256(base_source.read_bytes()).hexdigest() != prior["benchmark_source_sha256"]:
        raise RuntimeError("Original benchmark source changed; do not silently compare different harnesses")
    source_files = sorted(benchmark.SOURCE_DIRECTORY.glob("*.py")) + sorted(
        benchmark.SOURCE_DIRECTORY.glob("*.sql")
    )
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    load_start = os.getloadavg()
    matrix = []
    for trial in range(2):
        for index, name, weight in ((2, "overload", 1), (3, "noisy_client", 6)):
            policies = ["counterpart", "stable_latest_fifo"]
            case_seed = seed + trial * 100 + index
            random.Random(case_seed).shuffle(policies)
            for policy in policies:
                matrix.append(
                    (
                        trial + 1,
                        benchmark.Case(name, 60, noisy_weight=weight),
                        policy,
                        case_seed,
                        "stable_fifo",
                    )
                )
    for index, name, rate in ((100, "normal_ui_poll", 16), (101, "overload_ui_poll", 60)):
        policies = list(benchmark.POLICIES)
        case_seed = seed + index
        random.Random(case_seed).shuffle(policies)
        for policy in policies:
            matrix.append(
                (1, benchmark.Case(name, rate, duration=4.5, poll_ms=250), policy, case_seed, "active_poll")
            )
    results = []
    for trial, case, policy, case_seed, purpose in matrix:
        print(f"followup={purpose} trial={trial} case={case.name} policy={policy}", flush=True)
        result = execute(dsn, case, policy, case_seed)
        result.update({"trial": trial, "purpose": purpose})
        matching = next(
            row for row in prior["trials"] if row["trial"] == trial and row["case"]["name"] == case.name
        )
        if result["schedule_sha256"] != matching["schedule_sha256"]:
            raise RuntimeError("Follow-up changed the original offered schedule")
        results.append(result)
    return {
        "schema_version": "counterpart.feedback-followup.v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "original_report_sha256": hashlib.sha256(prior_bytes).hexdigest(),
        "original_report": str(prior_path),
        "original_benchmark_sha256": hashlib.sha256(base_source.read_bytes()).hexdigest(),
        "followup_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "production_source_sha256": hashes,
        "production_files_changed_during_run": [
            path.name
            for path in source_files
            if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[path.name]
        ],
        "frontend_source_sha256": hashlib.sha256(Path("web/src/App.tsx").read_bytes()).hexdigest(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "load_average_start": load_start,
            "load_average_end": os.getloadavg(),
        },
        "synthetic_service_only": True,
        "cloud_calls": 0,
        "model_accuracy_claim": False,
        "stable_fifo_policy": (
            "Latest frame replaces one pending preview but preserves that queue slot's job.created_at. "
            "A claimed slot ends; next pending slot starts with a new queue age. "
            "Capture timestamp, TTL, production admission, committed priority and in-flight fences unchanged."
        ),
        "limitations": prior["limitations"]
        + [
            "Stable FIFO adds one indexed pending lookup and one timestamp UPDATE per replacement.",
            "250ms follow-up is the active-preview polling condition, not the full adaptive browser policy.",
            "Browser idle is 1500 ms after feedback/4s timeout; saved pending captures use 650 ms.",
            "No HTTP/upload/rendering measured; browser tests cover wake and idle behavior.",
            "Original FIFO resets queue position; the stable control retains it across replacements.",
        ],
        "trials": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default="postgresql:///counterpart_test")
    parser.add_argument("--prior", type=Path, default=Path("research/feedback_benchmark.json"))
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--out", type=Path, default=Path("research/feedback_followup.json"))
    args = parser.parse_args()
    result = run(args.dsn, args.prior, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Saved {len(result['trials'])} follow-up trials to {args.out}")
