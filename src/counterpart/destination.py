"""Authenticated, bounded checks for the configured Roboflow destination.

Roboflow's official Python SDK reads ``project.id`` and ``project.public`` from
GET /{workspace}/{project}. We require an exact identity and the boolean False;
a missing flag, a license string or a browser assertion never proves privacy.
Sources: github.com/roboflow/roboflow-python, core/workspace.py and core/project.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time

import httpx

from .config import Settings
from .providers import _CredentialFilter

_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class DestinationError(RuntimeError):
    """Only an allowlisted code survives; no response, URL or credential is retained."""

    def __init__(self, code: str, *, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


class DestinationResolver:
    """One configured destination, one cache entry and no automatic HTTP retries.

    The UI may use the short cache. Export admission and each new image upload force
    a fresh check. The provider has no atomic privacy-and-upload operation, so an
    administrator changing project visibility during an upload remains a race.
    """

    def __init__(self, config: Settings, *, client: httpx.Client | None = None, ttl_seconds: float = 30):
        if not math.isfinite(ttl_seconds) or not 0 <= ttl_seconds <= 300:
            raise ValueError("Destination cache lifetime must be between zero and 300 seconds")
        self.config = config
        self.ttl_seconds = ttl_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(trust_env=False, headers={"Accept-Encoding": "identity"})
        self._lock = threading.Lock()
        self._entry: tuple[str, float, dict | DestinationError] | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _configuration(self) -> tuple[str, str, str]:
        if not self.config.export_enabled:
            raise DestinationError("export_disabled")
        project = self.config.export_project
        if not project:
            raise DestinationError("export_not_configured")
        try:
            credentials = self.config.credentials()
            workspace = credentials["ROBOFLOW_WORKSPACE"].get_secret_value()
            key = credentials["ROBOFLOW_API_KEY"].get_secret_value()
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            raise DestinationError("invalid_destination_configuration") from None
        if (
            not _SLUG.fullmatch(project)
            or not _SLUG.fullmatch(workspace)
            or not key
            or any(ord(c) < 32 for c in key)
        ):
            raise DestinationError("invalid_destination_configuration")
        return workspace, project, key

    def resolve(self, *, force: bool = False) -> dict:
        workspace, project, key = self._configuration()
        identity = hashlib.sha256(json.dumps([workspace, project, key]).encode()).hexdigest()
        with self._lock:
            now = time.monotonic()
            if not force and self._entry and self._entry[0] == identity and self._entry[1] > now:
                outcome = self._entry[2]
                if isinstance(outcome, DestinationError):
                    raise DestinationError(outcome.code, retryable=outcome.retryable)
                return dict(outcome)
            # Invalidate before attempting a refresh: a failed read cannot leave
            # a formerly private destination usable through stale cached success.
            self._entry = None
            try:
                result = self._read(workspace, project, key)
            except DestinationError as exc:
                self._entry = (
                    identity,
                    time.monotonic() + min(5, self.ttl_seconds),
                    DestinationError(exc.code, retryable=exc.retryable),
                )
                raise
            self._entry = (identity, time.monotonic() + self.ttl_seconds, result)
            return dict(result)

    def _read(self, workspace: str, project: str, key: str) -> dict:
        credential_filter = _CredentialFilter(key)
        loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
        for logger in loggers:
            logger.addFilter(credential_filter)
        try:
            try:
                with self._client.stream(
                    "GET",
                    f"https://api.roboflow.com/{workspace}/{project}",
                    params={"api_key": key},
                    timeout=8,
                    follow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status != 200:
                        if status in (401, 403):
                            code = "destination_auth_failed"
                        elif status == 404:
                            code = "destination_not_found"
                        elif 300 <= status < 400:
                            code = "destination_redirect_rejected"
                        else:
                            code = "destination_unavailable"
                        raise DestinationError(code, retryable=status in (408, 429) or status >= 500)
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16_384):
                        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise DestinationError("invalid_destination_response")
                        body.extend(chunk)
            except httpx.HTTPError:
                raise DestinationError("destination_unavailable", retryable=True) from None
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise DestinationError("invalid_destination_response") from None
            info = payload.get("project") if isinstance(payload, dict) else None
            if not isinstance(info, dict) or info.get("id") != f"{workspace}/{project}":
                raise DestinationError("destination_identity_mismatch")
            if info.get("public") is not False:
                raise DestinationError("private_destination_required")
            # Treat any extra explicit, conflicting privacy indicators as unsafe.
            if ("visibility" in info and info["visibility"] != "private") or (
                "private" in info and info["private"] is not True
            ):
                raise DestinationError("private_destination_required")
            return {"workspace": workspace, "project": project, "visibility": "private"}
        finally:
            for logger in loggers:
                logger.removeFilter(credential_filter)
