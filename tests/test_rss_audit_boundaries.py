"""RSS trust-boundary regressions on isolated stores and transports (A024–A029)."""
import asyncio
import socket
from contextlib import closing

import httpx
import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes import rss as routes
from zotero_summarizer.integrations import app_rss
from zotero_summarizer.storage import feeds, rss


def _seed(tmp_path):
    db = tmp_path / "triage.db"
    with feeds.open_triage_conn(db) as conn:
        feed_id = rss.upsert_rss_feed(conn, name="Feed", url="https://example.com/rss")
        conn.commit()
    return app_rss.AppRssReader(db), feed_id


@pytest.mark.parametrize("url", [
    "http://example.com:no/rss", "http://example.com:65536/rss",
    "http://[::1/rss", "http://example.com:0/rss",
])
def test_malformed_feed_urls_are_api_validation_errors(url):
    for update in (False, True):
        with pytest.raises(APIError) as caught:
            asyncio.run(routes.update_feed(1, routes.RssFeedUpdateRequest(url=url)) if update else routes.add_feed(routes.RssFeedRequest(name="Feed", url=url)))
        assert caught.value.status_code == 422


def test_duplicate_feed_url_update_returns_conflict_without_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    reader, first = _seed(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(triage_db_path=reader.triage_db_path))
    monkeypatch.setattr(routes, "validate_rss_url", lambda url: url)
    with reader._conn() as conn:
        second = rss.upsert_rss_feed(conn, name="Second", url="https://example.com/second")
        conn.commit()
    with pytest.raises(APIError) as caught:
        asyncio.run(routes.update_feed(second, routes.RssFeedUpdateRequest(name="Changed", url="https://example.com/rss")))
    assert caught.value.status_code == 409
    with reader._conn() as conn:
        rows = {row["id"]: row for row in rss.list_rss_feeds(conn)}
    assert rows[first]["name"] == "Feed"
    assert rows[second]["name"] == "Second"
    assert rows[second]["url"] == "https://example.com/second"


@pytest.mark.parametrize("body", [b"<html><body>not a feed</body></html>", b"<rss><channel><item>"])
def test_invalid_feed_refresh_records_failure_and_raises(tmp_path, monkeypatch, body):
    reader, feed_id = _seed(tmp_path)
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda *_, **__: (body, httpx.Headers()))
    with pytest.raises(ValueError, match="RSS|Atom|feed"):
        reader.refresh_feeds()
    with reader._conn() as conn:
        row = conn.execute("SELECT * FROM rss_feeds WHERE id=?", (feed_id,)).fetchone()
        assert row["last_error"] and row["last_fetched_at"]
        assert conn.execute("SELECT COUNT(*) FROM rss_items").fetchone()[0] == 0


def test_feed_metadata_is_sanitized_before_identity_and_storage():
    bad = "\x00\U000e0061"
    entry = dict(title="Title" + bad, summary="Abstract" + bad, link="https://example.com/p" + bad,
                 id="guid" + bad, author="Author" + bad, published="2026" + bad)
    clean = app_rss._entry_to_item(entry, feed={"id": 1, "name": "Feed" + bad}, feed_title="Journal" + bad)
    assert clean["title"] == "Title" and clean["abstract"] == "Abstract"
    assert clean["url"] == "https://example.com/p"
    assert all("\x00" not in value and "\U000e0061" not in value for value in clean.values() if isinstance(value, str))
    expected = app_rss._entry_to_item({k: v.replace(bad, "") for k, v in entry.items()}, feed={"id": 1, "name": "Feed"}, feed_title="Journal")
    assert clean == expected


@pytest.mark.parametrize("host,address", [("example.com", "8.8.8.8"), ("пример.рф", "2001:4860:4860::8888")])
def test_fetch_pins_validated_dns_and_preserves_host_and_tls_name(monkeypatch, host, address):
    resolutions = []

    def resolve(name, port, **kwargs):
        resolutions.append(name)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address if len(resolutions) == 1 else "127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    expected_host = httpx.URL(f"https://{host}:8443/rss").host.encode("idna").decode()

    def handle(request):
        assert request.url.host == address
        assert request.headers["host"] == expected_host + ":8443"
        assert request.extensions["sni_hostname"] == expected_host
        return httpx.Response(200, stream=httpx.ByteStream(b"<rss/>"))

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(app_rss.httpx, "Client", lambda **_: client)
    body, _ = app_rss._fetch_public_url(f"https://{host}:8443/rss", timeout=1)
    assert body == b"<rss/>" and len(resolutions) == 1


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "999999"}, {"Content-Encoding": "gzip"}])
def test_rss_stream_is_bounded_and_closed(monkeypatch, headers):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(app_rss, "_RSS_MAX_BYTES", 64_000, raising=False)
    yielded = []
    closed = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(10):
                yielded.append(1)
                yield b"x" * 64_000

        def close(self):
            closed.append(True)

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=Body())))
    monkeypatch.setattr(app_rss.httpx, "Client", lambda **_: client)
    with pytest.raises(ValueError, match="size|large|encoding"):
        app_rss._fetch_public_url("https://example.com/rss", timeout=1)
    assert len(yielded) <= 2 and closed


def test_gzip_is_decoded_with_a_hard_output_limit(monkeypatch):
    import gzip

    monkeypatch.setattr(app_rss, "_RSS_MAX_BYTES", 1024)
    for body in (b"<rss/>", b"x" * 100_000):
        response = httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(gzip.compress(body)))
        with closing(response):
            if len(body) > 1024:
                with pytest.raises(ValueError, match="size limit"):
                    app_rss._read_rss_body(response)
            else:
                assert app_rss._read_rss_body(response) == body


@pytest.mark.parametrize("body", [
    b'<rss version="2.0"><channel><title>Empty</title></channel></rss>',
    '<?xml version="1.0" encoding="iso-8859-1"?><rss version="2.0"><channel><title>Journal</title><item><guid>one</guid><title>Café</title></item></channel></rss>'.encode("iso-8859-1"),
    b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Empty</title></feed>',
])
def test_valid_feed_bytes_and_empty_feeds_remain_supported(tmp_path, monkeypatch, body):
    reader, _ = _seed(tmp_path)
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda *_, **__: (body, httpx.Headers()))
    result = reader.refresh_feeds()
    assert result["errors"] == []
    if b"<item>" in body:
        assert reader.get_feed_items()[0]["title"] == "Café"
    else:
        assert result["inserted"] == 0


def test_refresh_storage_failure_rolls_back_feed_and_does_not_continue(tmp_path, monkeypatch):
    reader, _ = _seed(tmp_path)
    body = b'<rss version="2.0"><channel><title>Feed</title><item><guid>one</guid></item><item><guid>two</guid></item></channel></rss>'
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda *_, **__: (body, httpx.Headers()))
    upsert = rss.upsert_rss_item

    def fail_second(conn, **kwargs):
        if kwargs["item"]["guid"] == "two":
            raise RuntimeError("storage fault")
        return upsert(conn, **kwargs)

    monkeypatch.setattr(rss, "upsert_rss_item", fail_second)
    with pytest.raises(RuntimeError, match="storage fault"):
        reader.refresh_feeds()
    assert reader.get_feed_items() == []


def test_refresh_api_reports_invalid_feed_as_upstream_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    reader, _ = _seed(tmp_path)
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(triage_db_path=reader.triage_db_path))
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda *_, **__: (b"<html/>", httpx.Headers()))
    with pytest.raises(APIError) as caught:
        asyncio.run(routes.refresh_feeds(routes.RssRefreshRequest()))
    assert caught.value.status_code == 502


def test_daemon_refresh_propagates_invalid_feed(tmp_path, monkeypatch):
    from zotero_summarizer.services.triage.feeds._tick import _maybe_refresh_app_rss

    reader, _ = _seed(tmp_path)
    monkeypatch.setattr(app_rss, "_fetch_public_url", lambda *_, **__: (b"<html/>", httpx.Headers()))
    with pytest.raises(app_rss.RssUrlRejected):
        _maybe_refresh_app_rss(reader, {}, "tick")


def test_rss_http_crud_and_failure_contracts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zotero_summarizer.api.errors import install_error_handlers

    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(triage_db_path=tmp_path / "triage.db"))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    app = FastAPI()
    app.include_router(routes.router)
    install_error_handlers(app)
    with TestClient(app) as client:
        assert client.post("/api/rss/feeds", json={"name": "Bad", "url": "http://example.com:bad/rss"}).status_code == 422
        first = client.post("/api/rss/feeds", json={"name": "First", "url": "https://example.com/one"}).json()
        second = client.post("/api/rss/feeds", json={"name": "Second", "url": "https://example.com/two"}).json()
        conflict = client.put(f"/api/rss/feeds/{second['id']}", json={"name": "Changed", "url": first["url"]})
        assert conflict.status_code == 409 and conflict.json()["error"] == "conflict"
        assert {row["name"] for row in client.get("/api/rss/feeds").json()["feeds"]} == {"First", "Second"}
        monkeypatch.setattr(app_rss, "_fetch_public_url", lambda *_, **__: (b"<html/>", httpx.Headers()))
        failure = client.post("/api/rss/refresh", json={})
        assert failure.status_code == 502 and failure.json()["error"] == "rss_refresh_failed"
        assert any(row["last_error"] for row in client.get("/api/rss/feeds").json()["feeds"])
        assert client.delete(f"/api/rss/feeds/{second['id']}").json() == {"deleted": True}


def test_redirect_revalidates_before_any_request_to_changed_address(monkeypatch):
    resolutions = []
    requests = []

    def resolve(*args, **kwargs):
        resolutions.append(True)
        return [(2, 1, 6, "", ("8.8.8.8" if len(resolutions) == 1 else "10.0.0.1", 443))]

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "/next"}, stream=httpx.ByteStream(b""))

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(app_rss.httpx, "Client", lambda **_: client)
    with pytest.raises(app_rss.RssUrlRejected):
        app_rss._fetch_public_url("https://example.com/rss", timeout=1)
    assert len(requests) == 1
    assert requests[0].url.host == "8.8.8.8"


@pytest.mark.parametrize("kind", ["rss", "pdf"])
def test_real_http_transport_connects_only_to_pinned_ip_with_origin_tls_name(tmp_path, monkeypatch, kind):
    import ssl
    from httpcore._backends.sync import SyncBackend
    from zotero_summarizer.integrations.pdf_fetch import fetch_pdf

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 8443))])
    written = []
    connected = []
    closed = []
    tls = []
    body = b"<rss/>" if kind == "rss" else b"%PDF-1.7\nBody"

    class Wire:
        def write(self, buffer, timeout=None):
            written.append(buffer)

        def read(self, max_bytes, timeout=None):
            return b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            assert ssl_context.check_hostname and ssl_context.verify_mode == ssl.CERT_REQUIRED
            tls.append(server_hostname)
            return self

        def get_extra_info(self, info):
            return None

        def close(self):
            closed.append(True)

    def connect(_self, host, port, **kwargs):
        connected.append((host, port))
        return Wire()

    monkeypatch.setattr(SyncBackend, "connect_tcp", connect)
    url = "https://example.com:8443/paper?x=1"
    if kind == "rss":
        result, _ = app_rss._fetch_public_url(url, timeout=1)
    else:
        result = fetch_pdf(url, cache_dir=tmp_path).read_bytes()
    assert result == body
    assert connected == [("8.8.8.8", 8443)] and tls == ["example.com"]
    assert b"Host: example.com:8443\r\n" in b"".join(written)
    assert b"GET /paper?x=1 HTTP/1.1\r\n" in b"".join(written)
    assert closed
