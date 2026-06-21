"""Unit tests for the review-fleet PDF acquisition chain (``_pdf_acquire``).

Every rung is mocked — no network, no browser. Asserts the ORDER (arXiv/OA headless
first, browser only as the proxied fallback), that ``enabled=False`` never opens a
browser, and that an unreachable proxied source surfaces ``needs_login``.
"""
from __future__ import annotations

import types
from pathlib import Path

from zotero_summarizer.services.library import _pdf_acquire


def _app(*, ua_enabled=False, ezproxy_prefix="", unpaywall=None, openalex=None, reuse_safari=False):
    ua = types.SimpleNamespace(
        enabled=ua_enabled, ezproxy_prefix=ezproxy_prefix, login_url="",
        browser_profile_dir="", headless=True, fetch_timeout_secs=60.0,
        reuse_safari_cookies=reuse_safari,
    )
    qr = types.SimpleNamespace(max_pdf_bytes=20_000_000, fetch_timeout_secs=30.0)
    config = types.SimpleNamespace(quality_review=qr, university_access=ua)
    return types.SimpleNamespace(
        app_state=types.SimpleNamespace(config=config),
        unpaywall_client=unpaywall, openalex_client=openalex,
    )


def _patch(monkeypatch, app, *, resolve=None, headless_fetch=None, browser_fetch=None):
    monkeypatch.setattr(_pdf_acquire, "get_state", lambda: app)
    monkeypatch.setattr(_pdf_acquire.pdf_fetch, "resolve_pdf_url", resolve or (lambda **_k: None))
    monkeypatch.setattr(_pdf_acquire.pdf_fetch, "fetch_pdf", headless_fetch or (lambda *_a, **_k: None))
    monkeypatch.setattr(_pdf_acquire.browser_fetch, "fetch_pdf_via_browser", browser_fetch or (lambda *_a, **_k: None))


def test_arxiv_short_circuits_before_browser(monkeypatch):
    """A resolvable arXiv/OA URL is fetched headless; the browser is never touched."""
    app = _app(ua_enabled=True)
    browser_calls: list = []
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: "https://arxiv.org/pdf/2401.00001.pdf",
        headless_fetch=lambda url, **_k: Path("/tmp/a.pdf"),
        browser_fetch=lambda *_a, **_k: browser_calls.append(_a) or Path("/x"),
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://arxiv.org/abs/2401.00001", "doi": ""})
    assert res.path == Path("/tmp/a.pdf") and res.needs_login is False
    assert browser_calls == []  # headless win → browser never invoked


def test_openalex_oa_url_rung_used_when_no_direct(monkeypatch):
    """No arXiv/Unpaywall URL, but OpenAlex has an oa_url → it's fetched headless."""
    work = types.SimpleNamespace(oa_url="https://oa.example/x.pdf")
    openalex = types.SimpleNamespace(fetch_work_by_doi=lambda doi: work)
    app = _app(openalex=openalex)
    fetched: list = []
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        headless_fetch=lambda url, **_k: (fetched.append(url), Path("/tmp/oa.pdf"))[1],
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "", "doi": "10.1/x"})
    assert res.path == Path("/tmp/oa.pdf")
    assert fetched == ["https://oa.example/x.pdf"]


def test_browser_rung_used_for_proxied_when_headless_fails(monkeypatch):
    """Headless yields nothing; a publisher URL + enabled access → the browser fetch
    runs and (here) succeeds."""
    app = _app(ua_enabled=True, ezproxy_prefix="https://ez.uni.edu/login?url=")
    seen: list = []
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        headless_fetch=lambda *_a, **_k: None,
        browser_fetch=lambda url, **_k: (seen.append(url), Path("/tmp/b.pdf"))[1],
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path == Path("/tmp/b.pdf")
    assert seen[-1] == "https://ez.uni.edu/login?url=https://www.nature.com/x"  # proxied target


def test_disabled_access_never_opens_browser(monkeypatch):
    """``enabled=False`` → the browser rung is skipped entirely; honest no-source."""
    app = _app(ua_enabled=False)
    browser_calls: list = []
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        browser_fetch=lambda *_a, **_k: browser_calls.append(_a) or Path("/x"),
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path is None and res.needs_login is False
    assert browser_calls == []


def test_reuse_safari_cookies_threaded_to_browser(monkeypatch):
    """When `reuse_safari_cookies` is set, the browser rung is called with it so the
    user's existing Safari session is injected (no separate in-app login)."""
    app = _app(ua_enabled=True, reuse_safari=True)
    seen = {}
    def _browser(url, **kw):
        seen.update(kw)
        return Path("/tmp/b.pdf")
    _patch(monkeypatch, app, resolve=lambda **_k: None, headless_fetch=lambda *_a, **_k: None, browser_fetch=_browser)
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path == Path("/tmp/b.pdf")
    assert seen.get("reuse_safari_cookies") is True


def test_unreachable_proxied_source_needs_login(monkeypatch):
    """A proxied source exists but the browser can't fetch it (paywall/Cloudflare,
    or no valid session) → the actionable ``needs_login`` signal — driven by the
    failed browser attempt, NOT a cookie-presence guess."""
    app = _app(ua_enabled=True)
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        headless_fetch=lambda *_a, **_k: None,
        browser_fetch=lambda *_a, **_k: None,
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path is None and res.needs_login is True
