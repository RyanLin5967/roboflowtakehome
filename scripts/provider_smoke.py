#!/usr/bin/env python3
"""One explicitly invoked, durably reserved hosted call; no local model compute."""

import io
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image

from counterpart.config import settings
from counterpart.providers import ProviderError, RoboflowDetector
from counterpart.repository import Repository


def main():
    config = settings()
    if not config.live_enabled:
        raise SystemExit("Live provider is disabled. Configure a verified included-credit allowance first.")
    repo = Repository(config.database_url)
    repo.initialize()
    # Every invocation may result in a new provider call, even after a previous timeout.
    request_key = "provider-smoke:" + uuid4().hex
    if not repo.reserve_budget("roboflow", request_key, 1, config.inference_limit):
        raise SystemExit("The local provider allowance is exhausted.")
    image = httpx.get("https://media.roboflow.com/quickstart/traffic.jpg", timeout=20)
    image.raise_for_status()
    with Image.open(io.BytesIO(image.content)) as decoded:
        width, height = decoded.size
    provider = RoboflowDetector(
        config.credentials()["ROBOFLOW_API_KEY"].get_secret_value(), config.roboflow_model
    )
    started = time.perf_counter()
    report = {
        "purpose": "Real hosted endpoint/schema check; not desk-scene quality or latency benchmarking",
        "request_reservation": request_key,
        "reserved_images": 1,
    }
    try:
        result = provider.detect(image.content, width, height)
        report.update(
            status="passed",
            model=result["model"],
            detections=len(result["detections"]),
            classes=sorted({d["label"] for d in result["detections"]}),
            wall_seconds=round(time.perf_counter() - started, 3),
        )
    except ProviderError as exc:
        report.update(status="failed", code=exc.code, http_status=exc.status_code)
    finally:
        provider.close()
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/provider_smoke_latest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
