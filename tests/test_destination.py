"""No remote requests: exercise actual resolver via bounded mock HTTP streams."""

import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from counterpart.config import Settings
from counterpart.destination import DestinationError, DestinationResolver


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOFLOW_API_KEY", "destination-test-secret+value")
    monkeypatch.setenv("ROBOFLOW_WORKSPACE", "test-workspace")
    config = Settings(
        export_enabled=True, export_project="test-project", credentials_file=tmp_path / "absent"
    )
    state = SimpleNamespace(config=config, calls=[], clock=100.0)
    monkeypatch.setattr("counterpart.destination.time.monotonic", lambda: state.clock)
    state.handler = lambda _: httpx.Response(
        200, json={"project": {"id": "test-workspace/test-project", "public": False}}
    )

    def transport(request):
        state.calls.append(request)
        return state.handler(request)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        state.resolver = DestinationResolver(config, client=client)
        yield state


def test_checks_authenticated_official_schema_and_returns_only_destination(rig):
    result = rig.resolver.resolve()
    assert result == {"workspace": "test-workspace", "project": "test-project", "visibility": "private"}
    assert len(rig.calls) == 1
    request = rig.calls[0]
    assert request.method == "GET"
    assert request.url.host == "api.roboflow.com"
    assert request.url.path == "/test-workspace/test-project"
    assert request.url.params["api_key"] == "destination-test-secret+value"
    assert not request.content


@pytest.mark.parametrize("privacy", [True, None, 0, "false", "private", {}, []])
def test_only_explicit_false_proves_privacy(rig, privacy):
    rig.handler = lambda _: httpx.Response(
        200, json={"project": {"id": "test-workspace/test-project", "public": privacy}}
    )
    with pytest.raises(DestinationError, match="^private_destination_required$"):
        rig.resolver.resolve()


@pytest.mark.parametrize(
    "info",
    [
        {"id": "test-workspace/test-project", "license": "private"},
        {"id": "test-workspace/test-project", "public": False, "visibility": "public"},
        {"id": "test-workspace/test-project", "public": False, "private": False},
    ],
)
def test_missing_or_conflicting_flags_fail_closed(rig, info):
    rig.handler = lambda _: httpx.Response(200, json={"project": info})
    with pytest.raises(DestinationError, match="^private_destination_required$"):
        rig.resolver.resolve()


@pytest.mark.parametrize("identity", [None, "test-project", "other/test-project", "test-workspace/other"])
def test_identity_cannot_be_redirected_by_provider_payload(rig, identity):
    rig.handler = lambda _: httpx.Response(200, json={"project": {"id": identity, "public": False}})
    with pytest.raises(DestinationError, match="^destination_identity_mismatch$"):
        rig.resolver.resolve()


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "destination_auth_failed", False),
        (403, "destination_auth_failed", False),
        (404, "destination_not_found", False),
        (302, "destination_redirect_rejected", False),
        (429, "destination_unavailable", True),
        (503, "destination_unavailable", True),
    ],
)
def test_errors_do_not_leak_url_body_or_key_and_never_follow_redirects(rig, status, code, retryable):
    rig.handler = lambda _: httpx.Response(
        status, json={"error": "destination-test-secret+value"}, headers={"location": "https://evil.invalid"}
    )
    with pytest.raises(DestinationError) as caught:
        rig.resolver.resolve()
    assert str(caught.value) == code
    assert caught.value.retryable is retryable
    assert len(rig.calls) == 1


def test_timeout_is_sanitized_and_retryable(rig):
    def timeout(request):
        raise httpx.ReadTimeout("Secret URL: " + str(request.url), request=request)

    rig.handler = timeout
    with pytest.raises(DestinationError) as caught:
        rig.resolver.resolve()
    assert str(caught.value) == "destination_unavailable"
    assert caught.value.retryable
    assert len(rig.calls) == 1


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", b"null", b'{"project": []}', b"x" * (2 * 1024 * 1024 + 1)]
)
def test_malformed_and_oversized_responses_fail_closed(rig, body):
    rig.handler = lambda _: httpx.Response(200, content=body)
    with pytest.raises(DestinationError):
        rig.resolver.resolve()


def test_cache_is_bounded_detached_and_expires(rig):
    first = rig.resolver.resolve()
    first["workspace"] = "tampered"
    assert rig.resolver.resolve()["workspace"] == "test-workspace"
    assert len(rig.calls) == 1
    rig.clock += 29
    rig.resolver.resolve()
    assert len(rig.calls) == 1
    rig.clock += 1
    rig.resolver.resolve()
    assert len(rig.calls) == 2
    rig.resolver.resolve(force=True)
    assert len(rig.calls) == 3


def test_failed_forced_refresh_destroys_previously_private_success(rig):
    rig.resolver.resolve()
    rig.handler = lambda _: httpx.Response(503)
    with pytest.raises(DestinationError):
        rig.resolver.resolve(force=True)
    with pytest.raises(DestinationError):
        rig.resolver.resolve()
    assert len(rig.calls) == 2
    rig.clock += 5
    with pytest.raises(DestinationError):
        rig.resolver.resolve()
    assert len(rig.calls) == 3


def test_parallel_reads_coalesce_one_metadata_request(rig):
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: rig.resolver.resolve(), range(16)))
    assert all(v["visibility"] == "private" for v in values)
    assert len(rig.calls) == 1


def test_configuration_or_credential_changes_invalidate_cache(rig, monkeypatch):
    rig.resolver.resolve()
    monkeypatch.setenv("ROBOFLOW_API_KEY", "rotated-test-key")
    rig.resolver.resolve()
    assert len(rig.calls) == 2
    assert rig.calls[-1].url.params["api_key"] == "rotated-test-key"
    rig.config.export_project = "another-project"
    with pytest.raises(DestinationError, match="^destination_identity_mismatch$"):
        rig.resolver.resolve()
    assert len(rig.calls) == 3


@pytest.mark.parametrize("invalid", ["disabled", "project", "workspace", "key", "invalid_slug"])
def test_unconfigured_or_disabled_does_not_dispatch(rig, monkeypatch, invalid):
    if invalid == "disabled":
        rig.config.export_enabled = False
    elif invalid == "project":
        rig.config.export_project = ""
    elif invalid == "invalid_slug":
        rig.config.export_project = "../other?api_key=unsafe"
    else:
        monkeypatch.setenv("ROBOFLOW_WORKSPACE" if invalid == "workspace" else "ROBOFLOW_API_KEY", "")
    with pytest.raises(DestinationError):
        rig.resolver.resolve()
    assert not rig.calls


def test_httpx_query_credential_is_redacted_from_log_records(rig, caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        rig.resolver.resolve()
    assert "destination-test-secret" not in caplog.text
    assert "[REDACTED]" in caplog.text
