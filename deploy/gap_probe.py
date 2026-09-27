"""Reproduce the bounded, private HTTPS deployment gate without provider calls.

Phases are deliberately separate: prepare/start do not build changing source.
Run build/check only after the source freeze. All destructive commands are fixed
to the new counterpart-gap-test profile/project; no Docker context is activated.
The private directory must be outside this repository. No OS trust is installed.
"""

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "counterpart-gap-test"
REPORT = ROOT / "research/gap_deployment_probe.json"
MARKER = "counterpart-private-https-probe-v1"
COMPOSE_VERSION = "v5.5.1"
BUILDX_VERSION = "v0.37.1"


def command(arguments, *, directory=None, input=None, timeout=120):
    environment = os.environ.copy()
    if directory:
        environment.update({
            "DOCKER_HOST": f"unix://{Path.home()}/.colima/{PROJECT}/docker.sock",
            "DOCKER_CONFIG": str(directory / "docker"),
        })
    result = subprocess.run(
        arguments, cwd=ROOT, env=environment, input=input, capture_output=True, timeout=timeout,
    )
    if result.returncode:
        # Never include subprocess argv/output: resolved Compose configuration
        # and backend diagnostics can contain disposable passwords or tokens.
        if directory:
            descriptor = os.open(
                directory / "last-operation-failure.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
            )
            with os.fdopen(descriptor, "wb") as output:
                output.write((result.stdout + b"\n" + result.stderr)[-65536:])
        raise RuntimeError(f"Private harness operation failed ({Path(arguments[0]).name}).")
    return result.stdout


def compose(directory, *arguments, timeout=120):
    return command([
        str(directory / "tools/docker-compose"), "--project-name", PROJECT,
        "--env-file", str(directory / "private/compose.env"),
        "-f", str(ROOT / "compose.yaml"), "-f", str(directory / "overlay.yaml"),
        *arguments,
    ], directory=directory, timeout=timeout)


def private_write(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data.encode() if isinstance(data, str) else data)


def validate_directory(directory, *, new=False):
    directory = directory.expanduser().resolve()
    if directory.is_relative_to(ROOT) or directory == Path.home() or directory == Path("/"):
        raise ValueError("Use a dedicated private directory outside the checkout.")
    if "\n" in str(directory) or "'" in str(directory):
        raise ValueError("Unsupported private directory characters.")
    if new:
        if directory.exists():
            raise ValueError("Preparation requires a new private directory.")
    elif (directory / "marker").read_text() != MARKER:
        raise ValueError("Directory does not contain this harness marker.")
    return directory


def download_checked(url, sha_url, destination):
    with urllib.request.urlopen(sha_url, timeout=30) as response:
        checksums = response.read(1024 * 1024).decode()
    with urllib.request.urlopen(url, timeout=90) as response:
        content = response.read(150 * 1024 * 1024)
    digest = hashlib.sha256(content).hexdigest()
    filename = url.rsplit("/", 1)[1]
    if not any(row.split()[0] == digest and (
        len(row.split()) == 1 or row.split()[-1].lstrip("*") == filename
    ) for row in checksums.splitlines() if row.strip()):
        raise RuntimeError("Official tool download checksum did not match.")
    private_write(destination, content)
    destination.chmod(0o700)
    return {"url": url, "sha256": digest}


def prepare(directory):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("This bounded reproduction is scoped to Apple Silicon Colima.")
    directory.mkdir(mode=0o700, parents=True)
    for part in ("private", "tools", "docker", "docker/cli-plugins"):
        (directory / part).mkdir(mode=0o700)
    private_write(directory / "marker", MARKER)
    command(["python3", str(ROOT / "deploy/prepare.py"), "--directory", str(directory / "private")])
    runtime = directory / "private/runtime.env"
    text = runtime.read_text().replace("COOKIE_SECURE=false", "COOKIE_SECURE=true")
    text = text.replace("OPERATOR_LOCAL_HTTP=true", "OPERATOR_LOCAL_HTTP=false")
    text = text.replace(
        '["localhost","127.0.0.1","api"]',
        '["counterpart.test","localhost","127.0.0.1","api"]',
    ).replace(
        '["http://localhost:8080","http://127.0.0.1:8080"]',
        '["https://counterpart.test:18443","https://127.0.0.1:18443"]',
    )
    runtime.write_text(text)
    password = (directory / "private/database-password").read_text().strip()
    private_write(directory / "private/postgres.env", f"POSTGRES_PASSWORD={password}\n")
    now = datetime.now(UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Counterpart disposable HTTPS probe")])
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=2))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("counterpart.test"), x509.IPAddress(ip_address("127.0.0.1")),
        ]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    private_write(directory / "private/tls.pem", certificate.public_bytes(serialization.Encoding.PEM))
    private_write(directory / "private/tls.key", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_write(directory / "private/spki", base64.b64encode(hashlib.sha256(spki).digest()))
    # Retain the product's actual reverse proxy, header and body-limit directives;
    # only the site addresses and explicit ephemeral certificate are changed.
    caddy_source = (ROOT / "deploy/Caddyfile").read_bytes()
    tls_source = (ROOT / "deploy/compose.tls.yaml").read_bytes()
    caddyfile = caddy_source.decode().replace(
        "{$COUNTERPART_DOMAIN} {",
        "https://counterpart.test:443, https://127.0.0.1:443 {\n\ttls /probe/tls.pem /probe/tls.key",
    )
    private_write(directory / "private/Caddyfile", caddyfile)
    caddy_image = yaml.safe_load(tls_source)["services"]["caddy"]["image"]
    # !override prevents the base 8080 port and host-path certificate mounts from
    # leaking into this no-host-mount VM. Compose reads env files client-side.
    private_write(directory / "overlay.yaml", f"""services:
  postgres:
    secrets: !reset []
    environment:
      POSTGRES_PASSWORD_FILE: !reset null
    env_file: [{json.dumps(str(directory / 'private/postgres.env'))}]
  api:
    ports: !override ["127.0.0.1:18080:8080"]
    environment:
      COUNTERPART_OPERATOR_COOKIE_SECURE: "true"
      COUNTERPART_OPERATOR_LOCAL_HTTP: "false"
  worker:
    environment:
      COUNTERPART_OPERATOR_COOKIE_SECURE: "true"
      COUNTERPART_OPERATOR_LOCAL_HTTP: "false"
  initialize:
    environment:
      COUNTERPART_OPERATOR_COOKIE_SECURE: "true"
      COUNTERPART_OPERATOR_LOCAL_HTTP: "false"
  caddy:
    image: {caddy_image}
    command: [caddy, run, --config, /probe/Caddyfile, --adapter, caddyfile]
    ports: ["127.0.0.1:18443:443"]
    volumes: [tls_probe:/probe:ro, caddy_data:/data, caddy_config:/config]
    networks: [backend]
    depends_on:
      api:
        condition: service_healthy
    security_opt: ["no-new-privileges:true"]
    restart: unless-stopped
volumes:
  tls_probe:
    external: true
    name: {PROJECT}_tls_probe
  caddy_data:
  caddy_config:
""")
    checksums = {}
    compose_url = f"https://github.com/docker/compose/releases/download/{COMPOSE_VERSION}/docker-compose-darwin-aarch64"
    checksums["compose"] = download_checked(
        compose_url, compose_url + ".sha256", directory / "tools/docker-compose",
    )
    buildx_base = f"https://github.com/docker/buildx/releases/download/{BUILDX_VERSION}"
    checksums["buildx"] = download_checked(
        f"{buildx_base}/buildx-{BUILDX_VERSION}.darwin-arm64", f"{buildx_base}/checksums-signed.txt",
        directory / "docker/cli-plugins/docker-buildx",
    )
    initial = {
        "run_id": uuid4().hex,
        "prepared_deployment_source": {
            "deploy/Caddyfile": hashlib.sha256(caddy_source).hexdigest(),
            "deploy/compose.tls.yaml": hashlib.sha256(tls_source).hexdigest(),
        },
        "docker_context": command(["docker", "context", "show"]).decode().strip(),
        "colima_profiles": [json.loads(row) for row in command(["colima", "list", "--json"]).splitlines()],
        "tools": checksums,
        "caddy_image": caddy_image,
    }
    private_write(directory / "initial.json", json.dumps(initial, indent=2))
    compose(directory, "config", "--quiet")
    print("Private bounded HTTPS harness prepared; no VM/build/provider calls started.")


def start(directory):
    initial = json.loads((directory / "initial.json").read_text())
    current = [json.loads(row) for row in command(["colima", "list", "--json"]).splitlines()]
    if any(item["name"] == PROJECT for item in initial["colima_profiles"] + current):
        raise RuntimeError("Refusing to take ownership of a pre-existing profile.")
    # Reserve this exact fresh profile directory atomically. Cleanup must find
    # the same owner marker before stopping/deleting a profile, including after
    # a partially failed start. A later unrelated same-name VM is never adopted.
    profile = Path.home() / ".colima" / PROJECT
    profile.mkdir(mode=0o700, exist_ok=False)
    private_write(profile / "counterpart-harness-owner", initial["run_id"])
    private_write(directory / "profile-claimed", initial["run_id"])
    command([
        "colima", "start", PROJECT, "--cpu", "2", "--memory", "2", "--disk", "8",
        "--root-disk", "6", "--mount", "none", "--ssh-agent=false", "--ssh-config=false",
        "--activate=false", "--runtime", "docker", "--vm-type", "vz", "--template=false",
        "--kubernetes=false", "--vz-rosetta=false",
    ], timeout=300)
    command(["docker", "volume", "create", f"{PROJECT}_tls_probe"], directory=directory)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for name in ("Caddyfile", "tls.pem", "tls.key"):
            data = (directory / "private" / name).read_bytes()
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), 0o600
            bundle.addfile(entry, io.BytesIO(data))
    command([
        "docker", "run", "--rm", "-i", "--user", "0", "--entrypoint", "sh",
        "-v", f"{PROJECT}_tls_probe:/probe", initial["caddy_image"],
        "-c", "umask 077; tar -xf - -C /probe",
    ], directory=directory, input=archive.getvalue(), timeout=180)
    assert command(["docker", "context", "show"]).decode().strip() == initial["docker_context"]
    print("Dedicated 2-CPU/2-GiB VM started without host mounts or context activation.")


def source_fingerprint():
    files = [ROOT / name for name in (
        "Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "compose.yaml",
        "deploy/Caddyfile", "deploy/compose.tls.yaml",
    )]
    files += sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "src").rglob("*.sql"))
    files += sorted(path for path in (ROOT / "web").rglob("*") if path.is_file() and not any(
        item in path.parts for item in ("node_modules", "dist", "test-results", "playwright-report")
    ))
    entries = [{"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in files]
    return {
        "sha256": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(), "files": entries,
    }


def build(directory):
    prepared = json.loads((directory / "initial.json").read_text())["prepared_deployment_source"]
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
           for name, digest in prepared.items()):
        raise RuntimeError("Deployment sources changed since preparation; refresh the private harness first.")
    before = source_fingerprint()
    compose(directory, "build", timeout=900)
    if source_fingerprint() != before:
        raise RuntimeError("Source changed during image build; rerun after freeze.")
    (directory / "built_source.json").write_text(json.dumps(before, indent=2))
    compose(directory, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120", timeout=180)
    print("Frozen locked image built; dedicated PostgreSQL/API/worker/HTTPS proxy started.")


def check(directory):
    before = json.loads((directory / "built_source.json").read_text())
    if source_fingerprint() != before:
        raise RuntimeError("Source no longer matches tested image; rebuild first.")
    spec = importlib.util.spec_from_file_location(
        "counterpart_deployment_probe", ROOT / "scripts/deployment_probe.py",
    )
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve annotations through the module registry.
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    target = module.DeploymentTarget(
        project=PROJECT, base_url="https://127.0.0.1:18443", ca_file=directory / "private/tls.pem",
        report_title="isolated local Colima Linux/arm64 HTTPS Docker Compose",
    )
    report = module.run(directory, REPORT, target=target)
    report["source_fingerprint"] = before
    report["source_worktree_dirty"] = bool(command(["git", "status", "--porcelain"]))
    report["tools"] = json.loads((directory / "initial.json").read_text())["tools"]
    report["tls"] = {
        "python_certificate_validation": True, "cookie_secure_httponly_samesite_strict": True,
        "python_verified_server_name": "counterpart.test",
        "cross_origin_login_refused": True, "host_os_trust_modified": False,
        "public_certificate_issuance": False,
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    command(["node", str(ROOT / "deploy/gap_probe_browser.mjs"), str(directory)], timeout=120)
    print("HTTPS recovery, immutable release, secure auth and fake-camera browser gates passed.")


def cleanup(directory):
    initial = json.loads((directory / "initial.json").read_text())
    if any(item["name"] == PROJECT for item in initial["colima_profiles"]):
        raise RuntimeError("Refusing to remove a pre-existing profile.")
    profile = Path.home() / ".colima" / PROJECT
    if profile.exists():
        marker = profile / "counterpart-harness-owner"
        if not marker.is_file() or marker.read_text() != initial["run_id"]:
            raise RuntimeError("Refusing to remove a profile not owned by this harness run.")
        # These operations may already have succeeded or never have started.
        # Continue toward VM deletion instead of leaving keys because a volume
        # or service is absent. Only fixed, owned resources are ever targeted.
        for operation in (
            lambda: compose(directory, "down", "--volumes", "--remove-orphans", timeout=180),
            lambda: command(["docker", "volume", "rm", f"{PROJECT}_tls_probe"], directory=directory),
            lambda: command(["colima", "stop", PROJECT], timeout=180),
        ):
            try:
                operation()
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
        command(["colima", "delete", PROJECT, "--force", "--data"], timeout=180)
    context_unchanged = command(["docker", "context", "show"]).decode().strip() == initial["docker_context"]
    profiles = [json.loads(row) for row in command(["colima", "list", "--json"]).splitlines()]
    profiles_unchanged = profiles == initial["colima_profiles"]
    try:
        native_status = httpx.get("http://127.0.0.1:8080/api/health", timeout=5, trust_env=False).status_code
    except httpx.TransportError:
        native_status = None
    report = json.loads(REPORT.read_text()) if REPORT.exists() else None
    shutil.rmtree(directory)
    if report is not None:
        report["cleanup"] = {
            "dedicated_vm_containers_volumes_removed": not profile.exists(),
            "docker_context_unchanged": context_unchanged,
            "existing_colima_profiles_unchanged": profiles_unchanged, "native_app_health": native_status,
            "private_directory_removed": True,
        }
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
    assert context_unchanged and profiles_unchanged, "Unrelated runtime state changed; investigate."
    assert native_status == 200, "Disposable cleanup completed; native health needs a separate check."
    print("Disposable VM, volumes and private files removed; native app and existing profiles unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "start", "build", "check", "cleanup"))
    parser.add_argument("--directory", type=Path, required=True)
    arguments = parser.parse_args()
    directory = validate_directory(arguments.directory, new=arguments.phase == "prepare")
    {"prepare": prepare, "start": start, "build": build, "check": check, "cleanup": cleanup}[
        arguments.phase
    ](directory)
