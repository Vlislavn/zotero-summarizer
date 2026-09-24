"""Both browser cache readers enforce the same magic/size contract as HTTP PDFs."""
from contextlib import nullcontext
import hashlib
import socket
from types import SimpleNamespace

import pytest

from tests.test_browser_fetch_degrade import _Ctx, _Page, _PW, _Req, _Resp
from zotero_summarizer.integrations import browser_fetch


URL = "https://paper.example/document"
PDF = b"%PDF-1.7\nnew"


@pytest.fixture(params=[False, True], ids=["fetch", "render"])
def cached_browser(request, tmp_path):
    render = request.param
    cache = tmp_path / "cache"
    cache.mkdir()
    key = hashlib.sha256((("render:" if render else "") + URL).encode()).hexdigest()[:16]
    path = cache / f"{key}.pdf"
    def acquire():
        if render:
            return browser_fetch.render_article_pdf(URL, cache_dir=cache, max_bytes=16)
        return browser_fetch.fetch_pdf_via_browser(URL, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=16)
    return path, acquire


@pytest.mark.parametrize("body", [b"<html>login", b"%PDF" + b"x" * 13, b"", b"%PD"])
def test_invalid_cache_is_never_a_success_when_browser_is_absent(monkeypatch, cached_browser, body):
    path, acquire = cached_browser
    path.write_bytes(body)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (None, None))

    assert acquire() is None
    assert path.read_bytes() == body


def test_valid_cache_at_limit_needs_neither_browser_nor_network(monkeypatch, cached_browser):
    path, acquire = cached_browser
    body = b"%PDF" + b"x" * 12
    path.write_bytes(body)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: pytest.fail("valid cache launched browser"))

    assert acquire() == path
    assert path.read_bytes() == body


@pytest.mark.parametrize("fail", [False, True])
def test_rebuild_replaces_only_after_success(monkeypatch, cached_browser, fail):
    path, acquire = cached_browser
    path.write_bytes(b"<html>old")
    page = _Page()
    body = RuntimeError("browser failed") if fail else PDF
    page._print_body = body
    response = _Resp(body)
    ctx = _Ctx(_Req({URL: response}), page)
    pw = _PW(ctx)
    pw.chromium.launch = lambda **kw: SimpleNamespace(new_context=lambda: ctx, close=lambda: None)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: pw, RuntimeError))
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *a: nullcontext({}))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])

    if fail:
        with pytest.raises(RuntimeError, match="browser failed"):
            acquire()
        assert path.read_bytes() == b"<html>old"
    else:
        assert acquire() == path
        assert path.read_bytes() == PDF
