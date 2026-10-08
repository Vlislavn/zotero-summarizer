"""Opt-in Chromium checks against synthetic origins only; no internet or user profile.

Run with ZS_BROWSER_EGRESS_SMOKE=1. The normal suite keeps optional browser startup
out of its native-library fork baseline; socket-level proxy tests always run.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import gzip
import hashlib
import json
import os
import socket
import threading

import pytest

from zotero_summarizer.integrations import browser_fetch
from zotero_summarizer.integrations.app_rss import RssUrlRejected


pytestmark = pytest.mark.skipif(os.environ.get("ZS_BROWSER_EGRESS_SMOKE") != "1", reason="opt-in local Chromium check")
_CONNECT = socket.socket.connect
_LIVE_BROWSER_TIMEOUT_SECS = 10
_ARTICLE_SOURCE_CAP_BYTES = 16 * 1024
_ARTICLE_MARKERS = (
    "EARLY_JS_ARTICLE_MARKER",
    "MIDDLE_JS_ARTICLE_MARKER",
    "LATE_JS_ARTICLE_MARKER",
    "CYRILLIC_ARTICLE_MARKER — Привет, мир",
    "GREEK_ARTICLE_MARKER — βιολογία — café",
)
_ARTICLE_MARKER_PARTS = (
    ("EARLY_", "JS_ARTICLE_MARKER"),
    ("MIDDLE_", "JS_ARTICLE_MARKER"),
    ("LATE_", "JS_ARTICLE_MARKER"),
    ("CYRILLIC_", "ARTICLE_MARKER — Привет, мир"),
    ("GREEK_", "ARTICLE_MARKER — βιολογία — café"),
)
_ARTICLE_OVER_CAP_SENTENCE = "Benign article paragraph. "
_ARTICLE_OVER_CAP_REPETITIONS = 1024
_ARTICLE_OVER_CAP_TEXT = _ARTICLE_OVER_CAP_SENTENCE * _ARTICLE_OVER_CAP_REPETITIONS


def _dynamic_article_page(texts, *, repeat_count=1):
    payload = json.dumps(texts, ensure_ascii=False)
    return f'''<!doctype html><html><head><meta charset="utf-8"></head><body>
<article id="paper"></article><script>
const paper = document.querySelector("#paper");
for (const [prefix, suffix] of {payload}) {{
  const paragraph = document.createElement("p");
  paragraph.textContent = prefix + suffix.repeat({repeat_count});
  paper.appendChild(paragraph);
}}
</script></body></html>'''.encode("utf-8")


@pytest.fixture
def cdp_commands(monkeypatch):
    from patchright.sync_api import CDPSession

    commands = []
    send = CDPSession.send

    def record(session, method, params=None):
        commands.append(method)
        return send(session, method, params)

    monkeypatch.setattr(CDPSession, "send", record)
    return commands


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
            status, content_type, headers = 200, "text/html; charset=utf-8", {}
            if self.path == "/auth":
                status, headers, body = 401, {"WWW-Authenticate": 'Basic realm="origin"'}, b"Please sign in"
            elif self.path == "/redirect":
                status, headers, body = 302, {"Location": private}, b""
            elif self.path == "/cookie":
                status, headers, body = 302, {
                    "Location": "/cookie_page", "Set-Cookie": "session=redirect; Path=/",
                }, b""
            elif self.path == "/cookie_page":
                headers, body = {"Set-Cookie": "session=kept; Path=/"}, b'<meta name="citation_pdf_url" content="/pdf">'
            elif self.path == "/pdf":
                content_type = "application/pdf"
                if "session=kept" in (self.headers.get("Cookie") or ""):
                    body = b"%PDF-1.7\nsynthetic"
                else:
                    status, headers, body = 401, {"WWW-Authenticate": 'Basic realm="origin"'}, b"Please sign in"
            elif self.path == "/auth_download":
                status, headers, body = 302, {
                    "Location": "/auth_download_page", "Set-Cookie": "download=redirect; Path=/",
                }, b""
            elif self.path == "/auth_download_page":
                headers = {"Set-Cookie": "download=ready; Path=/"}
                body = b'<a href="/protected.pdf">Download PDF</a>'
            elif self.path == "/protected.pdf":
                content_type = "application/pdf"
                if "download=ready" in (self.headers.get("Cookie") or ""):
                    body = b"%PDF-1.7\nauthenticated download"
                else:
                    status, headers, body = 401, {"WWW-Authenticate": 'Basic realm="origin"'}, b"Please sign in"
            elif self.path == "/missing_pdf_paywall":
                body = (b'<meta name="citation_pdf_url" content="/missing_pdf">'
                        b"<h1>Sign in to read this article</h1>")
            elif self.path == "/missing_pdf":
                status, content_type, headers, body = 401, "application/pdf", {
                    "WWW-Authenticate": 'Basic realm="origin"',
                }, b"Login required"
            elif self.path == "/article_dynamic":
                body = _dynamic_article_page(_ARTICLE_MARKER_PARTS)
            elif self.path == "/article_overcap":
                body = _dynamic_article_page(
                    [("", _ARTICLE_OVER_CAP_SENTENCE)], repeat_count=_ARTICLE_OVER_CAP_REPETITIONS,
                )
            else:
                body = {
                    "/embedded": f'<iframe src="{private}"></iframe>'.encode(),
                    "/meta": f'<meta name="citation_pdf_url" content="{private}">'.encode(),
                    "/gzip_pdf": b"%PDF" + b"x" * 4092,
                    "/gzip_html": b'<html><body><script>document.body.textContent="Streamed article"</script></body></html>',
                }.get(self.path, b"<h1>Public paper</h1>")
                if self.path == "/gzip_pdf":
                    content_type = "application/pdf"
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            if self.path.startswith("/gzip_"):
                body = gzip.compress(body)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", content_type)
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
def test_real_browser_egress(tmp_path, origin_lab, path, render, cdp_commands):
    from patchright.sync_api import Error
    def acquire():
        url = "http://paper.example" + path
        if render:
            return browser_fetch.render_article_pdf(
                url, cache_dir=tmp_path / "cache", timeout=_LIVE_BROWSER_TIMEOUT_SECS,
            )
        return browser_fetch.fetch_pdf_via_browser(
            url, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache",
            timeout=_LIVE_BROWSER_TIMEOUT_SECS,
        )

    if path in {"/public", "/cookie"}:
        result = acquire()
        assert result.read_bytes().startswith(b"%PDF")
    else:
        if path == "/embedded" and render:
            with pytest.raises(ValueError) as error:
                acquire()
            assert str(error.value) == "article page contains no extractable text"
        else:
            with pytest.raises((RssUrlRejected, Error)):
                acquire()
        assert not list((tmp_path / "cache").glob("*.pdf"))

    assert all(path != "/private" for path, _ in origin_lab)
    assert not any(path == "/authorization" for path, _ in origin_lab)
    if path == "/cookie":
        assert ("/cookie_page", "session=redirect") in origin_lab
        assert ("/pdf", "session=kept") in origin_lab
    assert "Page.printToPDF" not in cdp_commands


def test_authenticated_pdf_download_keeps_redirect_cookies(tmp_path, origin_lab, cdp_commands):
    result = browser_fetch.fetch_pdf_via_browser(
        "http://paper.example/auth_download", profile_dir=tmp_path / "profile",
        cache_dir=tmp_path / "cache", timeout=_LIVE_BROWSER_TIMEOUT_SECS,
    )

    assert result.read_bytes() == b"%PDF-1.7\nauthenticated download"
    assert ("/auth_download_page", "download=redirect") in origin_lab
    assert ("/protected.pdf", "download=ready") in origin_lab
    assert not any(path == "/authorization" for path, _ in origin_lab)
    assert "Page.printToPDF" not in cdp_commands


def test_declared_missing_pdf_does_not_render_paywall(tmp_path, origin_lab, cdp_commands):
    cache = tmp_path / "cache"
    result = browser_fetch.fetch_pdf_via_browser(
        "http://paper.example/missing_pdf_paywall", profile_dir=tmp_path / "profile",
        cache_dir=cache, timeout=_LIVE_BROWSER_TIMEOUT_SECS, render_fallback=True,
    )

    assert result is None
    assert any(path == "/missing_pdf" for path, _ in origin_lab)
    assert not list(cache.glob("*.pdf"))
    assert "Page.printToPDF" not in cdp_commands


@pytest.mark.parametrize("path,cap", [("/gzip_pdf", 4096), ("/gzip_pdf", 4095),
                                    ("/stream", 4096), ("/gzip_html", 50000), ("/auth", 4096)])
def test_real_browser_stream_limits_and_html(tmp_path, origin_lab, path, cap, cdp_commands):
    def acquire():
        return browser_fetch.fetch_pdf_via_browser(
            "http://paper.example" + path, profile_dir=tmp_path / "profile",
            cache_dir=tmp_path / "cache", timeout=_LIVE_BROWSER_TIMEOUT_SECS,
            max_bytes=cap, render_fallback=path == "/gzip_html",
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
    assert "Page.printToPDF" not in cdp_commands


def test_chrome_channel_persistent_pdf_download_keeps_redirect_cookies(
    tmp_path, origin_lab, cdp_commands,
):
    profile = tmp_path / "chrome-profile"
    cache = tmp_path / "cache"
    pdf_url = "http://paper.example/auth_download"
    raw_publisher_path = browser_fetch._cache_path(pdf_url, cache)
    article_snapshot_path = (
        cache / "article-snapshots" / f"{hashlib.sha256(pdf_url.encode('utf-8')).hexdigest()}.pdf"
    )
    result = browser_fetch.fetch_pdf_via_browser(
        pdf_url,
        profile_dir=profile,
        cache_dir=cache,
        timeout=_LIVE_BROWSER_TIMEOUT_SECS,
        headless=True,
        channel="chrome",
    )

    assert result == raw_publisher_path
    assert raw_publisher_path.is_file()
    assert not article_snapshot_path.exists()
    assert result.read_bytes() == b"%PDF-1.7\nauthenticated download"
    assert profile.is_dir()
    assert ("/auth_download_page", "download=redirect") in origin_lab
    assert ("/protected.pdf", "download=ready") in origin_lab
    requests = [path for path, _ in origin_lab if path not in {"/authorization", "/stream-sent"}]
    assert len(requests) > 0
    assert not any(path == "/authorization" for path, _ in origin_lab)
    assert not any(path == "/private" for path, _ in origin_lab)
    assert cdp_commands
    assert cdp_commands.count("Page.printToPDF") == 0
