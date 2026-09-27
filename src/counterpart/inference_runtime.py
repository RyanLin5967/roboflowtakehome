"""Durable, bounded perception dispatch for immutable capture evidence.

The cache stores observations, never verification decisions. Remote calls happen
outside transactions. Every dispatched attempt spends one durable reservation,
including ambiguous failures; a lost response cannot be made exactly-once.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .execution_contract import ExecutionContractError, assert_execution_matches, assert_result_matches
from .execution_contract import detector_configuration as model_configuration
from .providers import ProviderError

WORKER_STALE_SECONDS = 15

_SCHEMA = """
CREATE TABLE IF NOT EXISTS counterpart_workers (
    worker_id text PRIMARY KEY,
    generation uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('active', 'stopped')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS counterpart_workers_seen ON counterpart_workers(last_seen_at);
CREATE TABLE IF NOT EXISTS counterpart_inference_policy (
    provider text PRIMARY KEY,
    global_limit integer NOT NULL CHECK (global_limit > 0),
    session_limit integer NOT NULL CHECK (session_limit > 0)
);
CREATE TABLE IF NOT EXISTS counterpart_inference_cache (
    cache_key text PRIMARY KEY,
    asset_id text NOT NULL,
    model_config jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'idle', 'succeeded')),
    lease_token uuid,
    lease_expires_at timestamptz,
    result jsonb,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE counterpart_inference_cache ADD COLUMN IF NOT EXISTS reuse_count bigint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS counterpart_inference_attempts (
    id uuid PRIMARY KEY,
    cache_key text NOT NULL REFERENCES counterpart_inference_cache(cache_key),
    session_id text NOT NULL,
    job_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'abandoned')),
    lease_expires_at timestamptz NOT NULL,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS counterpart_inference_attempts_running
    ON counterpart_inference_attempts(status, lease_expires_at, session_id);
CREATE TABLE IF NOT EXISTS counterpart_inference_waiters (
    id uuid PRIMARY KEY,
    cache_key text NOT NULL,
    session_id text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS counterpart_inference_waiters_ready
    ON counterpart_inference_waiters(expires_at, session_id, created_at);
CREATE TABLE IF NOT EXISTS counterpart_inference_sessions (
    session_id text PRIMARY KEY,
    dispatched_at timestamptz NOT NULL
);
"""


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class InferenceRuntime:
    """Share frame observations and bounded provider capacity across workers.

    ``observe_frames`` renews its enclosing capture lease while provider calls
    run concurrently. At most ``concurrency`` frames from a sequence are queued
    locally; PostgreSQL also enforces deployment-wide and per-session limits.
    """

    def __init__(
        self,
        repo,
        media,
        config,
        detector,
        *,
        concurrency: int | None = None,
        global_limit: int | None = None,
        lease_seconds: float = 60,
        heartbeat_seconds: float = 5,
        poll_seconds: float = 0.1,
        wait_seconds: float = 180,
        detector_configuration: dict | None = None,
    ):
        self.repo, self.media, self.config, self.detector = repo, media, config, detector
        self.concurrency = (
            concurrency if concurrency is not None else getattr(config, "inference_concurrency", 2)
        )
        self.global_limit = (
            global_limit if global_limit is not None else getattr(config, "inference_global_limit", 4)
        )
        if (
            not isinstance(self.concurrency, int)
            or isinstance(self.concurrency, bool)
            or not isinstance(self.global_limit, int)
            or isinstance(self.global_limit, bool)
            or not 1 <= self.concurrency <= self.global_limit <= 32
        ):
            raise ValueError("Inference concurrency must satisfy 1 <= per-session <= global <= 32")
        for value in (lease_seconds, heartbeat_seconds, poll_seconds, wait_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Runtime intervals must be finite positive numbers")
        if not 0 < poll_seconds <= heartbeat_seconds < lease_seconds / 2 or wait_seconds <= 0:
            raise ValueError("Runtime heartbeat must be shorter than half the lease")
        self.lease_seconds, self.heartbeat_seconds = lease_seconds, heartbeat_seconds
        self.poll_seconds, self.wait_seconds = poll_seconds, wait_seconds
        # Credentials deliberately cannot enter this allowlisted configuration.
        # Custom adapters have separate namespaces; version their configuration
        # explicitly when semantics change. Roboflow's route includes its model version.
        self._default_identity = detector_configuration is None
        identity = (
            model_configuration(config, detector)
            if detector_configuration is None
            else detector_configuration
        )
        self._model_config = json.loads(_canonical(identity))
        self.config_digest = hashlib.sha256(_canonical(self._model_config).encode()).hexdigest()
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def model_config(self) -> dict:
        # Neither callers nor returned observation dictionaries can mutate the
        # runtime namespace after its digest/cache identity has been established.
        return json.loads(_canonical(self._model_config))

    def _assert_detector_identity(self) -> None:
        if self._default_identity and model_configuration(self.config, self.detector) != self._model_config:
            raise ProviderError("detector_configuration_changed")

    @contextmanager
    def _connection(self):
        # A stalled scheduler/DDL connection must not block lease handling
        # forever. Bounds are well below the default frame/capture leases.
        with psycopg.connect(self.repo.dsn, row_factory=dict_row, connect_timeout=5) as connection:
            connection.execute("SET LOCAL lock_timeout = '3s'")
            connection.execute("SET LOCAL statement_timeout = '5s'")
            yield connection

    def initialize(self) -> None:
        with self._initialize_lock:
            if self._initialized:
                return
            with self._connection() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(780245190224)")
                connection.execute("SELECT pg_advisory_xact_lock(780245190225)")
                connection.execute(_SCHEMA)
                connection.execute(
                    "INSERT INTO counterpart_inference_policy(provider,global_limit,session_limit) "
                    "VALUES ('roboflow',%s,%s) ON CONFLICT DO NOTHING",
                    (self.global_limit, self.concurrency),
                )
                policy = connection.execute(
                    "SELECT global_limit,session_limit FROM counterpart_inference_policy "
                    "WHERE provider='roboflow'"
                ).fetchone()
                if (policy["global_limit"], policy["session_limit"]) != (
                    self.global_limit,
                    self.concurrency,
                ):
                    raise ProviderError("inference_configuration_mismatch")
            self._initialized = True

    def cache_key(self, frame: dict) -> str:
        payload = {
            "asset_id": frame["asset_id"],
            "width": frame["width"],
            "height": frame["height"],
            "configuration": self.model_config,
        }
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()

    def _claim(self, frame: dict, request_id: str, session_id: str, job_key: str):
        key = self.cache_key(frame)
        with self._connection() as connection:
            # One short transaction orders eligibility, permits and budget; no
            # remote call or media read is made while this lock is held.
            connection.execute("SELECT pg_advisory_xact_lock(780245190225)")
            connection.execute(
                "DELETE FROM counterpart_inference_waiters WHERE expires_at <= clock_timestamp()"
            )
            connection.execute(
                "UPDATE counterpart_inference_attempts SET status='abandoned',"
                "completed_at=clock_timestamp(),error_code='inference_lease_expired' "
                "WHERE status='running' AND lease_expires_at <= clock_timestamp()"
            )
            connection.execute(
                "INSERT INTO counterpart_inference_waiters(id,cache_key,session_id,expires_at) "
                "VALUES (%s,%s,%s,clock_timestamp()+(%s*interval '1 second')) "
                "ON CONFLICT(id) DO UPDATE SET expires_at=EXCLUDED.expires_at",
                (request_id, key, session_id, self.lease_seconds),
            )
            cached = connection.execute(
                "SELECT *,lease_expires_at>clock_timestamp() AS fresh "
                "FROM counterpart_inference_cache WHERE cache_key=%s FOR UPDATE",
                (key,),
            ).fetchone()
            if cached and cached["status"] == "succeeded":
                connection.execute(
                    "UPDATE counterpart_inference_cache SET reuse_count=reuse_count+1 WHERE cache_key=%s",
                    (key,),
                )
                connection.execute("DELETE FROM counterpart_inference_waiters WHERE id=%s", (request_id,))
                return "cached", cached
            if cached and cached["status"] == "running" and cached["fresh"]:
                return "wait", None
            running = connection.execute(
                "SELECT count(*) AS count FROM counterpart_inference_attempts WHERE status='running'"
            ).fetchone()["count"]
            if running >= self.global_limit:
                return "wait", None
            # Round-robin by least-recently-dispatched session, FIFO within each
            # session. A waiter on another worker's identical asset cannot block
            # independent evidence: leased/completed cache keys are excluded.
            selected = connection.execute(
                "SELECT w.id FROM counterpart_inference_waiters w "
                "LEFT JOIN counterpart_inference_cache c ON c.cache_key=w.cache_key "
                "LEFT JOIN counterpart_inference_sessions s ON s.session_id=w.session_id "
                "WHERE w.expires_at>clock_timestamp() "
                "AND (c.cache_key IS NULL OR c.status='idle' OR "
                "(c.status='running' AND c.lease_expires_at<=clock_timestamp())) "
                "AND (SELECT count(*) FROM counterpart_inference_attempts a "
                "WHERE a.session_id=w.session_id AND a.status='running')<%s "
                "ORDER BY COALESCE(s.dispatched_at,'1970-01-01'::timestamptz),w.created_at,w.id LIMIT 1",
                (self.concurrency,),
            ).fetchone()
            if selected is None or str(selected["id"]) != request_id:
                return "wait", None
            connection.execute(
                "INSERT INTO counterpart_budget_accounts(provider) VALUES ('roboflow') ON CONFLICT DO NOTHING"
            )
            account = connection.execute(
                "SELECT reserved_units FROM counterpart_budget_accounts WHERE provider='roboflow' FOR UPDATE"
            ).fetchone()
            if account["reserved_units"] >= self.config.inference_limit:
                # Raising outside this transaction is important: delete commits.
                connection.execute("DELETE FROM counterpart_inference_waiters WHERE id=%s", (request_id,))
                return "budget", None
            connection.execute(
                "INSERT INTO counterpart_inference_cache(cache_key,asset_id,model_config,status) "
                "VALUES (%s,%s,%s,'idle') ON CONFLICT DO NOTHING",
                (key, frame["asset_id"], Jsonb(self.model_config)),
            )
            connection.execute(
                "UPDATE counterpart_inference_cache SET status='running',lease_token=%s,"
                "lease_expires_at=clock_timestamp()+(%s*interval '1 second') WHERE cache_key=%s",
                (request_id, self.lease_seconds, key),
            )
            connection.execute(
                "INSERT INTO counterpart_inference_attempts(id,cache_key,session_id,job_key,status,"
                "lease_expires_at) VALUES (%s,%s,%s,%s,'running',"
                "clock_timestamp()+(%s*interval '1 second'))",
                (request_id, key, session_id, job_key, self.lease_seconds),
            )
            connection.execute(
                "INSERT INTO counterpart_budget_reservations(provider,request_key,units) "
                "VALUES ('roboflow',%s,1)",
                (f"inference:{request_id}",),
            )
            connection.execute(
                "UPDATE counterpart_budget_accounts SET reserved_units=reserved_units+1 "
                "WHERE provider='roboflow'"
            )
            connection.execute(
                "INSERT INTO counterpart_inference_sessions(session_id,dispatched_at) "
                "VALUES (%s,clock_timestamp()) ON CONFLICT(session_id) "
                "DO UPDATE SET dispatched_at=EXCLUDED.dispatched_at",
                (session_id,),
            )
            connection.execute("DELETE FROM counterpart_inference_waiters WHERE id=%s", (request_id,))
            return "dispatch", None

    def _renew(self, key: str, request_id: str) -> bool:
        with self._connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(780245190225)")
            row = connection.execute(
                "UPDATE counterpart_inference_cache SET lease_expires_at="
                "clock_timestamp()+(%s*interval '1 second') WHERE cache_key=%s AND lease_token=%s "
                "AND status='running' AND lease_expires_at>clock_timestamp() RETURNING cache_key",
                (self.lease_seconds, key, request_id),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE counterpart_inference_attempts SET lease_expires_at="
                "clock_timestamp()+(%s*interval '1 second') WHERE id=%s AND status='running'",
                (self.lease_seconds, request_id),
            )
            return True

    def _finish(self, key: str, request_id: str, result: dict | None, error: str | None = None):
        # Keep errors allowlisted even for custom adapters; never store exception text.
        error = error if error and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error) else "inference_failed"
        with self._connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(780245190225)")
            cached = connection.execute(
                "UPDATE counterpart_inference_cache SET status=%s,result=%s,completed_at=clock_timestamp(),"
                "lease_expires_at=NULL WHERE cache_key=%s AND lease_token=%s AND status='running' "
                "AND lease_expires_at>clock_timestamp() RETURNING *",
                ("succeeded" if result is not None else "idle", Jsonb(result), key, request_id),
            ).fetchone()
            if cached is None:
                return None
            connection.execute(
                "UPDATE counterpart_inference_attempts SET status=%s,error_code=%s,"
                "completed_at=clock_timestamp() WHERE id=%s AND status='running'",
                (
                    "succeeded" if result is not None else "failed",
                    None if result is not None else error,
                    request_id,
                ),
            )
            return cached

    @contextmanager
    def _frame_heartbeat(self, key: str, request_id: str):
        stop = threading.Event()
        lost = threading.Event()

        def renew():
            while not stop.wait(self.heartbeat_seconds):
                try:
                    if not self._renew(key, request_id):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=renew, name="inference-lease", daemon=True)
        thread.start()
        try:
            yield lost
        finally:
            stop.set()
            thread.join(timeout=self.heartbeat_seconds + 1)

    @staticmethod
    def _validate_result(result: dict, contract: dict) -> None:
        try:
            assert_result_matches(result, contract["configuration"])
        except ExecutionContractError as error:
            raise ProviderError(error.code) from error

    def _frame(
        self,
        frame: dict,
        session_id: str,
        job_key: str,
        cancelled: threading.Event,
        execution_contract: dict | None = None,
    ) -> dict:
        self._assert_detector_identity()
        request_id, started = str(uuid4()), time.perf_counter()
        key = self.cache_key(frame)
        # Validate stored bytes even on a cache hit; corrupt evidence must not be
        # paired with an old good result. No image model is run locally.
        image = self.media.read(frame["asset_id"])
        try:
            while not cancelled.is_set():
                outcome, cached = self._claim(frame, request_id, session_id, job_key)
                if outcome == "budget":
                    raise ProviderError("budget_exhausted")
                if outcome == "cached":
                    if execution_contract is not None:
                        self._validate_result(cached["result"], execution_contract)
                    return self._observation(frame, cached, reused=True, started=started)
                if outcome == "dispatch":
                    with self._frame_heartbeat(key, request_id) as lease_lost:
                        try:
                            if cancelled.is_set():
                                # Reservation remains counted even if the process
                                # stops at the dispatch boundary. Never refund it.
                                raise ProviderError("capture_lease_lost")
                            result = self.detector.detect(image, frame["width"], frame["height"])
                            if not isinstance(result, dict) or not isinstance(result.get("detections"), list):
                                raise ProviderError("invalid_provider_response")
                            if not isinstance(result.get("model"), dict):
                                raise ProviderError("invalid_provider_response")
                            if execution_contract is not None:
                                self._validate_result(result, execution_contract)
                            _canonical(result)  # Reject non-finite or non-JSON provider output.
                            if lease_lost.is_set():
                                raise ProviderError("inference_lease_lost", retryable=True)
                        except Exception as exc:
                            code = exc.code if isinstance(exc, ProviderError) else "inference_failed"
                            self._finish(key, request_id, None, code)
                            raise
                        cached = self._finish(key, request_id, result)
                        if cached is None:
                            raise ProviderError("inference_lease_lost", retryable=True)
                    return self._observation(frame, cached, reused=False, started=started)
                if time.perf_counter() - started >= self.wait_seconds:
                    raise ProviderError("inference_capacity_timeout", retryable=True)
                cancelled.wait(self.poll_seconds)
            raise ProviderError("capture_lease_lost")
        finally:
            with self._connection() as connection:
                connection.execute("DELETE FROM counterpart_inference_waiters WHERE id=%s", (request_id,))

    def _observation(self, frame: dict, cached: dict, *, reused: bool, started: float) -> dict:
        result = dict(cached["result"])
        # An old provider duration must never be counted as fresh service latency.
        provider_latency = result.pop("latency_ms", None)
        return {
            **{k: v for k, v in frame.items() if k != "fixture_detections"},
            **result,
            "inference": {
                "source": "cache" if reused else "provider",
                "cache_key": cached["cache_key"],
                "configuration_digest": self.config_digest,
                "configuration": self.model_config,
                "observation_id": str(cached["lease_token"]),
                "observed_at": cached["completed_at"].isoformat(),
                "provider_latency_ms": provider_latency,
                "lookup_and_dispatch_ms": (time.perf_counter() - started) * 1000,
            },
        }

    def observe_frames(
        self,
        frames: list[dict],
        *,
        session_id: str,
        job_key: str,
        heartbeat: Callable[[], bool] | None = None,
        execution_contract: dict | None = None,
    ) -> list[dict]:
        # Contract validation precedes initialization, cache lookup and any reservation.
        self._assert_detector_identity()
        if execution_contract is not None:
            assert_execution_matches(execution_contract, self.model_config)
        self.initialize()
        heartbeat = heartbeat or (lambda: True)
        if not heartbeat():
            raise ProviderError("capture_lease_lost")
        cancelled, stopped = threading.Event(), threading.Event()

        def keep_capture_alive():
            while not stopped.wait(self.heartbeat_seconds):
                try:
                    if not heartbeat():
                        cancelled.set()
                        return
                except Exception:
                    cancelled.set()
                    return

        keeper = threading.Thread(target=keep_capture_alive, name="capture-lease", daemon=True)
        keeper.start()
        observations: list[dict | None] = [None] * len(frames)
        pending, next_index, failure = {}, 0, None
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="perception") as pool:
                while pending or next_index < len(frames):
                    while (
                        not cancelled.is_set()
                        and len(pending) < self.concurrency
                        and next_index < len(frames)
                    ):
                        future = pool.submit(
                            self._frame,
                            frames[next_index],
                            session_id,
                            job_key,
                            cancelled,
                            execution_contract,
                        )
                        pending[future] = next_index
                        next_index += 1
                    if not pending:
                        break
                    completed, _ = wait(pending, timeout=self.heartbeat_seconds, return_when=FIRST_COMPLETED)
                    for future in completed:
                        index = pending.pop(future)
                        try:
                            observations[index] = future.result()
                        except Exception as exc:
                            failure = failure or exc
                            cancelled.set()
            if failure is not None:
                raise failure
            if cancelled.is_set() or not heartbeat():
                raise ProviderError("capture_lease_lost")
            return observations
        finally:
            stopped.set()
            keeper.join(timeout=self.heartbeat_seconds + 1)

    def worker_started(self, worker_id: str) -> str:
        """Register a process incarnation, fencing any old reuse of its name."""
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", worker_id):
            raise ValueError("Use a simple, bounded worker identifier")
        self.initialize()
        generation = str(uuid4())
        with self._connection() as connection:
            # Cleanup is deliberately bounded; the independent spending and
            # observation ledgers are never pruned by worker registry maintenance.
            connection.execute(
                "DELETE FROM counterpart_workers WHERE worker_id IN "
                "(SELECT worker_id FROM counterpart_workers WHERE last_seen_at<"
                "clock_timestamp()-interval '24 hours' ORDER BY last_seen_at LIMIT 100)"
            )
            connection.execute(
                "INSERT INTO counterpart_workers(worker_id,generation,state) VALUES (%s,%s,'active') "
                "ON CONFLICT(worker_id) DO UPDATE SET generation=EXCLUDED.generation,state='active',"
                "started_at=clock_timestamp(),last_seen_at=clock_timestamp()",
                (worker_id, generation),
            )
        return generation

    def worker_heartbeat(self, worker_id: str, generation: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "UPDATE counterpart_workers SET last_seen_at=clock_timestamp() "
                "WHERE worker_id=%s AND generation=%s AND state='active' RETURNING worker_id",
                (worker_id, generation),
            ).fetchone()
        return row is not None

    def worker_stopped(self, worker_id: str, generation: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "UPDATE counterpart_workers SET state='stopped',last_seen_at=clock_timestamp() "
                "WHERE worker_id=%s AND generation=%s AND state='active' RETURNING worker_id",
                (worker_id, generation),
            ).fetchone()
        return row is not None

    def worker_status(self) -> dict:
        """Registry liveness, not a guarantee of model/provider readiness."""
        self.initialize()
        with self._connection() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT count(*) FILTER (WHERE state='active' AND last_seen_at>"
                "clock_timestamp()-(%s*interval '1 second')) AS active_workers,"
                "max(last_seen_at) AS last_seen FROM counterpart_workers",
                (WORKER_STALE_SECONDS,),
            ).fetchone()
        return {
            "active_workers": row["active_workers"],
            "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
            "stale_after_seconds": WORKER_STALE_SECONDS,
        }

    def progress(self, frames: list[dict]) -> dict:
        """Perception progress only; never imply a capture passed verification.

        Read-only after runtime initialization. Frame count includes repeated
        observations of an identical image; unique asset count shows actual
        potential provider work. Uses this runtime's exact configuration.
        """
        self.initialize()
        keys = [self.cache_key(frame) for frame in frames]
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT cache_key,status,lease_expires_at>clock_timestamp() AS active "
                "FROM counterpart_inference_cache WHERE cache_key=ANY(%s)",
                (keys,),
            ).fetchall()
        completed = {row["cache_key"] for row in rows if row["status"] == "succeeded"}
        return {
            "total_frames": len(frames),
            "observed_frames": sum(key in completed for key in keys),
            "unique_assets": len(set(keys)),
            "observed_assets": len(completed),
            "active_assets": sum(row["status"] == "running" and bool(row["active"]) for row in rows),
        }

    def cache_stats(self) -> dict:
        """Operator-only aggregate counters; no user assets or model claims."""
        self.initialize()
        with self._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            attempts = connection.execute(
                "SELECT status,count(*) AS count FROM counterpart_inference_attempts GROUP BY status"
            ).fetchall()
            cached = connection.execute(
                "SELECT count(*) AS count,COALESCE(sum(reuse_count),0)::bigint AS reuses "
                "FROM counterpart_inference_cache WHERE status='succeeded'"
            ).fetchone()
            waiting = connection.execute(
                "SELECT count(*) AS count FROM counterpart_inference_waiters "
                "WHERE expires_at>clock_timestamp()"
            ).fetchone()["count"]
            return {
                "cached_assets": cached["count"],
                "cache_reuses": cached["reuses"],
                "waiting_frames": waiting,
                "dispatch_attempts": {row["status"]: row["count"] for row in attempts},
            }
