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
