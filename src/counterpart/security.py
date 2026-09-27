"""Small single-operator authentication boundary, distinct from collector capabilities."""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from .config import Settings

COOKIE = "counterpart_operator"


class OperatorAuth:
    def __init__(self, config: Settings):
        self.config = config
        token = config.operator_token.get_secret_value()
        if config.require_operator and len(token) < 32:
            raise ValueError("Public mode requires a server-only operator token of at least 32 characters.")
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        loopback_http = (
            config.operator_local_http
            and bool(config.allowed_origins)
            and all(urlsplit(origin).hostname in local_hosts for origin in config.allowed_origins)
            and set(config.trusted_hosts) <= local_hosts | {"api"}
        )
        if config.operator_local_http and not loopback_http:
            raise ValueError("The local HTTP exception requires only loopback origins and hosts.")
        if config.require_operator and not config.operator_cookie_secure and not loopback_http:
            raise ValueError("Public mode requires secure operator cookies behind HTTPS.")
        if (
            config.require_operator
            and any(not origin.startswith("https://") for origin in config.allowed_origins)
            and not loopback_http
        ):
            raise ValueError("Public mode requires HTTPS allowed origins.")
        # Local ephemeral key grants no public bootstrap and intentionally expires across API restarts.
        self.key = (token or secrets.token_urlsafe(48)).encode()
        self._attempts = defaultdict(deque)
        self._lock = Lock()

    def issue(self) -> str:
        payload = f"{int(time.time()) + self.config.operator_cookie_ttl}.{secrets.token_hex(16)}"
        signature = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def valid(self, value: str | None) -> bool:
        if not value or len(value) > 200:
            return False
        try:
            expires, nonce, signature = value.split(".")
            if not 0 < int(expires) - time.time() <= self.config.operator_cookie_ttl + 5:
                return False
            expected = hmac.new(self.key, f"{expires}.{nonce}".encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    def require(self, request: Request):
        if not self.valid(request.cookies.get(COOKIE)):
            raise HTTPException(
                401, detail={"code": "operator_required", "message": "Sign in to the workspace."}
            )
        return "workspace-operator"

    def local(self, request: Request) -> bool:
        return (
            not self.config.require_operator
            and bool(request.client)
            and request.client.host
            in {
                "127.0.0.1",
                "::1",
                "testclient",
            }
        )

    def verify_login(self, token: str, request: Request):
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            for key in list(self._attempts):
                if not self._attempts[key] or now - self._attempts[key][-1] > 60:
                    del self._attempts[key]
            if client not in self._attempts and len(self._attempts) >= 1024:
                raise HTTPException(
                    429, detail={"code": "login_limited", "message": "Wait a minute before retrying."}
                )
            attempts = self._attempts[client]
            while attempts and now - attempts[0] > 60:
                attempts.popleft()
            if len(attempts) >= 5:
                raise HTTPException(
                    429, detail={"code": "login_limited", "message": "Wait a minute before retrying."}
                )
            attempts.append(now)
        expected = self.config.operator_token.get_secret_value()
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise HTTPException(
                401, detail={"code": "invalid_login", "message": "The workspace token is incorrect."}
            )

    def response(self):
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            COOKIE,
            self.issue(),
            max_age=self.config.operator_cookie_ttl,
            httponly=True,
            secure=self.config.operator_cookie_secure,
            samesite="strict",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response


class OriginGuard:
    def __init__(self, app, allowed_origins: list[str]):
        self.app = app
        self.allowed = set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = headers.get(b"origin", b"").decode(errors="replace")
            site = headers.get(b"sec-fetch-site", b"").decode(errors="replace")
            if (origin and origin not in self.allowed) or site == "cross-site":
                return await JSONResponse(
                    {
                        "detail": {
                            "code": "origin_rejected",
                            "message": "Use the configured Counterpart address.",
                        }
                    },
                    status_code=403,
                )(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                values = list(message.get("headers", []))
                values += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"permissions-policy", b"camera=(self), microphone=()"),
                ]
                if scope.get("path", "").startswith("/api/") and not any(
                    k.lower() == b"cache-control" for k, _ in values
                ):
                    values.append((b"cache-control", b"no-store"))
                message = {**message, "headers": values}
            await send(message)

        await self.app(scope, receive, secured_send)
