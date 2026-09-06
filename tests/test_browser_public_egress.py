"""Browser PDF acquisition must use public-only egress, including its API client."""
from types import SimpleNamespace

import pytest

from tests.test_browser_fetch_degrade import _Ctx, _Page, _PW, _Req, _Resp, _PDF
from zotero_summarizer.integrations import browser_fetch
from zotero_summarizer.integrations.app_rss import RssUrlRejected


@pytest.mark.parametrize("url", ["http://127.0.0.1/private", "http://[::1]/private", "file:///etc/passwd"])
@pytest.mark.parametrize("render", [False, True])
def test_private_article_never_launches_browser(tmp_path, monkeypatch, url, render):
    launched = []
    page = _Page()
    page.pdf = lambda **kw: _PDF
    ctx = _Ctx(_Req({url: _Resp(_PDF)}), page)
    pw = _PW(ctx)
    def launch(**kw):
        launched.append(kw)
        return SimpleNamespace(new_context=lambda: ctx, close=lambda: None)
    pw.chromium.launch = launch
    old_launch = pw.chromium.launch_persistent_context
    def persistent(*args, **kw):
        launched.append(kw)
        return old_launch(*args, **kw)
    pw.chromium.launch_persistent_context = persistent
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: pw, RuntimeError))

    with pytest.raises(RssUrlRejected):
        if render:
            browser_fetch.render_article_pdf(url, cache_dir=tmp_path / "cache")
        else:
            browser_fetch.fetch_pdf_via_browser(url, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache")

    assert launched == []
    assert list((tmp_path / "cache").glob("*.pdf")) == []


def test_persistent_browser_has_no_direct_network_route(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])
    url = "https://paper.example/article"
    pw = _PW(_Ctx(_Req({url: _Resp(_PDF)}), _Page()))

    assert browser_fetch._drive_browser(
        browser_fetch._BrowserLib(lambda: pw, RuntimeError), url, tmp_path, 5, 1000, True,
    ) == _PDF

    assert pw.launch_kwargs["proxy"]["server"].startswith("http://127.0.0.1:")
    assert "--proxy-bypass-list=<-loopback>" in pw.launch_kwargs["args"]


@pytest.mark.parametrize("stage", ["navigation", "close"])
def test_failed_login_never_marks_a_completed_session(tmp_path, monkeypatch, stage):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])
    page = _Page()
    def fail(*args, **kwargs):
        raise RuntimeError("login failed")
    page.goto = fail if stage == "navigation" else lambda *a, **k: None
    page.wait_for_event = fail
    ctx = _Ctx(_Req({}), page)
    ctx.pages = [page]
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: _PW(ctx), RuntimeError))

    result = browser_fetch.open_login_window("https://login.example/", tmp_path)

    assert result["ok"] is False
    assert result["logged_in"] is False
    assert not (tmp_path / browser_fetch._LOGIN_MARKER).exists()


@pytest.mark.parametrize("url", ["", "https://login.example/"])
def test_completed_login_retains_the_public_egress_boundary(tmp_path, monkeypatch, url):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])
    page = _Page()
    events = []
    page.goto = lambda *a, **k: events.append("navigate")
    page.wait_for_event = lambda *a, **k: events.append("close")
    ctx = _Ctx(_Req({}), page)
    ctx.pages = [page]
    pw = _PW(ctx)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: pw, RuntimeError))

    result = browser_fetch.open_login_window(url, tmp_path)

    assert result == {"ok": True, "logged_in": True, "error": ""}
    assert events == (["navigate", "close"] if url else ["close"])
    assert pw.launch_kwargs["proxy"]["server"].startswith("http://127.0.0.1:")
