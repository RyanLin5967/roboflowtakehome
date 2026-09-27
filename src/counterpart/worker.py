"""Fenced workers: remote perception outside transactions, durable acceptance inside."""

import logging
import signal
import threading
import uuid

import psycopg

from .config import Settings, settings
from .inference_runtime import InferenceRuntime
from .object_storage import EvidenceStore, MediaUnavailable, create_media_store
from .recipes import RecipeContractError, assert_runtime_compatible
from .repository import Repository
from .verification import verify

log = logging.getLogger(__name__)
WORKER_HEARTBEAT_SECONDS = 5


class Worker:
    def __init__(
        self,
        repo: Repository,
        media: EvidenceStore,
        config: Settings,
        detector=None,
        worker_id: str | None = None,
        export_worker=None,
    ):
        self.repo, self.media, self.config = repo, media, config
        self.detector = detector
        self.runtime = InferenceRuntime(repo, media, config, detector)
        self.inference = self.runtime if detector is not None else None
        self.export_worker = export_worker
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.stop = threading.Event()

    def once(self) -> bool:
        job = self.repo.claim(self.worker_id)
        if job is None:
            return False
        capture, session = job["capture"], job["session"]
        job_id, lease = job["id"], job["lease_token"]
        source = capture["frames"]
        try:
            snapshot = session.get("recipe_snapshot")
            assert_runtime_compatible(snapshot)
            if any(snapshot[key] != session[key] for key in ("recipe", "object_a", "object_b")):
                raise RecipeContractError("Session roles differ from its frozen recipe.")
            if session["mode"] == "live":
                if not self.config.live_enabled or self.inference is None:
                    self.repo.fail(job_id, lease, "live_disabled")
                    return True
                # Each image dispatch owns a durable reservation/checkpoint.
                # A retried sequence reuses completed immutable observations;
                # ambiguous attempts remain charged and never get refunded.
                observations = self.inference.observe_frames(
                    source,
                    session_id=session["id"],
                    job_key=f"{job_id}:{lease}",
                    heartbeat=lambda: self.repo.heartbeat(job_id, lease),
                )
            else:
                observations = []
                for frame in source:
                    if not self.repo.heartbeat(job_id, lease):
                        return True
                    self.media.read(frame["asset_id"])
                    observations.append(
                        {
                            **{k: v for k, v in frame.items() if k != "fixture_detections"},
                            "detections": frame["fixture_detections"],
                            "model": {"provider": "fixture", "model_id": "illustrative-replay-v1"},
                        }
                    )
            baseline_capture = session.get("baseline")
            baseline = (
                {**baseline_capture["result"], "frames": baseline_capture["frames"]}
                if baseline_capture and baseline_capture.get("result")
                else None
            )
            result = verify(
                session["recipe"],
                capture["slot"],
                observations,
                baseline,
                object_a=session["object_a"],
                object_b=session["object_b"],
                interrupted=capture.get("interrupted", capture.get("metadata", {}).get("interrupted", False)),
            )
            self.repo.finish(job_id, lease, result, observations)
        except Exception as exc:
            # Exception text may contain a provider URL/secret. Persist an allowlisted code only.
            from .providers import ProviderError

            if isinstance(exc, RecipeContractError):
                self.repo.fail(job_id, lease, "unsupported_recipe")
            elif isinstance(exc, psycopg.OperationalError):
                self.repo.fail(job_id, lease, "database_unavailable", retryable=True)
            elif isinstance(exc, MediaUnavailable):
                self.repo.fail(job_id, lease, "media_unavailable", retryable=True)
            elif isinstance(exc, ProviderError):
                self.repo.fail(job_id, lease, exc.code, retryable=exc.retryable)
            elif isinstance(exc, (OSError, ValueError, KeyError)):
                self.repo.fail(job_id, lease, "invalid_evidence")
                log.warning("Capture failed validation (%s)", type(exc).__name__)
            else:
                self.repo.fail(job_id, lease, "worker_error")
                log.error("Worker failed (%s); no exception text logged", type(exc).__name__)
        return True

    def run(self):
        generation = None
        while generation is None and not self.stop.is_set():
            try:
                generation = self.runtime.worker_started(self.worker_id)
            except Exception as exc:
                log.error("Worker registration unavailable (%s)", type(exc).__name__)
                self.stop.wait(2)
        if generation is None:
            return
        monitor_stop = threading.Event()

        def keep_alive():
            while not monitor_stop.wait(WORKER_HEARTBEAT_SECONDS):
                try:
                    if not self.runtime.worker_heartbeat(self.worker_id, generation):
                        # Another incarnation reused this name. Do not make it
                        # appear healthy by reviving an old registration.
                        self.stop.set()
                        return
                except Exception as exc:
                    log.error("Worker heartbeat unavailable (%s)", type(exc).__name__)

        monitor = threading.Thread(target=keep_alive, name="worker-presence", daemon=True)
        monitor.start()
        try:
            while not self.stop.is_set():
                try:
                    if not self.once():
                        if self.export_worker is None or not self.export_worker.once():
                            self.stop.wait(0.25)
                except Exception as exc:
                    log.error("Worker loop unavailable (%s)", type(exc).__name__)
                    self.stop.wait(2)
        finally:
            monitor_stop.set()
            monitor.join(timeout=6)
            try:
                self.runtime.worker_stopped(self.worker_id, generation)
            except Exception as exc:
                # If the database is unavailable, the registry expires by time.
                log.error("Worker stop acknowledgement unavailable (%s)", type(exc).__name__)


def main():
    config = settings()
    repo = Repository(config.database_url)
    repo.initialize()
    detector = None
    if config.live_enabled:
        from .providers import RoboflowDetector

        key = config.credentials()["ROBOFLOW_API_KEY"].get_secret_value()
        if key:
            detector = RoboflowDetector(key, config.roboflow_model)
    media = create_media_store(config)
    export_worker = None
    destination_resolver = None
    if config.export_enabled and config.export_project:
        from .destination import DestinationResolver
        from .exporting import ExportWorker
        from .providers import RoboflowExporter

        credentials = config.credentials()
        key = credentials["ROBOFLOW_API_KEY"].get_secret_value()
        workspace = credentials["ROBOFLOW_WORKSPACE"].get_secret_value()
        if key and workspace:
            exporter = RoboflowExporter(key, workspace, config.export_project)
            destination_resolver = DestinationResolver(config)
            export_worker = ExportWorker(
                repo, media, config, exporter, destination_resolver=destination_resolver
            )
    worker = Worker(repo, media, config, detector, export_worker=export_worker)
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    try:
        worker.run()
    finally:
        if detector is not None:
            detector.close()
        if export_worker is not None:
            exporter.close()
        if destination_resolver is not None:
            destination_resolver.close()


if __name__ == "__main__":
    main()
