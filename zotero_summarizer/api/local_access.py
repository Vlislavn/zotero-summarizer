"""Reject unsupported inbound exposure before any API, SPA or socket handler."""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from zotero_summarizer.services.setup.deployment import (
    LOCAL_ONLY_MESSAGE,
    is_loopback_address,
    is_loopback_host,
)


def _local_origin(value: str) -> tuple[str, str, int] | None:
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
    except ValueError:
        return None
    if url.scheme not in {"http", "https"} or not host or not is_loopback_host(host):
        return None
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    if url.netloc.lower() != authority or value.lower() != f"{url.scheme}://{authority}":
        return None
    return url.scheme, host, port if port is not None else (443 if url.scheme == "https" else 80)


def _allows_local_request(scope: Scope) -> bool:
    peer = scope.get("client")
    if not peer or not is_loopback_address(peer[0]):
        return False
    headers = Headers(scope=scope)
    hosts, origins = headers.getlist("host"), headers.getlist("origin")
    sites = headers.getlist("sec-fetch-site")
    if len(hosts) != 1 or len(origins) > 1 or len(sites) > 1:
        return False
    if sites and sites[0] not in {"same-origin", "none"}:
        return False
    scheme = {"ws": "http", "wss": "https"}.get(scope["scheme"], scope["scheme"])
    target = _local_origin(f"{scheme}://{hosts[0]}")
    return target is not None and (not origins or _local_origin(origins[0]) == target)


class LocalAccessMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or _allows_local_request(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": LOCAL_ONLY_MESSAGE})
            return
        response = JSONResponse(
            {"error": "local_access_only", "message": LOCAL_ONLY_MESSAGE, "details": {}},
            status_code=403,
        )
        await response(scope, receive, send)
