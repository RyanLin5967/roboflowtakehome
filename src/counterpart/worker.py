"""Fenced workers: remote perception outside transactions, durable acceptance inside."""

import logging
import signal
import threading
import uuid

from .config import Settings, settings
from .repository import Repository
from .storage import MediaStore
from .verification import verify

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        repo: Repository,
        media: MediaStore,
        config: Settings,
        detector=None,
        worker_id: str | None = None,
        export_worker=None,
    ):
        self.repo, self.media, self.config = repo, media, config
        self.detector = detector
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
            if session["mode"] == "live":
                if not self.config.live_enabled or self.detector is None:
                    self.repo.fail(job_id, lease, "live_disabled")
                    return True
                # Reserve the whole sequence before any model call; no half-funded capture.
                if not self.repo.reserve_budget(
                    "roboflow", f"{job_id}:{lease}", len(source), self.config.inference_limit
                ):
                    self.repo.fail(job_id, lease, "budget_exhausted")
                    return True
            observations = []
            for frame in source:
                if not self.repo.heartbeat(job_id, lease):
                    return True
                image = self.media.read(frame["asset_id"])
                if session["mode"] == "replay":
                    detected = {
                        "detections": frame["fixture_detections"],
                        "model": {"provider": "fixture", "model_id": "illustrative-replay-v1"},
                    }
                else:
                    detected = self.detector.detect(image, frame["width"], frame["height"])
                observations.append(
                    {**{k: v for k, v in frame.items() if k != "fixture_detections"}, **detected}
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

            if isinstance(exc, ProviderError):
                self.repo.fail(job_id, lease, exc.code, retryable=exc.retryable)
            elif isinstance(exc, (OSError, ValueError, KeyError)):
                self.repo.fail(job_id, lease, "invalid_evidence")
                log.warning("Capture failed validation (%s)", type(exc).__name__)
            else:
                self.repo.fail(job_id, lease, "worker_error")
                log.error("Worker failed (%s); no exception text logged", type(exc).__name__)
        return True

    def run(self):
        while not self.stop.is_set():
            try:
                if not self.once():
                    if self.export_worker is None or not self.export_worker.once():
                        self.stop.wait(0.25)
            except Exception as exc:
                log.error("Worker loop unavailable (%s)", type(exc).__name__)
                self.stop.wait(2)


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
    media = MediaStore(config.media_dir)
    export_worker = None
    if config.export_enabled and config.export_project:
        from .exporting import ExportWorker
        from .providers import RoboflowExporter

        credentials = config.credentials()
        key = credentials["ROBOFLOW_API_KEY"].get_secret_value()
        workspace = credentials["ROBOFLOW_WORKSPACE"].get_secret_value()
        if key and workspace:
            exporter = RoboflowExporter(key, workspace, config.export_project)
            export_worker = ExportWorker(repo, media, config, exporter)
    worker = Worker(repo, media, config, detector, export_worker=export_worker)
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    worker.run()


if __name__ == "__main__":
    main()
