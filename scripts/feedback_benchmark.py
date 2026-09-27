#!/usr/bin/env python3
"""Measured PostgreSQL feedback scheduling, with synthetic service delays and no ML.

The production Repository executes every policy. Comparator wrappers change only
the pending-preview sampling rule and session-selection ordering. All admission,
lease, expiry, committed priority and result fencing paths remain production code.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import platform
import queue
import random
import subprocess
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.repository import (
    MAX_PENDING_JOBS,
    MAX_RUNNING_JOBS,
    PREVIEW_MAX_AGE_SECONDS,
    CapacityExceeded,
    Repository,
)
from counterpart.verification import verify

POLICIES = ("counterpart", "latest_fifo", "sampled_fifo")
REPOSITORY_SOURCE_SHA256 = hashlib.sha256(Path(inspect.getfile(Repository)).read_bytes()).hexdigest()
SOURCE_DIRECTORY = Path(inspect.getfile(Repository)).parent
FIFO_ORIGINAL = "s.preview_served_at, s.created_at, s.id FOR UPDATE OF s SKIP LOCKED LIMIT 32"
FIFO_REPLACEMENT = (
    "(SELECT min(fj.created_at) FROM counterpart_jobs fj WHERE fj.session_id=s.id "
    "AND fj.status IN ('pending','retry_wait')), s.created_at, s.id "
    "FOR UPDATE OF s SKIP LOCKED LIMIT 32"
)


def fifo_query(query: str) -> str:
    """Fail closed on a changed production selection query, not a silent bad control."""
    if query.startswith("SELECT s.id FROM counterpart_sessions s WHERE EXISTS ("):
        if query.count(FIFO_ORIGINAL) != 1:
            raise RuntimeError("Production claim query changed; review comparator before benchmarking")
        return query.replace(FIFO_ORIGINAL, FIFO_REPLACEMENT)
    return query


class FIFOConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, *args, **kwargs):
        return self.connection.execute(
            fifo_query(query) if isinstance(query, str) else query, *args, **kwargs
        )

    def __getattr__(self, name):
        return getattr(self.connection, name)


class SampledOut(Exception):
    """An intentional, measured bounded keep-first sampling decision."""


class ComparatorRepository(Repository):
    def __init__(self, dsn: str, *, keep_first: bool):
        super().__init__(dsn)
        self.keep_first = keep_first

    @contextmanager
    def _connection(self):
        with super()._connection() as connection:
            yield FIFOConnection(connection)

    def _submit(self, connection, session_id, metadata, frames, fingerprint, **kwargs):
        if self.keep_first and metadata["slot"] == "preview":
            # The same session lock as production makes sampling atomic with
            # submit/claim. Never discard committed captures or in-flight work.
            self._session(connection, session_id, lock=True)
            pending = connection.execute(
                "SELECT 1 FROM counterpart_jobs j JOIN counterpart_captures c ON c.id=j.capture_id "
                "WHERE j.session_id=%s AND j.status IN ('pending','retry_wait') AND c.slot='preview'",
                (session_id,),
            ).fetchone()
            if pending:
                raise SampledOut()
        return super()._submit(connection, session_id, metadata, frames, fingerprint, **kwargs)


@dataclass(frozen=True)
class Case:
    name: str
    rate: float
    service_ms: float = 100
    noisy_weight: int = 1
    duration: float = 3
    clients: int = 4
    workers: int = 2
    poll_ms: float = 100


@dataclass(frozen=True)
class Offer:
    number: int
    at: float
    client: int
    seq: int
    slot: str
    delay_ms: float


def offered_schedule(case: Case, seed: int) -> list[Offer]:
    """Independent fixed-rate arrivals, with seeded phase and per-frame service jitter."""
    rng = random.Random(seed)
    offers = []
    weights = [case.noisy_weight] + [1] * (case.clients - 1)
    for client, weight in enumerate(weights):
        rate = case.rate * weight / sum(weights)
        at = rng.random() / rate
        seq = 0
        while at < case.duration:
            seq += 1
            offers.append(Offer(0, at, client, seq, "preview", case.service_ms * rng.uniform(0.8, 1.2)))
            at += 1 / rate
    # Dedicated collectors avoid a committed revision intentionally invalidating
    # a preview stream. Each submits immutable saved evidence exactly once.
    for index, fraction in enumerate((0.25, 0.65)):
        offers.append(
            Offer(0, case.duration * fraction, case.clients + index, 0, "baseline", case.service_ms)
        )
    return [
        Offer(number, offer.at, offer.client, offer.seq, offer.slot, offer.delay_ms)
        for number, offer in enumerate(sorted(offers, key=lambda value: (value.at, value.client)))
    ]


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear interpolation (NumPy's default), including explicit empty samples."""
    if not values:
        return None
    if not 0 <= fraction <= 1:
        raise ValueError("Percentile must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: list[float]) -> dict:
    return {
        "n": len(values),
        "mean": mean(values) if values else None,
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def feedback_coverage(observations: list[tuple[float, float]], start: float, end: float) -> dict:
    """Integrate age of the last *observed* result; do not erase starved clients.

    observation = (client-visible time, original capture time). Before first
    feedback, age is undefined and is reported as no-feedback time, separately.
    """
    if end < start:
        raise ValueError("End must follow start")
    events = sorted((visible, captured) for visible, captured in observations if start <= visible <= end)
    if any(captured > visible for visible, captured in events):
        raise ValueError("Feedback cannot precede capture")
    duration = end - start
    no_feedback = (events[0][0] if events else end) - start
    integral = 0.0
    gaps = []
    previous = start
    for index, (visible, captured) in enumerate(events):
        gaps.append(visible - previous)
        previous = visible
        until = events[index + 1][0] if index + 1 < len(events) else end
        integral += ((until - captured) ** 2 - (visible - captured) ** 2) / 2
    gaps.append(end - previous)
    covered = duration - no_feedback
    return {
        "horizon_ms": duration * 1000,
        "no_feedback_ms": no_feedback * 1000,
        "no_feedback_fraction": no_feedback / duration if duration else 0,
        "mean_age_after_first_feedback_ms": integral / covered * 1000 if covered else None,
        "max_feedback_gap_ms": max(gaps) * 1000,
        "updates_in_horizon": len(events),
    }


def jain_fairness(counts: list[int]) -> float:
    squared = sum(value * value for value in counts)
    return sum(counts) ** 2 / (len(counts) * squared) if squared else 0


def frame(number: int) -> dict:
    return {
        "asset_id": hashlib.sha256(f"feedback-benchmark-fixture-{number}".encode()).hexdigest(),
        "t_ms": 0,
        "width": 800,
        "height": 600,
        "model": {"provider": "synthetic_observations", "model_id": "no_model_executed"},
        "detections": [
            {"label": "bottle", "confidence": 0.95, "box": [0.1, 0.3, 0.25, 0.7]},
            {"label": "cup", "confidence": 0.95, "box": [0.45, 0.4, 0.6, 0.7]},
        ],
    }


def execute_trial(dsn: str, case: Case, policy: str, seed: int) -> dict:
    if policy not in POLICIES:
        raise ValueError("Unknown benchmark policy")
    if case.workers > MAX_RUNNING_JOBS or min(case.rate, case.duration, case.service_ms, case.poll_ms) <= 0:
        raise ValueError("Use positive timings and no more workers than the production global limit")
    schema = "feedback_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-c search_path={schema}")
    repo = (
        Repository(scoped_dsn)
        if policy == "counterpart"
        else ComparatorRepository(scoped_dsn, keep_first=policy == "sampled_fifo")
    )
    try:
        repo.initialize()
        sessions = [
            repo.create_session("spatial", "replay", "bottle", "cup")["session"]
            for _ in range(case.clients + 2)
        ]
        schedule = offered_schedule(case, seed)
        asset_numbers = {frame(offer.number)["asset_id"]: offer.number for offer in schedule}
        schedule_digest = hashlib.sha256(
            json.dumps([asdict(o) for o in schedule], sort_keys=True).encode()
        ).hexdigest()
        offered = {}
        accepted = {}
        outcomes = Counter()
        served = []
        visible = []
        samples = []
        errors = []
        lock = threading.Lock()
        inbound = queue.Queue(maxsize=MAX_PENDING_JOBS)
        submit_done = threading.Event()
        finish = threading.Event()
        start_gate = threading.Event()
        start = time.perf_counter() + 0.15

        def submitter():
            start_gate.wait()
            try:
                while True:
                    try:
                        item = inbound.get(timeout=0.1)
                    except queue.Empty:
                        if finish.is_set():
                            return
                        continue
                    if item is None:
                        return
                    offer, captured = item
                    session = sessions[offer.client]
                    began = time.perf_counter()
                    key = f"feedback-{offer.number:08d}"
                    try:
                        response = repo.submit(
                            session["id"],
                            {
                                "slot": offer.slot,
                                "expected_revision": session["revision"],
                                "epoch": session["epoch"],
                                "client_seq": offer.seq,
                                "idempotency_key": key,
                            },
                            [frame(offer.number)],
                            key,
                        )
                    except SampledOut:
                        with lock:
                            outcomes["sampled_out"] += 1
                        continue
                    except CapacityExceeded:
                        with lock:
                            outcomes["capacity_rejected"] += 1
                        continue
                    ended = time.perf_counter()
                    with lock:
                        accepted[response["capture"]["id"]] = {
                            "offer": offer,
                            "captured": captured,
                            "submitted": ended,
                            "submit_ms": (ended - began) * 1000,
                            "driver_wait_ms": (began - captured) * 1000,
                        }
                        outcomes["admitted"] += 1
            except Exception as error:
                errors.append(f"submitter: {type(error).__name__}: {error}")
            finally:
                submit_done.set()

        def worker(index):
            start_gate.wait()
            try:
                while not finish.is_set():
                    claim = repo.claim(f"feedback-worker-{index}")
                    if claim is None:
                        finish.wait(0.01)
                        continue
                    capture = claim["capture"]
                    # Admission may commit just before the submitter records its
                    # timing. Lookup from immutable capture identity, not timing order.
                    number = asset_numbers[capture["frames"][0]["asset_id"]]
                    offer = schedule[number]
                    service_start = time.perf_counter()
                    time.sleep(offer.delay_ms / 1000)
                    service_end = time.perf_counter()
                    result = verify("spatial", capture["slot"], capture["frames"])
                    current = repo.finish(claim["id"], claim["lease_token"], result, capture["frames"])
                    persisted = time.perf_counter()
                    with lock:
                        served.append(
                            {
                                "number": number,
                                "client": offer.client,
                                "slot": offer.slot,
                                "service_start": service_start,
                                "service_end": service_end,
                                "persisted": persisted,
                                "current": current,
                            }
                        )
            except Exception as error:
                errors.append(f"worker: {type(error).__name__}: {error}")
                finish.set()

        def observer():
            start_gate.wait()
            seen = set()
            try:
                while not finish.is_set():
                    for client, session in enumerate(sessions):
                        snapshot = repo.snapshot(session["id"])
                        observed = time.perf_counter()
                        capture = snapshot["feedback"] if client < case.clients else snapshot["baseline"]
                        if capture and capture["id"] not in seen:
                            seen.add(capture["id"])
                            with lock:
                                visible.append(
                                    {"capture_id": capture["id"], "client": client, "at": observed}
                                )
                    with repo._connection() as connection:
                        active = connection.execute(
                            "SELECT count(*) AS n FROM counterpart_jobs "
                            "WHERE status IN ('pending','running','retry_wait')"
                        ).fetchone()["n"]
                    with lock:
                        samples.append({"at": time.perf_counter(), "active_jobs": active})
                    finish.wait(case.poll_ms / 1000)
            except Exception as error:
                errors.append(f"observer: {type(error).__name__}: {error}")
                finish.set()

        threads = [threading.Thread(target=submitter, name="feedback-submit")]
        threads += [
            threading.Thread(target=worker, args=(index,), name=f"feedback-worker-{index}")
            for index in range(case.workers)
        ]
        threads += [threading.Thread(target=observer, name="feedback-observer")]
        for thread in threads:
            thread.start()
        start_gate.set()
        try:
            for offer in schedule:
                delay = start + offer.at - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                captured = time.perf_counter()
                offered[offer.number] = {
                    "captured": captured,
                    "offer": offer,
                    "emitter_lateness_ms": (captured - start - offer.at) * 1000,
                }
                try:
                    inbound.put_nowait((offer, captured))
                except queue.Full:
                    outcomes["driver_dropped"] += 1
            offered_end = start + case.duration
            time.sleep(max(0, offered_end - time.perf_counter()))
            inbound.put(None, timeout=5)
            if not submit_done.wait(10):
                raise RuntimeError("Submission driver did not finish")
            deadline = time.perf_counter() + 10
            while time.perf_counter() < deadline and not errors:
                with repo._connection() as connection:
                    active = connection.execute(
                        "SELECT count(*) AS n FROM counterpart_jobs "
                        "WHERE status IN ('pending','running','retry_wait')"
                    ).fetchone()["n"]
                if not active:
                    # Let a real read observe the final persisted answer. This
                    # drain never changes the fixed offered-period AoI horizon.
                    time.sleep(case.poll_ms / 1000 + 0.15)
                    break
                time.sleep(0.03)
            else:
                if not errors:
                    raise RuntimeError("Queue did not drain within the bounded deadline")
        finally:
            finish.set()
            for thread in threads:
                thread.join(timeout=5)
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError("A benchmark thread failed to stop")
        ended = time.perf_counter()
        if errors:
            raise RuntimeError("; ".join(errors))

        with repo._connection() as connection:
            statuses = connection.execute(
                "SELECT c.slot,c.status,count(*) AS n FROM counterpart_captures c GROUP BY c.slot,c.status"
            ).fetchall()
        per_client = []
        visible_rows = []
        for observation in visible:
            item = accepted[observation["capture_id"]]
            visible_rows.append({**observation, **item})
        for client in range(case.clients):
            updates = [(row["at"], row["captured"]) for row in visible_rows if row["client"] == client]
            first_offer = min(v["captured"] for v in offered.values() if v["offer"].client == client)
            per_client.append(
                {
                    "client": client,
                    "offered": sum(value["offer"].client == client for value in offered.values()),
                    "work_executed": sum(row["client"] == client for row in served),
                    "work_started_in_offered_horizon": sum(
                        row["client"] == client and row["service_start"] <= offered_end for row in served
                    ),
                    "served_fraction_of_offered": sum(row["client"] == client for row in served)
                    / sum(value["offer"].client == client for value in offered.values()),
                    "client_visible_updates": len(updates),
                    **feedback_coverage(updates, first_offer, offered_end),
                }
            )
        preview_rows = [row for row in visible_rows if row["offer"].slot == "preview"]
        commits = [row for row in visible_rows if row["offer"].slot == "baseline"]
        served_capture_times = [
            (row["persisted"] - offered[row["number"]]["captured"]) * 1000
            for row in served
            if row["slot"] == "preview" and row["current"]
        ]
        counts = [value["work_executed"] for value in per_client]
        horizon_counts = [value["work_started_in_offered_horizon"] for value in per_client]
        assert len(offered) == sum(outcomes.values()), "Every offered frame needs an explicit outcome"
        actual_span = max(v["captured"] for v in offered.values()) - min(
            v["captured"] for v in offered.values()
        )
        return {
            "case": asdict(case),
            "policy": policy,
            "seed": seed,
            "schedule_sha256": schedule_digest,
            "planned_preview_rate_per_second": case.rate,
            "offered_count": len(offered),
            "offered_preview_count": len(offered) - 2,
            "actual_arrival_span_ms": actual_span * 1000,
            "planned_offered_horizon_ms": case.duration * 1000,
            "drain_and_final_observation_ms": (ended - offered_end) * 1000,
            "outcomes": {
                name: outcomes[name]
                for name in ("admitted", "sampled_out", "capacity_rejected", "driver_dropped")
            },
            "capture_statuses": statuses,
            "service_calls": len(served),
            "stale_service_results": sum(not row["current"] for row in served),
            "service_delay_ms": distribution(
                [(row["service_end"] - row["service_start"]) * 1000 for row in served]
            ),
            "emitter_lateness_ms": distribution([value["emitter_lateness_ms"] for value in offered.values()]),
            "driver_wait_ms": distribution([value["driver_wait_ms"] for value in accepted.values()]),
            "submit_transaction_ms": distribution([value["submit_ms"] for value in accepted.values()]),
            "capture_to_persisted_feedback_ms": distribution(served_capture_times),
            "capture_to_client_observed_feedback_ms": distribution(
                [(row["at"] - row["captured"]) * 1000 for row in preview_rows]
            ),
            "committed": {
                "offered": 2,
                "persisted": sum(row["slot"] == "baseline" and row["current"] for row in served),
                "client_observed": len(commits),
                "capture_to_client_observed_ms": distribution(
                    [(row["at"] - row["captured"]) * 1000 for row in commits]
                ),
            },
            "queue_drained": True,
            "sampled_max_active_jobs": max(value["active_jobs"] for value in samples),
            "preview_work_jain_fairness": jain_fairness(counts),
            "preview_work_jain_fairness_offered_horizon": jain_fairness(horizon_counts),
            "per_client": per_client,
            "raw_service_events": [
                {
                    **row,
                    "service_start": row["service_start"] - start,
                    "service_end": row["service_end"] - start,
                    "persisted": row["persisted"] - start,
                }
                for row in served
            ],
            "raw_visible_events": [
                {
                    "number": row["offer"].number,
                    "client": row["client"],
                    "captured": row["captured"] - start,
                    "visible": row["at"] - start,
                }
                for row in visible_rows
            ],
            "raw_offer_events": [
                {**asdict(value["offer"]), "captured": value["captured"] - start}
                for value in offered.values()
            ],
        }
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def run(dsn: str, duration: float, repeats: int, seed: int) -> dict:
    source_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    benchmark_source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    source_files = sorted(SOURCE_DIRECTORY.glob("*.py")) + sorted(SOURCE_DIRECTORY.glob("*.sql"))
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    load_average_start = os.getloadavg() if hasattr(os, "getloadavg") else None
    hardware = {"logical_cpus": os.cpu_count(), "processor": platform.processor()}
    if platform.system() == "Darwin":
        for field, name in (("model", "hw.model"), ("memory_bytes", "hw.memsize")):
            hardware[field] = subprocess.check_output(["sysctl", "-n", name], text=True).strip()
    cases = [
        Case("low", 6, duration=duration),
        Case("near_ideal_capacity", 16, duration=duration),
        Case("overload", 60, duration=duration),
        Case("noisy_client", 60, noisy_weight=6, duration=duration),
        Case("slower_service", 16, service_ms=200, duration=duration),
    ]
    with psycopg.connect(dsn) as connection:
        postgres = connection.execute("SELECT version()").fetchone()[0]
    results = []
    execution_order = []
    matrix = [(trial, index, case) for trial in range(repeats) for index, case in enumerate(cases)]
    # Match the current idle collector's 1.5s polling cadence too. The standard
    # 100ms reader isolates scheduling; its timing must not stand in for the UI.
    matrix += [
        (0, 100, Case("normal_ui_poll", 16, duration=max(4.5, duration), poll_ms=1500)),
        (0, 101, Case("overload_ui_poll", 60, duration=max(4.5, duration), poll_ms=1500)),
    ]
    for trial, index, case in matrix:
        order = list(POLICIES)
        random.Random(seed + trial * 100 + index).shuffle(order)
        for policy in order:
            print(f"trial={trial + 1} case={case.name} policy={policy}", flush=True)
            result = execute_trial(dsn, case, policy, seed + trial * 100 + index)
            result["trial"] = trial + 1
            results.append(result)
            execution_order.append([trial + 1, case.name, policy])
    repository_source_unchanged = (
        hashlib.sha256(Path(inspect.getfile(Repository)).read_bytes()).hexdigest() == REPOSITORY_SOURCE_SHA256
    )
    return {
        "schema_version": "counterpart.feedback-benchmark.v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_revision": source_revision,
        "repository_source_sha256": REPOSITORY_SOURCE_SHA256,
        "repository_source_unchanged_during_run": repository_source_unchanged,
        "production_source_sha256": source_hashes,
        "production_files_changed_during_run": [
            path.name
            for path in source_files
            if hashlib.sha256(path.read_bytes()).hexdigest() != source_hashes[path.name]
        ],
        "benchmark_source_sha256": benchmark_source_sha256,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "postgres": postgres,
            "hardware": hardware,
            "load_average_start": load_average_start,
            "load_average_end": os.getloadavg() if hasattr(os, "getloadavg") else None,
        },
        "synthetic_service_only": True,
        "cloud_calls": 0,
        "model_accuracy_claim": False,
        "bounds": {
            "global_active_jobs": MAX_PENDING_JOBS,
            "running_job_limit": MAX_RUNNING_JOBS,
            "workers_per_trial": 2,
            "pending_previews_per_session": 1,
            "inflight_previews_per_session": 1,
            "preview_max_age_seconds": PREVIEW_MAX_AGE_SECONDS,
            "driver_queue_capacity": MAX_PENDING_JOBS,
        },
        "policies": {
            "counterpart": "Latest pending preview; least-recently-served session; committed priority.",
            "latest_fifo": "Latest pending preview; FIFO oldest pending session; committed priority.",
            "sampled_fifo": "Keep first pending; sample new arrivals; FIFO; committed priority.",
        },
        "limitations": [
            "Synthetic observations and delays; no Roboflow latency, quality, upload or rendering measured.",
            "Actual Repository transactions/snapshot reads; no HTTP API or inference-runtime cache/dispatch.",
            "100 ms observer is a benchmark client, not the UI's 1.5 second idle polling interval.",
            "Two cases use the UI's 1.5s poll cadence, without HTTP/upload/browser rendering.",
            "Short trials: descriptive timings, not significance, production capacity or an SLO.",
            "Visible latency conditions on results seen; no-feedback time/AoI expose missing updates.",
            "Offer rates stress the scheduler; they do not match the UI's 0.5 Hz preview cadence.",
            "No failures injected here; worker-death recovery has a separate product fault probe.",
            "Jain counts describe work parity, not demand-normalized fairness; inspect offer counts too.",
        ],
        "execution_order": execution_order,
        "trials": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default="postgresql:///counterpart_test")
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--out", type=Path, default=Path("research/feedback_benchmark.json"))
    args = parser.parse_args()
    if not 1 <= args.duration <= 15 or not 1 <= args.repeats <= 5:
        parser.error("Use 1–15 second trials and 1–5 repeats for this bounded local check")
    report = run(args.dsn, args.duration, args.repeats, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {len(report['trials'])} completed comparisons to {args.out}")
