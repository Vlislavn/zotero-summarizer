"""Unit tests for the review-fleet PDF acquisition chain (``_pdf_acquire``).

Every rung is mocked — no network, no browser. Asserts the ORDER (arXiv/OA headless
first, browser only as the proxied fallback), that ``enabled=False`` never opens a
browser, and that an unreachable proxied source surfaces ``needs_login``.
"""
from __future__ import annotations

import types
from pathlib import Path

from zotero_summarizer.services.library import _pdf_acquire


def _app(*, ua_enabled=False, ezproxy_prefix="", unpaywall=None, openalex=None, cookie_browser="",
         openalex_cache=None, review_web_articles=False, browser_channel=""):
    ua = types.SimpleNamespace(
        enabled=ua_enabled, ezproxy_prefix=ezproxy_prefix, login_url="",
        browser_profile_dir="", headless=True, fetch_timeout_secs=60.0,
        cookie_browser=cookie_browser, browser_channel=browser_channel,
    )
    qr = types.SimpleNamespace(max_pdf_bytes=20_000_000, fetch_timeout_secs=30.0,
                               review_web_articles=review_web_articles)
    prestige = types.SimpleNamespace(user_agent_email="")
    config = types.SimpleNamespace(quality_review=qr, university_access=ua, prestige=prestige)
    return types.SimpleNamespace(
        app_state=types.SimpleNamespace(config=config),
        unpaywall_client=unpaywall, openalex_client=openalex, openalex_cache=openalex_cache,
    )


def _patch(monkeypatch, app, *, resolve=None, headless_fetch=None, browser_fetch=None, render=None):
    monkeypatch.setattr(_pdf_acquire, "get_state", lambda: app)
    monkeypatch.setattr(_pdf_acquire.pdf_fetch, "resolve_pdf_url", resolve or (lambda **_k: None))
    monkeypatch.setattr(_pdf_acquire.pdf_fetch, "fetch_pdf", headless_fetch or (lambda *_a, **_k: None))
    monkeypatch.setattr(_pdf_acquire.browser_fetch, "fetch_pdf_via_browser", browser_fetch or (lambda *_a, **_k: None))
    monkeypatch.setattr(_pdf_acquire.browser_fetch, "render_article_pdf", render or (lambda *_a, **_k: None))


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


def test_pmc_rung_recovers_no_doi_pubmed_item(monkeypatch):
    """A PubMed paper in PMC but with NO DOI (e.g. AMIA proceedings): arXiv/Unpaywall
    resolve nothing, but the PMC rung supplies a PDF URL that enters the fetch chain
    (here the fetch is stubbed to succeed). Regression for the DOI-keyed rungs leaving
    the agentic cluster with no URL to try. (Live: fresh PMC is browser-routed.)"""
    app = _app(openalex_cache=object())  # opaque cache; resolver is stubbed below
    fetched: list = []
    captured: dict = {}

    def _resolve_pmc(**kw):
        captured.update(kw)
        return "https://pmc.ncbi.nlm.nih.gov/articles/PMC13274294/pdf/"

    monkeypatch.setattr(_pdf_acquire.pubmed, "resolve_pmc_pdf_url", _resolve_pmc)
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        headless_fetch=lambda url, **_k: (fetched.append(url), Path("/tmp/pmc.pdf"))[1],
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://pubmed.ncbi.nlm.nih.gov/42317861/", "doi": ""})
    assert res.path == Path("/tmp/pmc.pdf")
    assert fetched == ["https://pmc.ncbi.nlm.nih.gov/articles/PMC13274294/pdf/"]
    assert captured["pmid"] == "42317861"  # PMID parsed from the PubMed URL and threaded


def test_pmc_rung_skipped_when_no_cache(monkeypatch):
    """No OpenAlex cache (prestige/full-text disabled) → PMC rung is skipped, not crashed."""
    app = _app(openalex_cache=None)
    boom = lambda **_k: (_ for _ in ()).throw(AssertionError("resolve_pmc must not be called"))
    monkeypatch.setattr(_pdf_acquire.pubmed, "resolve_pmc_pdf_url", boom)
    _patch(monkeypatch, app, resolve=lambda **_k: None)
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://pubmed.ncbi.nlm.nih.gov/42317861/", "doi": ""})
    assert res.path is None and res.needs_login is False


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


def test_cookie_browser_threaded_to_browser(monkeypatch):
    """When `cookie_browser` is set, the browser rung is called with it so the user's
    existing session from that browser is injected (no separate in-app login)."""
    app = _app(ua_enabled=True, cookie_browser="chrome")
    seen = {}
    def _browser(url, **kw):
        seen.update(kw)
        return Path("/tmp/b.pdf")
    _patch(monkeypatch, app, resolve=lambda **_k: None, headless_fetch=lambda *_a, **_k: None, browser_fetch=_browser)
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path == Path("/tmp/b.pdf")
    assert seen.get("cookie_browser") == "chrome"


def test_browser_channel_threaded_to_browser(monkeypatch):
    """`browser_channel` (default 'chrome') reaches the browser rung so it drives the
    REAL Chrome binary — whose fingerprint matches the injected cf_clearance."""
    app = _app(ua_enabled=True, browser_channel="chrome")
    seen = {}
    def _browser(url, **kw):
        seen.update(kw)
        return Path("/tmp/b.pdf")
    _patch(monkeypatch, app, resolve=lambda **_k: None, headless_fetch=lambda *_a, **_k: None, browser_fetch=_browser)
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path == Path("/tmp/b.pdf")
    assert seen.get("channel") == "chrome"


def test_scholarly_landing_passes_render_fallback(monkeypatch):
    """A scholarly item whose landing fetch fails should be retried with
    render_fallback=True (so a DOI'd web page — e.g. a Nature news piece with no real
    PDF — renders instead of falsely reporting needs_login). The flag rides on the
    LANDING candidate only."""
    app = _app(ua_enabled=True, review_web_articles=True)
    seen = {}
    def _browser(url, **kw):
        seen.update(kw)
        return Path("/tmp/rendered.pdf")
    _patch(monkeypatch, app, resolve=lambda **_k: None, headless_fetch=lambda *_a, **_k: None, browser_fetch=_browser)
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/articles/d41586-x", "doi": "10.1038/d41586-x"})
    assert res.path == Path("/tmp/rendered.pdf")
    assert seen.get("render_fallback") is True


def test_unreachable_proxied_source_needs_login(monkeypatch):
    """A proxied source exists but the browser can't fetch it (paywall/Cloudflare,
    or no valid session) → the actionable ``needs_login`` signal — driven by the
    failed browser attempt, NOT a cookie-presence guess. ``login_url`` is the proxied
    landing (surfaced as a click-to-open sign-in link)."""
    app = _app(ua_enabled=True)
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        headless_fetch=lambda *_a, **_k: None,
        browser_fetch=lambda *_a, **_k: None,
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path is None and res.needs_login is True
    assert res.login_url == "https://www.nature.com/x"  # the landing to open + sign into


def test_headed_fallback_retries_when_headless_blocked(monkeypatch):
    """Interactive path (``allow_headed_fallback``): the headless attempt is bot-walled,
    so the proxied landing is retried once with a VISIBLE browser (headless=False), which
    passes the challenge."""
    app = _app(ua_enabled=True)  # headless=True by default
    headless_flags: list = []

    def _browser(url, **kw):
        headless_flags.append(kw.get("headless"))
        return None if kw.get("headless") else Path("/tmp/headed.pdf")

    _patch(monkeypatch, app, resolve=lambda **_k: None, headless_fetch=lambda *_a, **_k: None, browser_fetch=_browser)
    res = _pdf_acquire.acquire_pdf_for(
        "A", {"url": "https://www.nature.com/x", "doi": "10.1/x"}, allow_headed_fallback=True
    )
    assert res.path == Path("/tmp/headed.pdf")
    assert True in headless_flags and headless_flags[-1] is False  # headless first, then headed


def test_fleet_path_never_pops_headed_window(monkeypatch):
    """The background fleet (default ``allow_headed_fallback=False``) NEVER retries with a
    visible window — it degrades to ``needs_login`` headless-only."""
    app = _app(ua_enabled=True)
    headed: list = []

    def _browser(url, **kw):
        if kw.get("headless") is False:
            headed.append(url)
        return None

    _patch(monkeypatch, app, resolve=lambda **_k: None, headless_fetch=lambda *_a, **_k: None, browser_fetch=_browser)
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path is None and res.needs_login is True
    assert headed == []  # no visible window during a background run


def test_web_article_rendered_when_enabled(monkeypatch):
    """A non-scholarly web page (blog, no DOI) with review_web_articles ON → the render
    rung turns its HTML into a PDF the pipeline can review."""
    app = _app(review_web_articles=True)
    rendered: list = []
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        render=lambda url, **_k: (rendered.append(url), Path("/tmp/article.pdf"))[1],
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://eugeneyan.com/writing/x", "doi": ""})
    assert res.path == Path("/tmp/article.pdf") and res.needs_login is False
    assert rendered == ["https://eugeneyan.com/writing/x"]


def test_web_article_skipped_when_flag_off(monkeypatch):
    """review_web_articles OFF (default) → a blog is NOT rendered; honest no-source."""
    app = _app(review_web_articles=False)
    render_calls: list = []
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        render=lambda url, **_k: render_calls.append(url) or Path("/x"),
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://eugeneyan.com/writing/x", "doi": ""})
    assert res.path is None and res.needs_login is False
    assert render_calls == []  # never rendered


def test_scholarly_item_never_rendered(monkeypatch):
    """An item with a DOI is scholarly → the web-article renderer is never used (even
    with review_web_articles ON), so a paywall page isn't mistaken for an article."""
    app = _app(review_web_articles=True, ua_enabled=False)
    render_calls: list = []
    _patch(
        monkeypatch, app,
        resolve=lambda **_k: None,
        render=lambda url, **_k: render_calls.append(url) or Path("/x"),
    )
    res = _pdf_acquire.acquire_pdf_for("A", {"url": "https://www.nature.com/x", "doi": "10.1/x"})
    assert res.path is None and render_calls == []  # DOI ⇒ scholarly ⇒ no render
