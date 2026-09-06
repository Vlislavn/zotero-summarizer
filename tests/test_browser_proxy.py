"""Exercise real HTTP proxy framing over local socket pairs, without TCP/DNS."""
from concurrent.futures import ThreadPoolExecutor
import socket
import threading
from types import SimpleNamespace

import pytest

from zotero_summarizer.integrations import _browser_network as network
from zotero_summarizer.integrations.app_rss import RssUrlRejected


AUTH = "Basic cGFwZXI6c2VjcmV0"


def _exchange(request):
    server = SimpleNamespace(authorization=AUTH, connection_timeout=1, stopping=threading.Event())
    with ThreadPoolExecutor(max_workers=1) as executor:
        client, accepted = socket.socketpair()
        def serve():
            with accepted:
                network._Request(accepted, ("local", 0), server)
        job = executor.submit(serve)
        with client:
            client.settimeout(2)
            client.sendall(request)
            response = bytearray()
            while chunk := client.recv(65536):
                response.extend(chunk)
        job.result()
        return bytes(response)


@pytest.mark.parametrize("destination", ["127.0.0.1", "[::1]", "169.254.169.254", "192.168.1.1", "10.0.0.1"])
@pytest.mark.parametrize("method", ["GET", "CONNECT"])
def test_proxy_rejects_private_destinations_before_connect(monkeypatch, destination, method):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("private TCP connection"))
    target = f"{destination}:443" if method == "CONNECT" else f"http://{destination}/private"
    request = f"{method} {target} HTTP/1.1\r\nProxy-Authorization: {AUTH}\r\n\r\n".encode()

    with pytest.raises(RssUrlRejected, match="non-public"):
        _exchange(request)


def test_proxy_authenticates_before_dns_or_connect(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("unauthenticated DNS"))

    response = _exchange(b"CONNECT paper.example:443 HTTP/1.1\r\n\r\n")

    assert b" 407 " in response
    assert b'Proxy-Authenticate: Basic realm="paper-fetch"' in response


@pytest.mark.parametrize("method", ["GET", "CONNECT"])
def test_proxy_pins_dns_and_preserves_origin_bytes(monkeypatch, method):
    resolutions, connections, received = [], [], []
    def resolve(host, port, **kw):
        resolutions.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port))]
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    upstream, origin = socket.socketpair()
    def connect(address, **kw):
        connections.append(address)
        return upstream
    monkeypatch.setattr(socket, "create_connection", connect)
    target = "paper.example:443" if method == "CONNECT" else "http://paper.example/paper?q=1"
    request = (f"{method} {target} HTTP/1.1\r\nHost: wrong.example\r\n"
               f"Proxy-Authorization: {AUTH}\r\nCookie: session=kept\r\n\r\n").encode()
    if method == "CONNECT":
        request += b"TLS-client-bytes"
    def reply():
        with origin:
            origin.settimeout(2)
            data = bytearray()
            ending = b"TLS-client-bytes" if method == "CONNECT" else b"\r\n\r\n"
            while not data.endswith(ending):
                data.extend(origin.recv(65536))
            received.append(bytes(data))
            origin.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n%PDF")
    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(reply)
        response = _exchange(request)
        job.result()

    assert resolutions == ["paper.example"]
    assert connections == [("1.1.1.1", 443 if method == "CONNECT" else 80)]
    assert response.endswith(b"%PDF")
    assert AUTH.encode() not in received[0]
    if method == "CONNECT":
        assert received == [b"TLS-client-bytes"]
    else:
        assert received[0].startswith(b"GET /paper?q=1 HTTP/1.1\r\n")
        assert b"Host: paper.example\r\n" in received[0]
        assert b"Cookie: session=kept\r\n" in received[0]
