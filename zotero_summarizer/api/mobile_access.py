"""Explicit single-owner gateway over private Unix or consciously trusted localhost."""
from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


MOBILE_ACCESS_MESSAGE = "Authenticated owner access through the configured HTTPS gateway is required."


def _https_origin(value: str) -> str | None:
    if not value.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
    except ValueError:
        return None
    if url.scheme != "https" or not host or not host.endswith(".ts.net") or port not in (None, 443):
        return None
    labels = host.split(".")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
           or not all(char.isalnum() or char == "-" for char in label) for label in labels):
        return None
    authority = host + (":443" if port is not None else "")
    if url.netloc.lower() != authority or value.lower() != "https://" + authority:
        return None
    return "https://" + host


@dataclass(frozen=True)
class MobileAccessPolicy:
    origin: str
    owner_login: str
    socket_path: Path | None = None
    trusted_loopback_port: int | None = None

    def __post_init__(self) -> None:
        canonical = _https_origin(self.origin)
        if canonical is None:
            raise ValueError("Mobile origin must be an HTTPS Tailscale .ts.net origin without a path")
        if not self.owner_login or not self.owner_login.isascii() or any(
            ord(char) <= 32 or ord(char) == 127 for char in self.owner_login
        ):
            raise ValueError("An exact ASCII Tailscale owner login is required")
        if (self.socket_path is None) == (self.trusted_loopback_port is None):
            raise ValueError("Select exactly one private Unix socket or explicitly trusted localhost port")
        if self.socket_path is not None and not self.socket_path.is_absolute():
            raise ValueError("Mobile gateway requires an absolute private Unix socket path")
        if self.trusted_loopback_port is not None and (
            type(self.trusted_loopback_port) is not int or not 1024 <= self.trusted_loopback_port <= 65535
        ):
            raise ValueError("Trusted localhost port must be an integer in 1024..65535")
        object.__setattr__(self, "origin", canonical)

    def allows(self, scope: Scope) -> bool:
        peer = scope.get("client")
        if self.socket_path is not None:
            if peer is not None or scope.get("server") != (str(self.socket_path), None):
                return False
        elif not peer or peer[0] != "127.0.0.1" or scope.get("server") != ("127.0.0.1", self.trusted_loopback_port):
            return False
        if scope.get("scheme") not in {"http", "ws"}:
            return False
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        host_matches = (hosts == ["localhost"] if self.socket_path is not None else
                        len(hosts) == 1 and _https_origin("https://" + hosts[0]) == self.origin)
        if not host_matches or "tailscale-funnel-request" in headers:
            return False
        logins = headers.getlist("tailscale-user-login")
        if len(logins) != 1 or not logins[0].isascii() or not compare_digest(logins[0], self.owner_login):
            return False
        if headers.getlist("x-forwarded-proto") != ["https"]:
            return False
        hosts = headers.getlist("x-forwarded-host")
        if len(hosts) != 1 or _https_origin("https://" + hosts[0]) != self.origin:
            return False
        origins, sites = headers.getlist("origin"), headers.getlist("sec-fetch-site")
        return (len(origins) <= 1 and (not origins or _https_origin(origins[0]) == self.origin)
                and len(sites) <= 1 and (not sites or sites[0] in {"same-origin", "none"}))


class MobileAccessMiddleware:
    def __init__(self, app: ASGIApp, policy: MobileAccessPolicy) -> None:
        self.app, self.policy = app, policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if not self.policy.allows(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": MOBILE_ACCESS_MESSAGE})
            else:
                response = JSONResponse({"error": "mobile_access_denied", "message": MOBILE_ACCESS_MESSAGE,
                                         "details": {}}, status_code=403)
                await response(scope, receive, send)
            return
        authorized = dict(scope)
        authorized["scheme"] = "wss" if scope["type"] == "websocket" else "https"
        authorized["headers"] = [(key, value) for key, value in scope["headers"] if key.lower() != b"host"]
        authorized["headers"].append((b"host", self.policy.origin.removeprefix("https://").encode("ascii")))
        await self.app(authorized, receive, send)
