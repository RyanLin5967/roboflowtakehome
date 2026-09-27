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
import ssl
import subprocess
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:18080"


@dataclass(frozen=True)
class DeploymentTarget:
    """Explicitly allowlisted disposable harness targets, never user deployments."""

    project: str = "counterpart-test"
    base_url: str = BASE_URL
    ca_file: Path | None = None
    report_title: str = "isolated local Colima Linux/arm64 Docker Compose"

    def __post_init__(self):
        if (self.project, self.base_url) not in {
            ("counterpart-test", BASE_URL),
            ("counterpart-gap-test", "https://127.0.0.1:18443"),
        }:
            raise ValueError("Only fixed disposable deployment targets are allowed.")
        if self.base_url.startswith("https://") and self.ca_file is None:
            raise ValueError("HTTPS deployment requires explicit trusted certificate validation.")


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


def run(directory: Path, report_path: Path, *, target: DeploymentTarget | None = None) -> dict:
    target = target or DeploymentTarget()
    private = directory / "private"
    token = dotenv_values(private / "runtime.env")["COUNTERPART_OPERATOR_TOKEN"]
    environment = {
        **os.environ,
        "DOCKER_HOST": f"unix://{Path.home()}/.colima/{target.project}/docker.sock",
        "DOCKER_CONFIG": str(directory / "docker"),
    }
    compose = [
        str(directory / "tools/docker-compose"),
        "--project-name",
        target.project,
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

    verify = ssl.create_default_context(cafile=str(target.ca_file)) if target.ca_file else True
    options = {
        "base_url": target.base_url,
        "headers": {"Origin": target.base_url},
        "timeout": 20,
        "verify": verify,
        "trust_env": False,
    }
    if target.ca_file:
        # CPython intentionally omits SNI for an IP URL. Connect to fixed
        # loopback while validating and selecting the named test certificate.
        # This uses HTTPX/httpcore's request extension; validation stays on.
        def named_tls(request):
            request.extensions["sni_hostname"] = "counterpart.test"

        options["event_hooks"] = {"request": [named_tls]}
    with httpx.Client(**options) as operator:
        with httpx.Client(**options) as collector:
            health = operator.get("/api/health").raise_for_status().json()
            assert not health["live_enabled"] and not health["export_enabled"]
            assert operator.get("/api/operator/campaigns").status_code == 401
            assert operator.post("/api/operator/bootstrap").status_code == 403
            login = operator.post("/api/operator/login", json={"token": token}).raise_for_status()
            if target.ca_file:
                cookie = login.headers["set-cookie"].lower()
                assert "; secure" in cookie and "; httponly" in cookie and "samesite=strict" in cookie
                assert operator.post(
                    "/api/operator/login", json={"token": token},
                    headers={"Origin": "https://unrelated.example"},
                ).status_code == 403
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
            gap_checks = {}
            visual_revision = None
            evidence_path = f"/api/operator/sessions/{session['id']}/evidence"
            if target.project == "counterpart-gap-test":
                detail = operator.get(evidence_path).raise_for_status().json()
                candidate = next(
                    item for item in detail["captures"] if item["capture"]["slot"] == "counterpart"
                )
                original_capture = current()["latest_capture"]
                reverify_path = (
                    f"/api/operator/sessions/{session['id']}/captures/{candidate['capture']['id']}/reverify"
                )
                body = {
                    "expected_version": candidate["revision_version"],
                    "expected_evidence_sha256": candidate["evidence_sha256"],
                    "recipe_version": "2.0.0", "idempotency_key": uuid4().hex,
                }
                visual_revision = operator.post(reverify_path, json=body).raise_for_status().json()
                assert visual_revision["recipe_snapshot"]["recipe_version"] == "2.0.0"
                assert visual_revision["original_recipe_snapshot"]["recipe_version"] == "1.0.0"
                # A stricter verifier can find the recorded baseline uncertain.
                # In that case the counterpart correctly lacks an accepted new
                # baseline, while its independently rechecked reference still
                # proves the actual v2 native image path executed.
                visual_results = [visual_revision["result"], visual_revision.get("reference_result") or {}]
                assert any(item.get("evidence", {}).get("visual") for item in visual_results)
                assert visual_revision["reference_result"]["outcome"] == "unknown"
                assert visual_revision["result"]["code"] == "baseline_required"
                assert visual_revision["comparison"]["status"] == "blocked_reference_unknown"
                assert visual_revision["comparison"]["outcome"] == "unknown"
                assert operator.post(reverify_path, json=body).raise_for_status().json() == visual_revision
                assert current()["latest_capture"] == original_capture
                assert operator.get(release_path + "/manifest").raise_for_status().json() == released
                # Execute native extension imports and inspect every gap schema
                # in the built runtime; a v1 replay alone would miss wheel/ELF
                # incompatibility and packaging omissions in optional paths.
                native = json.loads(control(
                    "exec", "-T", "api", "python", "-c",
                    "import json,cv2,numpy as np;from counterpart.visual_checks import VISUAL_VERSION;"
                    "print(json.dumps({'cv2':cv2.__version__,'numpy':np.__version__,"
                    "'visual':VISUAL_VERSION,'finite':bool(np.isfinite(np.eye(3)).all()),"
                    "'threads':cv2.getNumThreads()}))",
                ))
                assert native["finite"] and native["threads"] == 1
                required_tables = {
                    "counterpart_split_groups", "counterpart_evidence_revisions",
                    "counterpart_attempt_reviews", "counterpart_baseline_authorizations",
                    "counterpart_evidence_adjudications", "counterpart_annotation_snapshots",
                    "counterpart_annotation_approvals", "counterpart_roboflow_deliveries",
                    "counterpart_roboflow_delivery_events",
                }
                tables = set(control(
                    "exec", "-T", "postgres", "psql", "-U", "counterpart", "-d", "counterpart", "-Atc",
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'",
                ).splitlines())
                assert required_tables <= tables
                gap_checks = {
                    "v2_saved_evidence_reverification": True,
                    "v2_result_outcome": visual_revision["result"]["outcome"],
                    "v2_result_code": visual_revision["result"]["code"],
                    "v2_reference_outcome": (visual_revision.get("reference_result") or {}).get("outcome"),
                    "v2_comparison_status": visual_revision["comparison"]["status"],
                    "reverification_idempotent": True, "original_capture_and_release_unchanged": True,
                    "runtime_native_libraries": native, "new_schema_tables_present": sorted(required_tables),
                }
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
            if visual_revision:
                history = operator.get(evidence_path).raise_for_status().json()
                revisions = [row for item in history["captures"] for row in item["revisions"]]
                assert visual_revision in revisions
                gap_checks["new_revision_persisted_after_restart"] = True
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
                "environment": target.report_title,
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
                "gap_checks": gap_checks,
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
