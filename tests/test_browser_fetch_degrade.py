"""``browser_fetch`` degrades gracefully (authorized best-effort contract) when the
optional browser dep is missing, and validates %PDF magic / size before caching. No
real browser is ever launched — we mock at the ``_load_playwright`` boundary.
"""
from __future__ import annotations

from zotero_summarizer.integrations import browser_fetch


def _no_browser(monkeypatch):
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (None, None))


def test_is_available_false_when_dep_missing(monkeypatch):
    _no_browser(monkeypatch)
    assert browser_fetch.is_available() is False


def test_fetch_returns_none_when_dep_missing(tmp_path, monkeypatch):
    _no_browser(monkeypatch)
    out = browser_fetch.fetch_pdf_via_browser(
        "https://www.nature.com/x", profile_dir=tmp_path / "prof", cache_dir=tmp_path / "cache",
    )
    assert out is None  # no crash, honest None


def test_render_article_returns_none_when_dep_missing(tmp_path, monkeypatch):
    _no_browser(monkeypatch)
    out = browser_fetch.render_article_pdf(
        "https://eugeneyan.com/writing/x", cache_dir=tmp_path / "cache",
    )
    assert out is None  # no crash, honest None (falls back to no_fetchable_source)


def test_login_window_reports_unavailable_when_dep_missing(tmp_path, monkeypatch):
    _no_browser(monkeypatch)
    res = browser_fetch.open_login_window("https://lib.edu/login", tmp_path / "prof")
    assert res["ok"] is False and res["logged_in"] is False and "not installed" in res["error"]


def test_size_cap_accommodates_large_clinical_pdf():
    """Regression: a ~20.5 MB clinical/Nature PDF must pass the default cap. The old
    20 MB cap rejected a valid Nature PDF the Chrome session had already fetched (the
    fetch GOT the bytes, then dropped them for being 0.5 MB too big)."""
    from zotero_summarizer.models import QualityReviewConfig
    cap = QualityReviewConfig().max_pdf_bytes
    big = b"%PDF-1.4" + bytes(20_600_000)  # ~20.5 MB, like the real Nature Medicine paper
    assert cap >= 25_000_000
    assert browser_fetch._looks_pdf(big, max_bytes=cap) is True


def test_looks_pdf_validates_magic_and_size():
    assert browser_fetch._looks_pdf(b"%PDF-1.7\n...", max_bytes=1_000_000) is True
    assert browser_fetch._looks_pdf(b"<html>not a pdf</html>", max_bytes=1_000_000) is False
    assert browser_fetch._looks_pdf(b"", max_bytes=1_000_000) is False
    assert browser_fetch._looks_pdf(b"%PDF" + b"x" * 100, max_bytes=10) is False  # oversize


def test_cookie_reuse_off_returns_empty(monkeypatch):
    """Empty browser name = reuse off → [] without even importing browser-cookie3."""
    monkeypatch.setattr(browser_fetch, "_import_browser_cookie3",
                        lambda: pytest.fail("must not import when reuse is off"))
    assert browser_fetch._load_browser_cookies("") == []


def test_cookie_reuse_empty_when_dep_missing(monkeypatch):
    monkeypatch.setattr(browser_fetch, "_import_browser_cookie3", lambda: None)
    assert browser_fetch._load_browser_cookies("chrome") == []  # degrades, no crash


def test_cookie_reuse_empty_when_store_unreadable(monkeypatch):
    """Locked/encrypted store → [] (fall back to in-app login)."""
    import types
    def _boom():
        raise OSError("operation not permitted")
    fake_mod = types.SimpleNamespace(chrome=_boom, BrowserCookieError=RuntimeError)
    monkeypatch.setattr(browser_fetch, "_import_browser_cookie3", lambda: fake_mod)
    assert browser_fetch._load_browser_cookies("chrome") == []


def test_cookie_reuse_unknown_browser_returns_empty(monkeypatch):
    import types
    monkeypatch.setattr(browser_fetch, "_import_browser_cookie3", lambda: types.SimpleNamespace())
    assert browser_fetch._load_browser_cookies("netscape") == []  # no such loader → []


def test_cookie_dicts_maps_jar_to_playwright_format():
    import types
    jar = [
        types.SimpleNamespace(name="sess", value="abc", domain=".nature.com", path="/", secure=True, expires=99999),
        types.SimpleNamespace(name="", value="x", domain=".nature.com", path="/", secure=False, expires=None),  # no name → skip
        types.SimpleNamespace(name="y", value="z", domain="", path="/", secure=False, expires=None),  # no domain → skip
    ]
    out = browser_fetch._cookie_dicts(jar)
    assert out == [{"name": "sess", "value": "abc", "domain": ".nature.com", "path": "/", "secure": True, "expires": 99999.0}]


def test_is_logged_in_tracks_login_marker_not_cookies(tmp_path):
    """Readiness hinges on the login-complete MARKER, not a Cookies file — Chromium
    writes Cookies on any page visit, which would false-positive."""
    prof = tmp_path / "prof"
    assert browser_fetch.is_logged_in(prof) is False  # no profile yet
    # A Cookies file alone (incidental browsing) must NOT read as logged-in.
    (prof / "Default").mkdir(parents=True)
    (prof / "Default" / "Cookies").write_bytes(b"x")
    assert browser_fetch.is_logged_in(prof) is False
    # Only the explicit login-complete marker counts.
    (prof / browser_fetch._LOGIN_MARKER).write_text("", encoding="utf-8")
    assert browser_fetch.is_logged_in(prof) is True


# --- strategy-3 navigation (Cloudflare pass) ---------------------------------------
# Minimal fakes driving _drive_browser. No real browser. The point: when the cheap
# `ctx.request.get(pdf_url)` is bot-walled (non-PDF), navigating to the PDF as a REAL
# page fires the application/pdf response interceptor, and those bytes are returned.
_PDF = b"%PDF-1.7\nbody"


class _Resp:
    def __init__(self, body, *, ok=True, ctype="text/html"):
        self._body, self.ok, self.headers = body, ok, {"content-type": ctype}

    def body(self):
        return self._body


class _Req:
    def __init__(self, table):
        self._table = table  # url -> _Resp

    def get(self, url, **_kw):
        return self._table.get(url, _Resp(b"", ok=False))


class _Page:
    def __init__(self, *, meta_url=None, nav_pdf_url=None, nav_pdf_bytes=b"", dl_hrefs=None):
        self._cb = None
        self._meta_url = meta_url          # citation_pdf_url meta value (may be a redirect trap)
        self._nav_pdf_url = nav_pdf_url    # the URL whose page.goto streams application/pdf
        self._nav_pdf_bytes = nav_pdf_bytes
        self._dl_hrefs = dl_hrefs or []    # on-page "Download PDF" anchors

    def on(self, _event, cb):
        self._cb = cb

    def goto(self, url, **_kw):
        # A real navigation to nav_pdf_url streams an application/pdf response (what a page
        # nav gets but ctx.request can't); the landing / redirect-trap navs stream nothing.
        if url == self._nav_pdf_url and self._cb:
            self._cb(_Resp(self._nav_pdf_bytes, ok=True, ctype="application/pdf"))

    def query_selector(self, sel):
        return _Meta(self._meta_url) if ("citation_pdf_url" in sel and self._meta_url) else None

    def eval_on_selector_all(self, _sel, _js):
        return list(self._dl_hrefs)

    def pdf(self, **_kw):
        return b""


class _Meta:
    def __init__(self, content):
        self._c = content

    def get_attribute(self, _name):
        return self._c


class _Ctx:
    def __init__(self, req, page):
        self.request, self._page = req, page

    def add_cookies(self, _c):
        pass

    def new_page(self):
        return self._page

    def close(self):
        pass


class _PW:
    def __init__(self, ctx):
        import types
        self.launch_kwargs = {}

        def _launch(*_a, **k):
            self.launch_kwargs = k
            return ctx

        self.chromium = types.SimpleNamespace(launch_persistent_context=_launch)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_strategy3_navigates_when_api_request_is_blocked(tmp_path):
    """The declared citation_pdf_url is served behind Cloudflare: ctx.request.get returns
    a non-PDF (challenge HTML), but page.goto(pdf_url) fires the application/pdf response
    → _drive_browser returns the real PDF bytes (the navigation fix)."""
    landing = "https://www.nature.com/articles/s41746-x"
    pdf_url = "https://www.nature.com/articles/s41746-x.pdf"
    req = _Req({landing: _Resp(b"<html>landing</html>"), pdf_url: _Resp(b"<html>cf challenge</html>")})
    pw = _PW(_Ctx(req, _Page(meta_url=pdf_url, nav_pdf_url=pdf_url, nav_pdf_bytes=_PDF)))
    body = browser_fetch._drive_browser(
        lambda: pw, RuntimeError, landing, tmp_path / "prof",
        timeout=5.0, max_bytes=10_000_000, headless=True,
    )
    assert body == _PDF  # navigated to the PDF and captured it, not b""


def test_download_pdf_link_used_when_citation_meta_redirects(tmp_path):
    """CAPA(npj DM): the citation_pdf_url meta is a REDIRECT TRAP (Nature serves
    <article>.pdf as HTML). The page's real 'Download PDF' control → _reference.pdf,
    which the cookie'd request fetches. _drive_browser must follow that, not give up."""
    landing = "https://www.nature.com/articles/s41746-026-02674-7"
    trap = landing + ".pdf"                 # citation_pdf_url → 30x to HTML
    real = landing + "_reference.pdf"       # on-page "Download PDF" → the actual file
    req = _Req({
        landing: _Resp(b"<html>landing</html>"),
        trap: _Resp(b"<html>redirected to landing</html>"),   # not a PDF (the trap)
        real: _Resp(_PDF, ok=True, ctype="application/pdf"),  # the real PDF
    })
    page = _Page(meta_url=trap, nav_pdf_url=None, dl_hrefs=[real])  # nav streams nothing
    body = browser_fetch._drive_browser(
        lambda: _PW(_Ctx(req, page)), RuntimeError, landing, tmp_path / "prof",
        timeout=5.0, max_bytes=20_000_000, headless=True,
    )
    assert body == _PDF  # followed the Download-PDF link after the meta trap failed


def test_channel_and_no_viewport_threaded_to_launch(tmp_path):
    """channel='chrome' drives the REAL Chrome binary (fingerprint matches the injected
    cf_clearance) and no_viewport avoids the headless-viewport tell — both must reach
    launch_persistent_context."""
    landing = "https://www.nature.com/articles/s41746-x"
    pdf_url = landing + ".pdf"
    req = _Req({landing: _Resp(b"<html>landing</html>"), pdf_url: _Resp(b"<html>cf</html>")})
    pw = _PW(_Ctx(req, _Page(meta_url=pdf_url, nav_pdf_url=pdf_url, nav_pdf_bytes=_PDF)))
    browser_fetch._drive_browser(
        lambda: pw, RuntimeError, landing, tmp_path / "prof",
        timeout=5.0, max_bytes=10_000_000, headless=True, channel="chrome",
    )
    assert pw.launch_kwargs.get("channel") == "chrome"
    assert pw.launch_kwargs.get("no_viewport") is True
    # empty channel → bundled chromium (channel=None), not the literal ""
    pw2 = _PW(_Ctx(req, _Page(meta_url=pdf_url, nav_pdf_url=pdf_url, nav_pdf_bytes=_PDF)))
    browser_fetch._drive_browser(lambda: pw2, RuntimeError, landing, tmp_path / "p2",
                                 timeout=5.0, max_bytes=10_000_000, headless=True, channel="")
    assert pw2.launch_kwargs.get("channel") is None
