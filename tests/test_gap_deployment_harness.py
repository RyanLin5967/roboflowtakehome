"""Destructive-scope guards for the disposable HTTPS gate; no VM is started."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(tmp_path, monkeypatch):
    module = load_module("gap_harness_test", "deploy/gap_probe.py")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(module, "REPORT", tmp_path / "report.json")
    directory = tmp_path / "private"
    directory.mkdir()
    (directory / "initial.json").write_text(json.dumps({
        "run_id": "owned-run", "docker_context": "original-context",
        "colima_profiles": [{"name": "dbprobe", "status": "Stopped"}],
    }))
    (directory / "marker").write_text(module.MARKER)
    (directory / "disposable-private-key").write_text("must be removed after cleanup")
    return module, directory, home


def test_new_profile_appearing_after_prepare_is_never_adopted(harness, monkeypatch):
    module, directory, _ = harness
    calls = []

    def command(arguments, **kwargs):
        calls.append(arguments)
        assert arguments == ["colima", "list", "--json"]
        return json.dumps({"name": module.PROJECT, "status": "Stopped"}).encode()

    monkeypatch.setattr(module, "command", command)
    with pytest.raises(RuntimeError, match="pre-existing profile"):
        module.start(directory)
    assert len(calls) == 1


def test_existing_profile_directory_is_not_claimed_even_if_not_listed(harness, monkeypatch):
    module, directory, home = harness
    profile = home / ".colima" / module.PROJECT
    profile.mkdir(parents=True)
    (profile / "unrelated-data").write_text("preserve")
    monkeypatch.setattr(module, "command", lambda *args, **kwargs: b"")
    with pytest.raises(FileExistsError):
        module.start(directory)
    assert (profile / "unrelated-data").read_text() == "preserve"
    assert not (profile / "counterpart-harness-owner").exists()


def test_cleanup_refuses_unowned_same_name_profile(harness, monkeypatch):
    module, directory, home = harness
    profile = home / ".colima" / module.PROJECT
    profile.mkdir(parents=True)
    (profile / "counterpart-harness-owner").write_text("another-run")
    monkeypatch.setattr(module, "command", lambda *args, **kwargs: pytest.fail("No runtime action allowed"))
    with pytest.raises(RuntimeError, match="not owned"):
        module.cleanup(directory)
    assert directory.exists() and profile.exists()


def test_cleanup_finishes_after_missing_resources_and_reports_native_health_separately(harness, monkeypatch):
    module, directory, home = harness
    profile = home / ".colima" / module.PROJECT
    profile.mkdir(parents=True)
    (profile / "counterpart-harness-owner").write_text("owned-run")
    module.REPORT.write_text("{}")
    operations = []

    def command(arguments, **kwargs):
        operations.append(arguments)
        if arguments[:3] == ["docker", "volume", "rm"] or arguments[:2] == ["colima", "stop"]:
            raise RuntimeError("Already absent")
        if arguments[:2] == ["colima", "delete"]:
            assert arguments[2] == module.PROJECT
            shutil.rmtree(profile)
            return b""
        if arguments == ["docker", "context", "show"]:
            return b"original-context\n"
        if arguments == ["colima", "list", "--json"]:
            return b'{"name":"dbprobe","status":"Stopped"}\n'
        pytest.fail("Unexpected runtime operation")

    def already_absent(*args, **kwargs):
        raise RuntimeError("Stack already absent")

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("Native app unavailable")

    monkeypatch.setattr(module, "command", command)
    monkeypatch.setattr(module, "compose", already_absent)
    monkeypatch.setattr(module.httpx, "get", unavailable)
    with pytest.raises(AssertionError, match="native health"):
        module.cleanup(directory)
    assert not directory.exists() and not profile.exists()
    assert any(arguments[:2] == ["colima", "delete"] for arguments in operations)
    result = json.loads(module.REPORT.read_text())["cleanup"]
    assert result["dedicated_vm_containers_volumes_removed"]
    assert result["private_directory_removed"] and result["native_app_health"] is None
    assert result["existing_colima_profiles_unchanged"]


@pytest.mark.parametrize("values", [
    {"project": "production"},
    {"base_url": "https://production.example"},
    {"project": "counterpart-gap-test", "base_url": "https://127.0.0.1:18443"},
])
def test_probe_rejects_non_disposable_targets_and_untrusted_https(values):
    module = load_module("deployment_target_test", "scripts/deployment_probe.py")
    with pytest.raises(ValueError):
        module.DeploymentTarget(**values)
