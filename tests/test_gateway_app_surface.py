"""Actual factory and fixture assets; transport adaptation is not real authentication."""
from pathlib import Path

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from zotero_summarizer.api import app as app_module
from zotero_summarizer.api.mobile_access import MobileAccessPolicy
from zotero_summarizer.settings import Settings
from test_mobile_access_boundary import _client, _headers, LOGIN, ORIGIN


@pytest.fixture
def surface(tmp_path, monkeypatch):
    dist = tmp_path / "fixture-build"
    (dist / "assets").mkdir(parents=True)
    for name, body in {
        "index.html": b"<html>fixture application</html>",
        "assets/fixture.js": b"window.fixture = true;",
        "sw.js": b"// fixture service worker",
        "manifest.webmanifest": b'{"name":"fixture"}',
        "app-icon.svg": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
    }.items():
        (dist / name).write_bytes(body)
    monkeypatch.setattr(app_module, "_FRONTEND_DIST", dist)
    policy = MobileAccessPolicy(ORIGIN, LOGIN, tmp_path / "api.sock")
    app = app_module.create_app(Settings.load(project_root=tmp_path), mobile_access=policy)
    calls = []

    @app.websocket("/gateway-socket")
    async def endpoint(socket: WebSocket):
        calls.append("websocket")
        await socket.accept()
        await socket.send_text(str(socket.url))
        await socket.close()

    return app, policy, calls


@pytest.mark.parametrize("path,expected", [
    ("/", b"<html>fixture application</html>"),
    ("/today", b"<html>fixture application</html>"),
    ("/assets/fixture.js", b"window.fixture = true;"),
    ("/sw.js", b"// fixture service worker"),
    ("/manifest.webmanifest", b'{"name":"fixture"}'),
    ("/app-icon.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
])
def test_owner_reaches_actual_factory_spa_assets_and_public_files(surface, path, expected):
    with _client(surface) as client:
        response = client.get(path, headers=_headers())
    assert response.status_code == 200
    assert response.content == expected


@pytest.mark.parametrize("path", ["/", "/today", "/assets/fixture.js", "/sw.js", "/manifest.webmanifest",
                                   "/app-icon.svg", "/docs", "/openapi.json", "/api/sync/status"])
def test_non_owner_cannot_read_any_surface(surface, path):
    headers = [(key, "other@example.net" if key == "Tailscale-User-Login" else value)
               for key, value in _headers()]
    with _client(surface) as client:
        response = client.get(path, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"] == "mobile_access_denied"
    assert b"fixture application" not in response.content


def test_owner_websocket_gets_only_configured_https_authority(surface):
    with _client(surface) as client:
        with client.websocket_connect("/gateway-socket", headers=dict(_headers())) as connection:
            assert connection.receive_text() == ORIGIN.replace("https://", "wss://") + "/gateway-socket"
    assert surface[2] == ["websocket"]


def test_non_owner_websocket_never_reaches_endpoint(surface):
    headers = {key: value for key, value in _headers()}
    headers["Tailscale-User-Login"] = "other@example.net"
    with _client(surface) as client:
        with pytest.raises(WebSocketDisconnect) as failure:
            with client.websocket_connect("/gateway-socket", headers=headers):
                pytest.fail("non-owner reached websocket")
    assert failure.value.code == 1008
    assert surface[2] == []


@pytest.mark.parametrize("authority", ["READER.EXAMPLE-TAILNET.TS.NET", "reader.example-tailnet.ts.net:443"])
def test_canonical_case_and_default_https_port_are_equivalent(surface, authority):
    headers = [(key, authority if key == "X-Forwarded-Host" else "https://" + authority if key == "Origin" else value)
               for key, value in _headers()]
    with _client(surface) as client:
        assert client.get("/", headers=headers).status_code == 200


@pytest.mark.parametrize("authority", [
    "reader.example-tailnet.ts.net:0443", "reader.example-tailnet.ts.net:bad",
    "reader.example-tailnet.ts.net/", "reader.example-tailnet.ts.net?", "reader.example-tailnet.ts.net#",
    "user@reader.example-tailnet.ts.net", "reader.example-tailnet.ts.net.evil.invalid",
    "reader.example-tailnet.ts.net.", "reader.example-tailnet.ts.net,evil.ts.net",
    "reader.example-tailnet.ts.net\\evil", "reader.example-tailnet.ts.net:8443",
])
def test_bad_authority_has_no_asset_access(surface, authority):
    headers = [(key, authority if key == "X-Forwarded-Host" else value) for key, value in _headers()]
    with _client(surface) as client:
        assert client.get("/assets/fixture.js", headers=headers).status_code == 403


@pytest.mark.parametrize("owner", ["OWNER@example.net", " owner@example.net", "owner@example.net ", "*@example.net"])
def test_owner_match_is_exact_not_casefolded_trimmed_or_domain_wide(surface, owner):
    headers = [(key, owner if key == "Tailscale-User-Login" else value) for key, value in _headers()]
    with _client(surface) as client:
        assert client.get("/", headers=headers).status_code == 403


def test_explicit_policy_is_canonical_without_mutating_caller_path(tmp_path):
    path = tmp_path / "api.sock"
    policy = MobileAccessPolicy("HTTPS://READER.EXAMPLE-TAILNET.TS.NET:443", LOGIN, path)
    assert policy.origin == ORIGIN and policy.socket_path == path
    with pytest.raises(ValueError):
        MobileAccessPolicy(ORIGIN, LOGIN, Path("relative.sock"))
