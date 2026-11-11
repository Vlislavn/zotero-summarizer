"""Actual Uvicorn/Unix HTTP and WebSocket, using explicit fixture identities only."""
import base64
import json
from secrets import token_bytes
import signal
from pathlib import Path

import pytest

from _gateway_process import HEADERS, ORIGIN, get, owned_server, wire
from zotero_summarizer.cli._mobile_serve import private_listener
from zotero_summarizer.settings import Settings


def test_real_gateway_transport_admission_redirect_and_spoof_refusal(tmp_path):
    with owned_server(tmp_path, "mobile") as (_child, info):
        path = info["address"]
        response = get(path)
        assert response.startswith(b"HTTP/1.1 200")
        body = json.loads(response.split(b"\r\n\r\n", 1)[1])
        assert body["base_url"] == ORIGIN + "/"
        assert body["host"] == "reader.example-tailnet.ts.net"
        redirected = get(path, "/trailing")
        assert redirected.startswith(b"HTTP/1.1 307")
        assert ("location: " + ORIGIN + "/trailing/").encode() in redirected
        for field, value in [("Tailscale-User-Login", "other@example.net"), ("X-Forwarded-Proto", "http"),
                             ("X-Forwarded-Host", "evil.ts.net"), ("Origin", "https://evil.invalid")]:
            headers = {**HEADERS, field: value}
            denied = get(path, headers=headers)
            assert denied.startswith(b"HTTP/1.1 403"), denied
            assert b"base_url" not in denied
        denied = get(path, headers={"Host": "localhost"})
        assert denied.startswith(b"HTTP/1.1 403")
    assert not Path(path).exists()


def test_real_websocket_transport_admits_owner_and_refuses_non_owner(tmp_path):
    with owned_server(tmp_path, "mobile") as (_child, info):
        fields = {**HEADERS, "Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
                  "Sec-WebSocket-Key": base64.b64encode(token_bytes(16)).decode("ascii")}
        request = "GET /socket HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in fields.items()) + "\r\n"
        response = wire(info["address"], request.encode(), stop_at=b"gateway fixture")
        assert response.startswith(b"HTTP/1.1 101")
        assert b"gateway fixture" in response
        fields["Tailscale-User-Login"] = "other@example.net"
        fields["Sec-WebSocket-Key"] = base64.b64encode(token_bytes(16)).decode("ascii")
        request = "GET /socket HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in fields.items()) + "\r\n"
        denied = wire(info["address"], request.encode())
        assert denied.startswith(b"HTTP/1.1 403")
        assert b"gateway fixture" not in denied


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_real_gateway_signal_releases_owned_socket_and_new_owner_can_restart(tmp_path, signum):
    from _gateway_process import SAFETY_SECONDS

    with owned_server(tmp_path, "mobile") as (child, info):
        path = Path(info["address"])
        assert get(str(path)).startswith(b"HTTP/1.1 200")
        child.send_signal(signum)
        child.wait(timeout=SAFETY_SECONDS)
        assert child.returncode != 0
        assert not path.exists()
        settings = Settings.load(project_root=path.parents[2])
        with private_listener(settings):
            assert path.is_socket()
    assert not path.exists()
