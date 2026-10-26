"""The shipped app accepts local callers, not unauthenticated remote exposure."""
from argparse import Namespace
import hashlib
import json
from pathlib import Path
import sys

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from zotero_summarizer.api import app as app_module
from zotero_summarizer.cli import build_parser
from zotero_summarizer.cli._app import _serve
from zotero_summarizer.settings import Settings


@pytest.fixture(autouse=True)
def loaded_source_receipt(tmp_path, request, record_property):
    root = Path(__file__).resolve().parents[1]
    receipt = {"case": request.node.nodeid, "modules": {}}
    for name in ("zotero_summarizer.api.app", "zotero_summarizer.cli._app",
                 "zotero_summarizer.api.local_access", "zotero_summarizer.services.setup.deployment"):
        module = sys.modules.get(name)
        if module is not None:
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(root), (name, path)
            receipt["modules"][name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    record_property("loaded_source", json.dumps(receipt, sort_keys=True))
    suffix = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
    (tmp_path / f"loaded-source-{suffix}.json").write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_FRONTEND_DIST", tmp_path / "no-build")
    app = app_module.create_app(Settings.load(project_root=tmp_path))
    calls = []

    @app.api_route("/api/security-probe", methods=["GET", "POST", "DELETE"])
    def probe():
        calls.append("called")
        return {"ok": True}

    @app.websocket("/api/security-socket")
    async def probe_socket(socket: WebSocket):
        await socket.accept()
        await socket.send_text("local")
        await socket.close()

    return app, calls


def local_client(app, *, peer=("127.0.0.1", 50000)):
    return TestClient(app, base_url="http://localhost:8000", client=peer)


@pytest.mark.parametrize("peer,host,origin", [
    (("127.0.0.1", 50000), "localhost:8000", "http://localhost:8000"),
    (("127.0.0.2", 50000), "127.0.0.2:8000", "http://127.0.0.2:8000"),
    (("::1", 50000), "[::1]:8000", "http://[::1]:8000"),
    (("::ffff:127.0.0.1", 50000), "localhost:8000", None),
])
def test_local_requests_reach_existing_app(local_app, peer, host, origin):
    app, calls = local_app
    headers = {"Host": host}
    if origin is not None:
        headers.update({"Origin": origin, "Sec-Fetch-Site": "same-origin"})
    response = local_client(app, peer=peer).post("/api/security-probe", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert calls == ["called"]


@pytest.mark.parametrize("peer", [("192.0.2.5", 50000), ("2001:db8::1", 50000),
                                  ("testclient", 50000), None])
def test_remote_or_unknown_peer_cannot_spoof_local_headers(local_app, peer):
    app, calls = local_app
    response = local_client(app, peer=peer).post("/api/security-probe", headers={
        "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Host": "localhost:8000",
        "X-Forwarded-Proto": "https", "Forwarded": "for=127.0.0.1;host=localhost",
        "X-Remote-User": "owner",
    })
    assert response.status_code == 403
    assert response.json()["error"] == "local_access_only"
    assert calls == []


@pytest.mark.parametrize("host", ["", "example.invalid", "localhost.evil.invalid", "0.0.0.0:8000",
                                  "192.0.2.5:8000", "localhost:bad", "localhost:8000/path",
                                  "user@localhost:8000", "[::1]suffix:8000", "localhost:8000,localhost"])
def test_untrusted_or_malformed_host_is_rejected(local_app, host):
    app, calls = local_app
    response = local_client(app).get("/api/security-probe", headers={"Host": host})
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("origin", ["null", "https://example.invalid", "http://localhost:9000",
                                    "https://localhost:8000", "http://127.0.0.1:8000",
                                    "http://localhost:8000/path", "http://user@localhost:8000"])
def test_cross_origin_request_cannot_mutate(local_app, origin):
    app, calls = local_app
    response = local_client(app).post("/api/security-probe", headers={"Origin": origin})
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("header,value", [("Host", "localhost:8000"), ("Origin", "http://localhost:8000")])
def test_duplicate_authority_headers_are_rejected(local_app, header, value):
    app, calls = local_app
    response = local_client(app).post("/api/security-probe", headers=[(header, value), (header, value)])
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("method,path", [("GET", "/"), ("GET", "/docs"), ("GET", "/assets/private.js"),
                                        ("POST", "/api/security-probe"), ("DELETE", "/api/security-probe")])
def test_cross_site_browser_is_denied_before_routes(local_app, method, path):
    app, calls = local_app
    response = local_client(app).request(method, path, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert calls == []


def test_forwarded_headers_do_not_change_a_local_request(local_app):
    app, calls = local_app
    response = local_client(app).get("/api/security-probe", headers={
        "X-Forwarded-For": "192.0.2.5", "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "example.invalid", "X-Remote-User": "someone",
    })
    assert response.status_code == 200
    assert calls == ["called"]


def test_local_websocket_and_cross_origin_rejection(local_app):
    app, _ = local_app
    with local_client(app).websocket_connect("ws://localhost:8000/api/security-socket", headers={"Origin": "http://localhost:8000"}) as socket:
        assert socket.receive_text() == "local"
    with pytest.raises(WebSocketDisconnect) as rejected:
        with local_client(app).websocket_connect("ws://localhost:8000/api/security-socket", headers={"Origin": "https://example.invalid"}):
            pytest.fail("cross-origin socket reached application")
    assert rejected.value.code == 1008


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.5", "example.invalid"])
def test_remote_bind_is_rejected_loudly(host, capsys):
    with pytest.raises(SystemExit) as rejected:
        build_parser().parse_args(["serve", "--host", host])
    assert rejected.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_programmatic_remote_bind_cannot_launch(monkeypatch):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: pytest.fail("unsupported bind launched"))
    with pytest.raises(ValueError, match="loopback"):
        _serve(Namespace(host="0.0.0.0", port=8000, reload=False))


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_supported_launcher_preserves_host_port_and_disables_proxy_headers(monkeypatch, tmp_path, host):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    args = build_parser().parse_args(["serve", "--host", host, "--port", "8765"])
    args.project_root = str(tmp_path)  # main() normally resolves and admits this root.
    monkeypatch.setenv("ZOTERO_SUMMARIZER_HOME", str(tmp_path))
    assert args.func(args) == 0
    assert calls == [(("zotero_summarizer.api.app:create_app",), {
        "factory": True, "host": host, "port": 8765, "reload": False, "proxy_headers": False,
    })]
