#!/usr/bin/env python3
"""Bounded real-Postgres replay. No inference, no fake model latency benchmark."""

import argparse
import hashlib
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from counterpart.repository import Repository
from counterpart.verification import verify


def frame():
    return {
        "asset_id": hashlib.sha256(b"explicit synthetic probe").hexdigest(),
        "t_ms": 0,
        "width": 800,
        "height": 600,
        "model": {"provider": "synthetic_probe", "model_id": "none"},
        "detections": [
            {"label": "bottle", "confidence": 0.95, "box": [0.1, 0.3, 0.25, 0.7]},
            {"label": "cup", "confidence": 0.95, "box": [0.45, 0.4, 0.6, 0.7]},
        ],
    }


def submit(repo, session, slot, seq=0):
    identity = uuid4().hex
    return repo.submit(
        session["id"],
        {
            "slot": slot,
            "expected_revision": session["revision"],
            "epoch": session["epoch"],
            "client_seq": seq,
            "idempotency_key": identity,
        },
        [frame()],
        identity,
    )


def finish(repo, claim):
    capture = claim["capture"]
    result = verify("spatial", capture["slot"], capture["frames"])
    return repo.finish(claim["id"], claim["lease_token"], result, capture["frames"])


def run(dsn):
    schema = "probe_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        pg_version = conn.execute("SELECT version()").fetchone()[0]
    repo = Repository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    repo.initialize()
    try:
        sessions = [repo.create_session("spatial", "replay", "bottle", "cup")["session"] for _ in range(12)]
        start = time.perf_counter()
        for seq in range(1, 21):
            for session in sessions:
                submit(repo, session, "preview", seq)
        submitted_seconds = time.perf_counter() - start
        before = repo.metrics()
        served = []
        claim_ms = []
        while True:
            start = time.perf_counter()
            claim = repo.claim("probe-worker")
            elapsed = (time.perf_counter() - start) * 1000
            if claim is None:
                break
            claim_ms.append(elapsed)
            served.append((claim["session"]["id"], claim["capture"]["client_seq"]))
            assert finish(repo, claim)
        assert len(served) == 12 and len({sid for sid, _ in served}) == 12
        assert all(seq == 20 for _, seq in served)

        # Actual lease expiration, then a different owner reclaims the saved work.
        saved = repo.create_session("spatial", "replay", "bottle", "cup")["session"]
        submit(repo, saved, "baseline")
        orphan = repo.claim("orphaned-worker", lease_seconds=0.1)
        time.sleep(0.15)
        recovered = repo.claim("replacement-worker")
        assert recovered["capture"]["id"] == orphan["capture"]["id"]
        assert recovered["lease_token"] != orphan["lease_token"]
        assert not finish(repo, orphan)
        assert finish(repo, recovered)
        assert repo.snapshot(saved["id"])["state"] == "baseline_ready"

        return {
            "recorded_at": datetime.now(UTC).isoformat(),
            "kind": "deterministic infrastructure replay",
            "limitations": (
                "No model calls or real collectors. Burst coalescing and lease recovery only; "
                "not sustained load capacity."
            ),
            "postgres": pg_version,
            "sessions": 12,
            "previews_submitted": 240,
            "before_dispatch": before,
            "preview_jobs_served": len(served),
            "latest_preview_served_per_session": all(seq == 20 for _, seq in served),
            "max_jobs_served_per_session": max(Counter(s for s, _ in served).values()),
            "submitted_wall_seconds": round(submitted_seconds, 4),
            "claim_ms_median": round(sorted(claim_ms)[len(claim_ms) // 2], 3),
            "claim_ms_max": round(max(claim_ms), 3),
            "lease_recovery_passed": True,
            "late_old_worker_acceptance_rejected": True,
            "metrics_after": repo.metrics(),
        }
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default="postgresql:///counterpart_test")
    parser.add_argument("--out", default="artifacts/reliability_probe.json")
    args = parser.parse_args()
    result = run(args.dsn)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
