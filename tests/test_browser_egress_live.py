"""Opt-in Chromium checks against synthetic origins only; no internet or user profile.

Run with ZS_BROWSER_EGRESS_SMOKE=1. The normal suite keeps optional browser startup
out of its native-library fork baseline; socket-level proxy tests always run.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import gzip
import os
import socket
import threading

import pytest

from zotero_summarizer.integrations import browser_fetch
from zotero_summarizer.integrations.app_rss import RssUrlRejected


pytestmark = pytest.mark.skipif(os.environ.get("ZS_BROWSER_EGRESS_SMOKE") != "1", reason="opt-in local Chromium check")
_CONNECT = socket.socket.connect


@pytest.fixture
def origin_lab(monkeypatch):
    seen = []
    class Origin(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            seen.append((self.path, self.headers.get("Cookie")))
            if self.headers.get("Authorization") or self.headers.get("Proxy-Authorization"):
                seen.append(("/authorization", True))
            if self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                sent = 0
                try:
                    for i in range(256):
                        chunk = (b"%PDF" if i == 0 else b"xxxx") + b"x" * 16380
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                        sent += len(chunk)
                        threading.Event().wait(0.002)
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Expected: the bounded client cancels this synthetic oversized response.
                seen.append(("/stream-sent", sent))
                return
            private = f"http://127.0.0.1:{self.server.server_port}/private"
            if self.path == "/auth":
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="origin"')
                body = b"Please sign in"
            elif self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", private)
                body = b""
            elif self.path == "/cookie":
                self.send_response(302)
                self.send_header("Location", "/cookie_page")
                self.send_header("Set-Cookie", "session=redirect; Path=/")
                body = b""
            elif self.path == "/cookie_page":
                self.send_response(200)
                self.send_header("Set-Cookie", "session=kept; Path=/")
                body = b'<meta name="citation_pdf_url" content="/pdf">'
            else:
                self.send_response(200)
                body = {"/embedded": f'<iframe src="{private}"></iframe>'.encode(),
                        "/meta": f'<meta name="citation_pdf_url" content="{private}">'.encode(),
                        "/gzip_pdf": b"%PDF" + b"x" * 4092,
                        "/gzip_html": b'<html><body><script>document.body.textContent="Streamed article"</script></body></html>',
                        "/pdf": b"%PDF-1.7\nsynthetic"}.get(self.path, b"<h1>Public paper</h1>")
            if self.path.startswith("/gzip_"):
                body = gzip.compress(body)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", "application/pdf" if self.path in {"/pdf", "/gzip_pdf"} else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Origin) as origin:
        def resolve(host, port, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port))]
        def connect(address, **kw):
            assert address[0] == "1.1.1.1", address
            sock = socket.socket()
            sock.settimeout(kw.get("timeout", 5))
            _CONNECT(sock, origin.server_address)
            return sock
        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        monkeypatch.setattr(socket, "create_connection", connect)
        thread = threading.Thread(target=origin.serve_forever, kwargs={"poll_interval": 0.1})
        thread.start()
        try:
            yield seen
        finally:
            origin.shutdown()
            thread.join()


@pytest.mark.parametrize("path,render", [("/public", True), ("/cookie", False),
                                        ("/redirect", True), ("/redirect", False),
                                        ("/embedded", True), ("/meta", False)])
def test_real_browser_egress(tmp_path, origin_lab, path, render):
    from patchright.sync_api import Error
    def acquire():
        url = "http://paper.example" + path
        if render:
            return browser_fetch.render_article_pdf(url, cache_dir=tmp_path / "cache", timeout=10)
        return browser_fetch.fetch_pdf_via_browser(
            url, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache", timeout=10,
        )

    if path in {"/public", "/cookie"}:
        result = acquire()
        assert result.read_bytes().startswith(b"%PDF")
    else:
        with pytest.raises((RssUrlRejected, Error)):
            acquire()
        assert not list((tmp_path / "cache").glob("*.pdf"))

    assert all(path != "/private" for path, _ in origin_lab)
    if path == "/cookie":
        assert ("/cookie_page", "session=redirect") in origin_lab
        assert ("/pdf", "session=kept") in origin_lab


@pytest.mark.parametrize("path,cap", [("/gzip_pdf", 4096), ("/gzip_pdf", 4095),
                                    ("/stream", 4096), ("/gzip_html", 50000), ("/auth", 4096)])
def test_real_browser_stream_limits_and_html(tmp_path, origin_lab, path, cap):
    def acquire():
        return browser_fetch.fetch_pdf_via_browser(
            "http://paper.example" + path, profile_dir=tmp_path / "profile",
            cache_dir=tmp_path / "cache", timeout=10, max_bytes=cap, render_fallback=path == "/gzip_html",
        )

    if path == "/stream" or cap == 4095:
        with pytest.raises(ValueError, match="exceeds max_bytes"):
            acquire()
        assert not list((tmp_path / "cache").glob("*.pdf"))
    elif path == "/auth":
        assert acquire() is None
    elif path == "/gzip_pdf":
        assert acquire().read_bytes() == b"%PDF" + b"x" * 4092
    else:
        import fitz
        with fitz.open(acquire()) as document:
            assert "Streamed article" in "".join(page.get_text() for page in document)
    assert not any(path == "/authorization" for path, _ in origin_lab)
    if path == "/stream":
        sent = [count for name, count in origin_lab if name == "/stream-sent"]
        assert len(sent) == 1 and sent[0] < 4 * 1024 * 1024
