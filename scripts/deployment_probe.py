"""Exercise an explicitly disposable, cloud-disabled container deployment.

Requires a running counterpart-test Compose stack on loopback port 18080. It
creates fixture data, SIGKILLs only that project's worker, and restarts that
project's API/worker/PostgreSQL to verify persistence. Never run against live
data. Configuration paths contain credentials and are never printed.
"""

import argparse
import hashlib
import io
import json
import os
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:18080"


def wait_for(action, ready, *, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = action()
            if ready(result):
                return result
        except (httpx.TransportError, httpx.HTTPStatusError):
            pass
        time.sleep(0.2)
    raise RuntimeError("Disposable deployment did not reach the expected state in time.")


def run(directory: Path, report_path: Path) -> dict:
    private = directory / "private"
    token = dotenv_values(private / "runtime.env")["COUNTERPART_OPERATOR_TOKEN"]
    environment = {
        **os.environ,
        "DOCKER_HOST": f"unix://{Path.home()}/.colima/counterpart-test/docker.sock",
        "DOCKER_CONFIG": str(directory / "docker"),
    }
    compose = [
        str(directory / "tools/docker-compose"),
        "--project-name",
        "counterpart-test",
        "--env-file",
        str(private / "compose.env"),
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(directory / "overlay.yaml"),
    ]

    def command(arguments):
        result = subprocess.run(
            arguments,
            env=environment,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            # Command output/config may carry credentials. Keep failure reporting
            # generic and inspect allowlisted container state separately.
            raise RuntimeError("A disposable container operation failed.")
        return result.stdout.strip()

    def control(*arguments):
        return command(compose + list(arguments))

    with httpx.Client(base_url=BASE_URL, headers={"Origin": BASE_URL}, timeout=20) as operator:
        with httpx.Client(base_url=BASE_URL, headers={"Origin": BASE_URL}, timeout=20) as collector:
            health = operator.get("/api/health").raise_for_status().json()
            assert not health["live_enabled"] and not health["export_enabled"]
            assert operator.get("/api/operator/campaigns").status_code == 401
            assert operator.post("/api/operator/bootstrap").status_code == 403
            operator.post("/api/operator/login", json={"token": token}).raise_for_status()
            worker_state = wait_for(
                lambda: operator.get("/api/operator/runtime").raise_for_status().json(),
                lambda state: state["workers"]["active_workers"] == 1,
            )
            campaign = (
                operator.post(
                    "/api/operator/campaigns",
                    json={
                        "name": "Disposable container acceptance probe",
                        "mode": "replay",
                        "recipe": "spatial",
                        "target_pairs": 1,
                    },
                )
                .raise_for_status()
                .json()
            )
            base = f"/api/operator/campaigns/{campaign['id']}"
            invite = (
                operator.post(
                    base + "/invites",
                    json={
                        "label": "Generated fixture only",
                        "max_sessions": 1,
                        "expires_in_hours": 1,
                    },
                )
                .raise_for_status()
                .json()
            )
            joined = (
                collector.post(
                    "/api/invites/sessions",
                    json={
                        "token": invite["token"],
                        "idempotency_key": uuid4().hex,
                    },
                )
                .raise_for_status()
                .json()
            )
            collector.headers["Authorization"] = f"Bearer {joined['token']}"
            assert collector.get("/api/operator/campaigns").status_code == 401
            session = joined["session"]
            session_path = f"/api/sessions/{session['id']}"

            def current():
                return collector.get(session_path).raise_for_status().json()

            def capture(scenario, state):
                return (
                    collector.post(
                        session_path + "/replay",
                        json={
                            "scenario": scenario,
                            "expected_revision": state["revision"],
                            "epoch": state["epoch"],
                            "idempotency_key": uuid4().hex,
                        },
                    )
                    .raise_for_status()
                    .json()
                )

            capture("baseline", session)
            session = wait_for(current, lambda state: state["latest_capture"]["status"] == "accepted")
            original_baseline = session["baseline"]["id"]
            worker = control("ps", "-q", "worker")
            assert worker and "\n" not in worker
            command(["docker", "kill", "--signal", "SIGKILL", worker])
            control("up", "-d", "--no-deps", "worker")
            assert current()["baseline"]["id"] == original_baseline

            # Keep the worker stopped while submitting to prove queue durability.
            control("stop", "--timeout", "5", "worker")
            queued = capture("counterpart", current())
            time.sleep(0.4)
            pending = current()
            assert pending["latest_capture"]["id"] == queued["capture"]["id"]
            assert pending["latest_capture"]["status"] in {"queued", "pending"}
            control("start", "worker")
            session = wait_for(current, lambda state: state["latest_capture"]["status"] == "accepted")
            assert session["baseline"]["id"] == original_baseline
            pair = (
                collector.post(
                    session_path + "/publish",
                    json={
                        "expected_revision": session["revision"],
                    },
                )
                .raise_for_status()
                .json()["pair"]
            )
            again = (
                collector.post(
                    session_path + "/publish",
                    json={
                        "expected_revision": session["revision"],
                    },
                )
                .raise_for_status()
                .json()["pair"]
            )
            assert pair["id"] == again["id"]
            operator.post(
                base + f"/pairs/{pair['id']}/review",
                json={
                    "decision": "approved",
                    "expected_version": 0,
                    "reason": "Container fixture validation; not a physical capture accuracy claim.",
                    "idempotency_key": uuid4().hex,
                },
            ).raise_for_status()
            released = (
                operator.post(
                    base + "/releases",
                    json={
                        "pair_ids": [pair["id"]],
                        "include_replay": True,
                        "idempotency_key": uuid4().hex,
                    },
                )
                .raise_for_status()
                .json()
            )
            assert released["evidence_kind"] == "fixture_replay" and released["pair_count"] == 1
            release_path = f"/api/operator/releases/{released['id']}"

            def archive_counts():
                response = operator.get(release_path + "/archive").raise_for_status()
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    assert manifest == released
                    assets = [name for name in archive.namelist() if name.startswith("assets/")]
                    for name in assets:
                        assert hashlib.sha256(archive.read(name)).hexdigest() == name.split("/")[1]
                    examples = [json.loads(row) for row in archive.read("examples.jsonl").splitlines()]
                    assert examples and all(row["human_reviewed"] for row in examples)
                    return len(assets), len(examples), len(response.content)

            assets, examples, archive_bytes = archive_counts()
            assert assets == 2 and examples == 2
            # Restart all durable services, preserving only their named volumes.
            control("stop", "--timeout", "5", "worker", "api", "postgres")
            control("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
            wait_for(
                lambda: operator.get("/api/health").raise_for_status(),
                lambda result: result.status_code == 200,
            )
            assert current()["baseline"]["id"] == original_baseline
            assert operator.get(release_path + "/manifest").raise_for_status().json() == released
            assert archive_counts()[:2] == (assets, examples)
            runtime = operator.get("/api/operator/runtime").raise_for_status().json()
            assert runtime["inference"]["dispatch_attempts"] == {}, "Replay must not dispatch model calls."
            uid = control("exec", "-T", "api", "python", "-c", "import os;print(os.getuid())")
            assert uid == "10001"
            credentials_absent = control(
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "import os;print(all(not os.getenv(k) for k in "
                "['ROBOFLOW_API_KEY','MODAL_TOKEN_ID','MODAL_TOKEN_SECRET','AWS_ACCESS_KEY_ID']))",
            )
            assert credentials_absent == "True"
            report = {
                "executed_at": datetime.now(UTC).isoformat(),
                "source_revision": command(["git", "rev-parse", "HEAD"]),
                "image_id": command(
                    ["docker", "image", "inspect", "counterpart:local", "--format", "{{.Id}}"]
                ),
                "environment": "isolated local Colima Linux/arm64 Docker Compose",
                "vm_limits": {"cpus": 2, "memory_gib": 2, "data_disk_gib": 8, "root_disk_gib": 6},
                "mode": "fixture_replay",
                "cloud_calls": 0,
                "model_accuracy_claim": False,
                "checks": {
                    "locked_image_build": True,
                    "postgres_api_worker_start": True,
                    "anonymous_operator_refused": True,
                    "local_bootstrap_refused": True,
                    "operator_login": True,
                    "invitation_collection": True,
                    "collector_cannot_be_operator": True,
                    "worker_sigkill_preserves_baseline": True,
                    "stopped_worker_queue_recovered": True,
                    "idempotent_publication": True,
                    "reviewed_immutable_release": True,
                    "archive_hash_readback": True,
                    "all_service_restart_persistence": True,
                    "nonroot_api_uid": int(uid),
                    "provider_credentials_absent": True,
                },
                "archive": {"assets": assets, "examples": examples, "bytes": archive_bytes},
                "initial_workers": worker_state["workers"]["active_workers"],
                "limits": ["No physical camera validation", "No hosted S3 check", "No public TLS issuance"],
            }
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "research/deployment_probe.json")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.directory, arguments.report), indent=2))
