"""Synthetic authenticated-gateway contracts; not live Tailscale/iOS acceptance."""
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
import os
import socket
import stat
import tempfile

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from zotero_summarizer.api import app as app_module
from zotero_summarizer.api.mobile_access import MobileAccessPolicy
from zotero_summarizer.cli import build_parser
from zotero_summarizer.cli._mobile_serve import _serve_mobile, private_listener
from zotero_summarizer.settings import Settings


ORIGIN = "https://reader.example-tailnet.ts.net"
LOGIN = "owner@example.net"


def _headers():
    return [("Host", "localhost"), ("Tailscale-User-Login", LOGIN),
            ("X-Forwarded-Host", ORIGIN.removeprefix("https://")),
            ("X-Forwarded-Proto", "https"), ("Origin", ORIGIN), ("Sec-Fetch-Site", "same-origin")]


@pytest.fixture
def mobile(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_FRONTEND_DIST", tmp_path / "no-build")
    path = tmp_path / "mobile.sock"
    policy = MobileAccessPolicy(ORIGIN, LOGIN, path)
    app = app_module.create_app(Settings.load(project_root=tmp_path), mobile_access=policy)
    calls = []

    @app.api_route("/api/probe", methods=["GET", "POST", "DELETE"])
    def probe(request: Request):
        calls.append("called")
        return {"base_url": str(request.base_url)}

    @app.get("/trailing/")
    def trailing():
        return {"ok": True}

    return app, policy, calls


@contextmanager
def _client(mobile, *, peer=None, server="uds"):
    app, policy, _calls = mobile

    async def synthetic_transport(scope, receive, send):
        adapted = dict(scope)
        if scope["type"] in {"http", "websocket"}:
            adapted.update(client=peer, server=(str(policy.socket_path), None) if server == "uds" else server)
        await app(adapted, receive, send)

    client = TestClient(synthetic_transport, base_url="http://localhost", follow_redirects=False)
    try:
        yield client
    finally:
        client.close()


def test_explicit_owner_gateway_uses_canonical_https_for_routes_and_redirects(mobile):
    with _client(mobile) as client:
        response = client.post("/api/probe", headers=_headers())
        assert response.status_code == 200
        assert response.json() == {"base_url": ORIGIN + "/"}
        redirected = client.get("/trailing", headers=_headers())
        assert redirected.status_code == 307
        assert redirected.headers["location"] == ORIGIN + "/trailing/"
    assert mobile[2] == ["called"]


@pytest.mark.parametrize("header,value", [
    ("Tailscale-User-Login", "other@example.net"), ("Tailscale-User-Login", ""),
    ("X-Forwarded-Host", "evil.ts.net"), ("X-Forwarded-Host", "reader.example-tailnet.ts.net.evil.invalid"),
    ("X-Forwarded-Proto", "http"), ("Origin", "null"), ("Origin", "https://evil.invalid"),
    ("Sec-Fetch-Site", "cross-site"), ("Host", "reader.example-tailnet.ts.net"),
    ("Tailscale-Funnel-Request", "?1"),
])
def test_forged_or_cross_site_gateway_metadata_never_reaches_handler(mobile, header, value):
    headers = [(key, val) for key, val in _headers() if key.lower() != header.lower()]
    headers.append((header, value))
    with _client(mobile) as client:
        response = client.post("/api/probe", headers=headers)
    assert response.status_code == 403 and response.json()["error"] == "mobile_access_denied"
    assert "other@example.net" not in response.text
    assert mobile[2] == []


@pytest.mark.parametrize("header", ["Host", "Tailscale-User-Login", "X-Forwarded-Host", "X-Forwarded-Proto", "Origin", "Sec-Fetch-Site"])
def test_duplicate_security_headers_fail_closed(mobile, header):
    headers = _headers()
    headers.append(next((key, value) for key, value in headers if key == header))
    with _client(mobile) as client:
        assert client.post("/api/probe", headers=headers).status_code == 403
    assert mobile[2] == []


@pytest.mark.parametrize("peer,server", [
    (("127.0.0.1", 5000), "uds"), (("192.0.2.1", 5000), "uds"),
    (None, ("127.0.0.1", 8000)), (None, None), (None, ("/different/socket", None)),
])
def test_even_correct_identity_headers_do_not_authorize_tcp_or_unknown_transport(mobile, peer, server):
    with _client(mobile, peer=peer, server=server) as client:
        assert client.post("/api/probe", headers=_headers()).status_code == 403
    assert mobile[2] == []


@pytest.mark.parametrize("path", ["/", "/docs", "/assets/private.js", "/api/sync/status", "/api/probe"])
def test_anonymous_rejection_covers_whole_app_not_only_sync(mobile, path):
    headers = [(key, value) for key, value in _headers() if key != "Tailscale-User-Login"]
    with _client(mobile) as client:
        assert client.get(path, headers=headers).status_code == 403
    assert mobile[2] == []


def test_websocket_is_denied_before_app_when_identity_missing(mobile):
    from starlette.websockets import WebSocketDisconnect

    with _client(mobile) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("/api/probe", headers={"Host": "localhost"}):
                pytest.fail("anonymous socket reached app")
    assert rejected.value.code == 1008


@pytest.mark.parametrize("origin", ["http://reader.tail.ts.net", "https://example.invalid", "https://reader.tail.ts.net/",
                                    "https://user@reader.tail.ts.net", "https://reader.tail.ts.net?x=1", "https://reader.tail.ts.net:8443"])
def test_invalid_gateway_origin_is_not_a_policy(tmp_path, origin):
    with pytest.raises(ValueError):
        MobileAccessPolicy(origin, LOGIN, tmp_path / "api.sock")


def test_default_app_still_rejects_gateway_headers_and_unknown_peer(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_FRONTEND_DIST", tmp_path / "no-build")
    app = app_module.create_app(Settings.load(project_root=tmp_path))
    client = TestClient(app, base_url="http://localhost", client=None)
    try:
        response = client.get("/docs", headers=_headers())
    finally:
        client.close()
    assert response.status_code == 403 and response.json()["error"] == "local_access_only"


def test_real_private_socket_has_installed_uvicorn_transport_identity_and_owned_cleanup():
    from uvicorn.protocols.utils import get_local_addr, get_remote_addr

    with tempfile.TemporaryDirectory(prefix="zs-mobile-", dir="/tmp") as directory:
        settings = Settings.load(project_root=Path(directory))
        with private_listener(settings) as (listener, path, _release):
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert path.stat().st_uid == os.getuid()
            listener.listen(1)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(path))
                connection, _address = listener.accept()
                with connection:
                    class Transport:
                        def get_extra_info(self, name):
                            return connection if name == "socket" else None
                    assert get_remote_addr(Transport()) is None
                    assert get_local_addr(Transport()) == (str(path), None)
        assert not path.exists()


def test_existing_socket_path_is_preserved_not_unlinked_or_overwritten():
    with tempfile.TemporaryDirectory(prefix="zs-mobile-", dir="/tmp") as directory:
        settings = Settings.load(project_root=Path(directory))
        parent = settings.data_dir / "mobile"
        parent.mkdir(parents=True, mode=0o700)
        path = parent / "api.sock"
        path.write_bytes(b"foreign marker")
        with pytest.raises(OSError):
            with private_listener(settings):
                pytest.fail("foreign path was replaced")
        assert path.read_bytes() == b"foreign marker"


def test_mobile_launcher_passes_only_prebound_socket_with_proxy_processing_disabled(monkeypatch):
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="zs-mobile-", dir="/tmp") as directory:
        observed = {}

        class Server:
            started = True
            should_exit = False

            def __init__(self, config):
                observed["config"] = config

            @contextmanager
            def capture_signals(self):
                yield

            def run(self, *, sockets):
                observed["sockets"] = sockets
                observed["path"] = Path(sockets[0].getsockname())
                assert stat.S_IMODE(observed["path"].stat().st_mode) == 0o600

        monkeypatch.setattr(uvicorn, "Server", Server)
        args = Namespace(project_root=directory, origin=ORIGIN, owner_login=LOGIN)
        assert _serve_mobile(args) == 0
        assert not observed["path"].exists()
        assert observed["config"].proxy_headers is False
        assert observed["config"].workers == 1
        assert observed["config"].access_log is False
        assert observed["sockets"][0].fileno() == -1


def test_mobile_launcher_is_explicit_and_never_reconfigures_tailscale():
    parsed = build_parser().parse_args(["serve-mobile", "--origin", ORIGIN, "--owner-login", LOGIN])
    assert parsed.func is _serve_mobile
    assert parsed.origin == ORIGIN and parsed.owner_login == LOGIN
