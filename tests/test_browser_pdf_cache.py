"""Both browser cache readers enforce the same magic/size contract as HTTP PDFs."""
from contextlib import nullcontext
import hashlib
import socket
from types import SimpleNamespace

import httpx
import pytest

from tests.test_browser_fetch_degrade import _Ctx, _Page, _PW, _Req, _Resp
from zotero_summarizer.integrations import browser_fetch, pdf_fetch


URL = "https://paper.example/document"
PDF = b"%PDF-1.7\nnew"
TEXT_PDF = b"%PDF-1.7\ntext snapshot"
MAX_BYTES = 50_000


def _expected_publisher_cache_path(url, cache_dir):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{key}.pdf"


def _expected_article_cache_path(url, cache_dir):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / "article-snapshots" / f"{key}.pdf"


def _install_persistent_browser(monkeypatch, *, body, content_type="text/html"):
    page = _Page()
    page._runtime_results = [
        {"text": "Article body from bounded DOM text.", "status": "complete", "done": True, "nodes": 2}
    ]
    context = _Ctx(_Req({URL: _Resp(body, ctype=content_type)}), page)
    playwright = _PW(context)
    playwright.chromium.launch = lambda **_kwargs: SimpleNamespace(
        new_context=lambda: context, close=lambda: None,
    )
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: playwright, RuntimeError))
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *args: nullcontext({}))
    monkeypatch.setattr(browser_fetch, "render_text_pdf", lambda text, *, max_bytes: TEXT_PDF)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))],
    )
    return page


@pytest.fixture(params=[False, True], ids=["fetch", "render"])
def cached_browser(request, tmp_path):
    render = request.param
    cache = tmp_path / "cache"
    cache.mkdir()
    path = (
        _expected_article_cache_path(URL, cache)
        if render else _expected_publisher_cache_path(URL, cache)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    def acquire():
        if render:
            return browser_fetch.render_article_pdf(URL, cache_dir=cache, max_bytes=MAX_BYTES)
        return browser_fetch.fetch_pdf_via_browser(
            URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        )
    return path, acquire, render


@pytest.mark.parametrize("body", [b"<html>login", b"%PDF" + b"x" * MAX_BYTES, b"", b"%PD"])
def test_invalid_cache_is_never_a_success_when_browser_is_absent(monkeypatch, cached_browser, body):
    path, acquire, _render = cached_browser
    path.write_bytes(body)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (None, None))

    assert acquire() is None
    assert path.read_bytes() == body


def test_valid_cache_at_limit_needs_neither_browser_nor_network(monkeypatch, cached_browser):
    path, acquire, _render = cached_browser
    body = b"%PDF" + b"x" * (MAX_BYTES - 4)
    path.write_bytes(body)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: pytest.fail("valid cache launched browser"))

    assert acquire() == path
    assert path.read_bytes() == body


@pytest.mark.parametrize("legacy_prefix", ["render:", "render-text:"])
def test_old_prefixed_snapshot_cache_is_ignored_and_preserved(tmp_path, monkeypatch, legacy_prefix):
    cache = tmp_path / "cache"
    cache.mkdir()
    old_key = hashlib.sha256((legacy_prefix + URL).encode()).hexdigest()[:16]
    old_path = cache / f"{old_key}.pdf"
    old_body = b"%PDF-1.7\nlegacy print derivative"
    old_path.write_bytes(old_body)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (None, None))

    assert browser_fetch.render_article_pdf(URL, cache_dir=cache, max_bytes=MAX_BYTES) is None
    assert old_path.read_bytes() == old_body


def test_atomic_cache_failure_cleans_temp_and_preserves_old_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    final_path = _expected_publisher_cache_path(URL, cache)
    final_path.write_bytes(b"invalid prior cache")

    def fail_replace(source, destination):
        assert source.parent == destination.parent == cache
        raise OSError("atomic replace failed")

    monkeypatch.setattr(browser_fetch.os, "replace", fail_replace)
    with pytest.raises(OSError, match="atomic replace failed"):
        browser_fetch._write_cache(final_path, b"next cache generation")

    assert final_path.read_bytes() == b"invalid prior cache"
    assert list(cache.glob("*.tmp")) == []
    assert list(cache.glob(".*.tmp")) == []


@pytest.mark.parametrize("fail", [False, True])
def test_rebuild_replaces_only_after_success(monkeypatch, cached_browser, fail):
    path, acquire, render = cached_browser
    path.write_bytes(b"<html>old")
    page = _Page()
    body = RuntimeError("browser failed") if fail else PDF
    page._print_body = body
    if render:
        page._runtime_results = [
            {"text": "Article body with a Late-JS-SENTINEL.", "status": "complete", "done": True, "nodes": 2}
        ]
    response = _Resp(b"<html>article shell</html>" if render else body)
    ctx = _Ctx(_Req({URL: response}), page)
    pw = _PW(ctx)
    pw.chromium.launch = lambda **kw: SimpleNamespace(new_context=lambda: ctx, close=lambda: None)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: pw, RuntimeError))
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *a: nullcontext({}))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])

    if fail:
        if render:
            page._runtime_results = [
                {"text": "partial article", "status": "incomplete", "done": False, "nodes": 100_001}
            ]
            with pytest.raises(ValueError, match="node budget"):
                acquire()
        else:
            with pytest.raises(RuntimeError, match="browser failed"):
                acquire()
        assert path.read_bytes() == b"<html>old"
    else:
        assert acquire() == path
        if render:
            import fitz

            with fitz.open(path) as document:
                assert "Late-JS-SENTINEL" in document[0].get_text()
        else:
            assert path.read_bytes() == PDF


def test_snapshot_never_enters_headless_pdf_url_cache(tmp_path, monkeypatch):
    _install_persistent_browser(monkeypatch, body=b"<html>article shell</html>")
    cache = tmp_path / "cache"
    snapshot_path = browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    )
    assert snapshot_path is not None
    assert snapshot_path.read_bytes() == TEXT_PDF
    monkeypatch.setattr(pdf_fetch, "offline_requested", lambda: True)

    assert pdf_fetch.fetch_pdf("render-text:" + URL, cache_dir=cache) is None
    assert pdf_fetch.fetch_pdf(URL, cache_dir=cache) is None
    assert snapshot_path == _expected_article_cache_path(URL, cache)


def test_text_fallback_keeps_headless_pdf_cache_publisher_owned(tmp_path, monkeypatch):
    _install_persistent_browser(monkeypatch, body=b"<html>article shell</html>")
    cache = tmp_path / "cache"
    browser_result = browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    )
    raw_path = _expected_publisher_cache_path(URL, cache)
    snapshot_path = _expected_article_cache_path(URL, cache)
    publisher_pdf = b"%PDF-1.7\npublisher source"
    requests = []
    monkeypatch.setattr(pdf_fetch, "offline_requested", lambda: False)

    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=publisher_pdf)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        headless_result = pdf_fetch.fetch_pdf(URL, cache_dir=cache, http_client=client)

    assert headless_result is not None
    assert headless_result.read_bytes() == publisher_pdf
    assert requests == [URL]
    assert browser_result == snapshot_path
    assert snapshot_path.read_bytes() == TEXT_PDF
    assert headless_result == raw_path


def test_text_snapshot_cache_is_shared_but_opt_in_and_needs_no_browser(tmp_path, monkeypatch):
    page = _install_persistent_browser(monkeypatch, body=b"<html>article shell</html>")
    cache = tmp_path / "cache"
    snapshot_path = _expected_article_cache_path(URL, cache)
    assert not snapshot_path.parent.exists()

    assert browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    ) == snapshot_path
    assert snapshot_path.parent.is_dir()
    methods = [method for session in page._cdps for method, _ in session.calls]
    assert "Page.printToPDF" not in methods

    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: pytest.fail("snapshot cache launched browser"))
    assert browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    ) == snapshot_path
    assert browser_fetch.render_article_pdf(URL, cache_dir=cache, max_bytes=MAX_BYTES) == snapshot_path

    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (None, None))
    assert browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
    ) is None


def test_missing_browser_does_not_create_article_cache_subdirectory(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (None, None))

    assert browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    ) is None
    assert browser_fetch.render_article_pdf(URL, cache_dir=cache, max_bytes=MAX_BYTES) is None
    assert not (cache / "article-snapshots").exists()


def test_invalid_raw_cache_survives_text_fallback(tmp_path, monkeypatch):
    _install_persistent_browser(monkeypatch, body=b"<html>article shell</html>")
    cache = tmp_path / "cache"
    raw_path = _expected_publisher_cache_path(URL, cache)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_raw = b"invalid prior publisher cache"
    raw_path.write_bytes(invalid_raw)

    result = browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    )

    assert result == _expected_article_cache_path(URL, cache)
    assert raw_path.read_bytes() == invalid_raw
    assert result.read_bytes() == TEXT_PDF


def test_genuine_browser_pdf_stays_in_raw_cache(tmp_path, monkeypatch):
    _install_persistent_browser(monkeypatch, body=PDF, content_type="application/pdf")
    cache = tmp_path / "cache"

    result = browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    )

    assert result == _expected_publisher_cache_path(URL, cache)
    assert result.read_bytes() == PDF
    assert not _expected_article_cache_path(URL, cache).exists()


def test_article_snapshot_path_has_full_raw_url_identity(tmp_path):
    assert browser_fetch.article_snapshot_path(URL, tmp_path) == _expected_article_cache_path(URL, tmp_path)


def test_render_article_captured_publisher_pdf_uses_raw_cache(tmp_path, monkeypatch):
    _install_persistent_browser(monkeypatch, body=PDF, content_type="application/pdf")
    cache = tmp_path / "cache"

    result = browser_fetch.render_article_pdf(URL, cache_dir=cache, max_bytes=MAX_BYTES)

    assert result == _expected_publisher_cache_path(URL, cache)
    assert result.read_bytes() == PDF
    assert not _expected_article_cache_path(URL, cache).exists()


def test_raw_pdf_cache_precedes_opted_in_text_snapshot(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    raw_path = _expected_publisher_cache_path(URL, cache)
    snapshot_path = _expected_article_cache_path(URL, cache)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.parent.mkdir(parents=True)
    raw_path.write_bytes(PDF)
    snapshot_path.write_bytes(TEXT_PDF)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: pytest.fail("valid raw cache launched browser"))

    assert browser_fetch.fetch_pdf_via_browser(
        URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=MAX_BYTES,
        render_fallback=True,
    ) == raw_path
    assert browser_fetch.render_article_pdf(URL, cache_dir=cache, max_bytes=MAX_BYTES) == raw_path
