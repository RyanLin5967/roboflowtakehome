"""Regression gates for private boot configuration, not a container-boot claim."""

import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from counterpart.config import Settings
from counterpart.security import OperatorAuth

ROOT = Path(__file__).resolve().parents[1]


def prepare(directory):
    return subprocess.run(
        [sys.executable, str(ROOT / "deploy/prepare.py"), "--directory", str(directory)],
        capture_output=True,
        text=True,
    )


def config_from_runtime(path):
    values = {}
    for key, value in dotenv_values(path).items():
        name = key.removeprefix("COUNTERPART_").lower()
        values[name] = json.loads(value) if name in {"trusted_hosts", "allowed_origins"} else value
    return Settings(**values)


def test_generated_configuration_is_private_authenticated_and_cloud_off(tmp_path):
    directory = tmp_path / "private"
    process = prepare(directory)
    assert process.returncode == 0, process.stderr
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    config = config_from_runtime(directory / "runtime.env")
    assert config.require_operator
    assert config.operator_local_http and not config.operator_cookie_secure
    assert not config.live_enabled and not config.export_enabled
    assert config.storage_backend == "filesystem"
    OperatorAuth(config)  # Configuration must actually boot, not just parse YAML.
    password = (directory / "database-password").read_text().strip()
    assert len(password) >= 32 and password in config.database_url
    assert len(config.operator_token.get_secret_value()) >= 32
    assert password not in process.stdout + process.stderr
    assert config.operator_token.get_secret_value() not in process.stdout + process.stderr
    compose = dotenv_values(directory / "compose.env")
    assert Path(compose["COUNTERPART_ENV_FILE"]).resolve() == (directory / "runtime.env").resolve()


def test_prepare_refuses_overwrite_without_changing_existing_secrets(tmp_path):
    directory = tmp_path / "private"
    assert prepare(directory).returncode == 0
    previous = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert prepare(directory).returncode != 0
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == previous


def test_prepare_refuses_to_write_credentials_inside_repository():
    target = ROOT / "deployment-must-not-be-created"
    assert not target.exists()
    result = prepare(target)
    assert result.returncode != 0
    assert "outside the repository" in result.stderr
    assert not target.exists()


def test_compose_exposes_only_loopback_api_and_persists_evidence():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = compose["services"]
    assert services["api"]["ports"] == ["127.0.0.1:8080:8080"]
    assert not services["postgres"].get("ports")
    assert not services["worker"].get("ports")
    for name in ["api", "worker", "initialize"]:
        service = services[name]
        assert service["environment"]["COUNTERPART_REQUIRE_OPERATOR"] == "true"
        assert "media:/data/media" in service["volumes"]
        assert service["read_only"] and service["cap_drop"] == ["ALL"]
    assert "postgres:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert services["api"]["depends_on"]["initialize"]["condition"] == "service_completed_successfully"


def test_tls_overlay_disables_http_exception_and_limits_host_origin():
    overlay = yaml.safe_load((ROOT / "deploy/compose.tls.yaml").read_text())
    environment = overlay["services"]["api"]["environment"]
    assert environment["COUNTERPART_OPERATOR_COOKIE_SECURE"] == "true"
    assert environment["COUNTERPART_OPERATOR_LOCAL_HTTP"] == "false"
    assert environment["COUNTERPART_ALLOWED_ORIGINS"].startswith('["https://')
    assert "COUNTERPART_DOMAIN" in environment["COUNTERPART_TRUSTED_HOSTS"]
    assert "*" not in environment["COUNTERPART_TRUSTED_HOSTS"]


def test_images_pinned_and_runtime_nonroot_without_model_startup():
    dockerfile = (ROOT / "Dockerfile").read_text()
    for line in dockerfile.splitlines():
        if line.startswith("FROM "):
            assert re.search(r"@sha256:[0-9a-f]{64}(?: AS \w+)?$", line)
    assert "USER 10001:10001" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "npm ci" in dockerfile
    assert "CMD [\"counterpart\", \"serve\"]" in dockerfile
    for filename in ["compose.yaml", "deploy/compose.tls.yaml"]:
        values = yaml.safe_load((ROOT / filename).read_text())
        for name, service in values["services"].items():
            if name in {"postgres", "caddy"}:
                assert re.search(r"@sha256:[0-9a-f]{64}$", service["image"])


def test_runtime_s3_policy_cannot_delete_or_overwrite_unconditionally():
    policy = json.loads((ROOT / "deploy/s3-policy.example.json").read_text())
    statements = {item["Sid"]: item for item in policy["Statement"]}
    assert statements["RuntimeCannotDeleteEvidence"]["Effect"] == "Deny"
    assert "s3:DeleteObjectVersion" in statements["RuntimeCannotDeleteEvidence"]["Action"]
    overwrite = statements["RefuseUnconditionalReplacement"]
    assert overwrite["Effect"] == "Deny"
    assert overwrite["Condition"] == {"Null": {"s3:if-none-match": "true"}}


@pytest.mark.parametrize("origin", ["http://example.com", "https://example.com"])
def test_private_http_exception_cannot_authorize_remote_origin(tmp_path, origin):
    directory = tmp_path / "private"
    assert prepare(directory).returncode == 0
    config = config_from_runtime(directory / "runtime.env")
    config.allowed_origins = [origin]
    with pytest.raises(ValueError):
        OperatorAuth(config)
