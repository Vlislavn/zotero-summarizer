"""Browser-driven PDF fetch for university institutional access (leaf).

For non-arXiv / paywalled papers (Cloudflare-protected like bioRxiv, or behind a
journal subscription) a headless ``httpx`` GET can't pass the challenge / SSO —
which is why ``deep_review`` normally relies on Zotero's "Find Available PDF". This
module drives a REAL browser instead, reusing a PERSISTENT profile the user logs
into once (``open_login_window``), so the EZproxy/Shibboleth/OpenAthens session and
the Cloudflare ``cf_clearance`` cookie carry across runs.

SOTA "just import" stack: **patchright** (a drop-in patched Playwright with
undetectable CDP — passes Cloudflare managed challenges), falling back to plain
``playwright`` when patchright isn't installed. Both expose the same
``sync_playwright`` API, so the code below is identical either way.

Layering: this is an integrations LEAF — it imports only stdlib + the optional
browser lib + sibling integration constants. It takes ``profile_dir``/``cache_dir``
as arguments (a ``services`` concern resolves them from ``Settings``); it never
reaches for config or services.

Best-effort by contract (authorized: the user opted into browser automation and the
review-fleet reports an honest per-item outcome): a missing browser dependency, a
not-logged-in profile, or a fetch failure returns ``None`` — the caller degrades to
``needs_library_login`` / ``no_fetchable_source`` rather than crashing. Single
browser at a time (a module lock) — both for the unified-memory RAM budget and to
dodge Chromium's per-profile ``SingletonLock``.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.integrations.pdf_fetch import (
    _DEFAULT_CACHE_DIR,
    _DEFAULT_MAX_BYTES,
    _PDF_MAGIC,
)

LOGGER = logging.getLogger(__name__)

# One browser process at a time: RAM safety on the unified-memory Mac AND Chromium
# refuses to open a profile already held by another process (SingletonLock).
_BROWSER_LOCK = threading.Lock()
# Generous one-time login budget (SSO + 2FA is interactive) — distinct from the
# per-fetch timeout. Named constant, not a magic literal sprinkled inline.
_LOGIN_TIMEOUT_SECS = 600.0
# Marker written when the user completes the headed login flow. We use it (NOT the
# mere presence of a Cookies file, which Chromium writes on ANY page visit) as the
# "has a session been established?" signal for the Settings readiness panel.
_LOGIN_MARKER = ".zs_login_complete"


def _load_playwright() -> tuple[Callable[[], Any] | None, type[BaseException] | None]:
    """Return ``(sync_playwright, PlaywrightError)`` from patchright (preferred) or
    playwright, or ``(None, None)`` when neither is installed. The error class lets
    callers catch browser failures narrowly (no bare ``except``)."""
    for module_name in ("patchright.sync_api", "playwright.sync_api"):
        try:
            module = __import__(module_name, fromlist=["sync_playwright", "Error"])
        except ImportError:
            continue
        return module.sync_playwright, module.Error
    LOGGER.info("browser fetch unavailable: install the optional `browser` extra (patchright)")
    return None, None


def _import_browser_cookie3() -> Any:
    """Return the ``browser_cookie3`` module, or ``None`` when the optional dep is
    absent (Safari-cookie reuse simply degrades to the in-app login)."""
    try:
        import browser_cookie3
    except ImportError:
        LOGGER.info("Safari-cookie reuse unavailable: install the optional `browser` extra (browser-cookie3)")
        return None
    return browser_cookie3


def _cookie_dicts(jar: Any) -> list[dict[str, Any]]:
    """Convert a ``http.cookiejar`` jar to Playwright ``add_cookies`` dicts (domain+path
    form). Skips entries with no name/domain."""
    out: list[dict[str, Any]] = []
    for c in jar:
        if not getattr(c, "name", "") or not getattr(c, "domain", ""):
            continue
        cookie: dict[str, Any] = {
            "name": c.name, "value": c.value or "",
            "domain": c.domain, "path": c.path or "/", "secure": bool(c.secure),
        }
        if getattr(c, "expires", None):
            cookie["expires"] = float(c.expires)
        out.append(cookie)
    return out


def _load_browser_cookies(browser: str) -> list[dict[str, Any]]:
    """The user's cookies from ``browser`` (e.g. ``chrome``/``firefox``) as Playwright
    ``add_cookies`` dicts, or ``[]`` when reuse is off, the dep/browser is unavailable,
    or the store can't be read. Best-effort by contract — the user opted into
    browser-session reuse and the in-app login is the fallback (a read failure must not
    crash the fetch). NOTE: ``safari`` is unreadable on macOS 15+/26 (hardened
    container, even with Full Disk Access) → returns ``[]``."""
    name = (browser or "").strip().lower()
    if not name:
        return []
    module = _import_browser_cookie3()
    if module is None:
        return []
    loader = getattr(module, name, None)
    if loader is None:
        LOGGER.info("cookie reuse: %r is not a browser browser-cookie3 supports", name)
        return []
    err_cls = getattr(module, "BrowserCookieError", None)
    catch: tuple[type[BaseException], ...] = (OSError,) if err_cls is None else (OSError, err_cls)
    try:
        jar = loader()
    except catch as exc:
        LOGGER.info("%s cookies unreadable: %s", name, exc)
        return []
    return _cookie_dicts(jar)


def _looks_pdf(body: bytes, *, max_bytes: int) -> bool:
    return bool(body) and len(body) <= max_bytes and body[: len(_PDF_MAGIC)] == _PDF_MAGIC


def _cache_path(url: str, cache_dir: Path) -> Path:
    url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{url_key}.pdf"


def fetch_pdf_via_browser(
    url: str,
    *,
    profile_dir: Path,
    cache_dir: Path | None = None,
    timeout: float = 60.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    headless: bool = True,
    cookie_browser: str = "",
    render_fallback: bool = False,
) -> Path | None:
    """Fetch ``url`` to a local PDF using the persistent browser profile; return the
    cached path or ``None``. Shares ``pdf_fetch``'s cache dir + filename scheme so a
    headless and a browser fetch of the same URL hit one cache.

    Three strategies: (1) an authenticated context request (carries the profile's
    cookies — best for a direct-PDF link behind EZproxy); (2) full navigation with
    response interception (publishers that stream the PDF inline); (3) for a landing
    page that didn't stream one, follow its ``citation_pdf_url`` meta (the Highwire tag
    Nature/Springer/Elsevier/Wiley expose) and fetch THAT through the cookie'd context.
    When ``cookie_browser`` is set (e.g. ``chrome``), the user's existing session from
    THAT browser is injected first (no separate in-app login). ``None`` on a missing
    dep, a non-PDF, or any browser error (authorized best-effort contract)."""
    if not url:
        return None
    cache_dir = (cache_dir or _DEFAULT_CACHE_DIR).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = _cache_path(url, cache_dir)
    if final_path.exists() and final_path.stat().st_size > 0:
        return final_path

    sync_playwright, error_class = _load_playwright()
    if sync_playwright is None:
        return None

    if not _BROWSER_LOCK.acquire(blocking=False):
        LOGGER.info("browser fetch skipped: another browser session is in flight")
        return None
    try:
        body = _drive_browser(sync_playwright, error_class, url, profile_dir, timeout, max_bytes, headless,
                              cookie_browser=cookie_browser, render_fallback=render_fallback)
    finally:
        _BROWSER_LOCK.release()

    if not _looks_pdf(body, max_bytes=max_bytes):
        return None
    tmp_path = cache_dir / f"{final_path.stem}.tmp"
    tmp_path.write_bytes(body)
    tmp_path.replace(final_path)
    return final_path


def render_article_pdf(
    url: str,
    *,
    cache_dir: Path | None = None,
    timeout: float = 60.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Path | None:
    """Render a WEB ARTICLE (an HTML page with no PDF — blog / Substack / news / docs)
    to a PDF via headless Chromium ``page.pdf()``, so the PDF-only review pipeline can
    digest it. Returns the cached path or ``None`` (missing dep / nav failure / non-PDF)
    — best-effort, same contract as ``fetch_pdf_via_browser``.

    Uses an EPHEMERAL context (a public page needs no session) and a cache key prefixed
    ``render:`` so it never collides with a real fetched PDF at the same URL. ``page.pdf``
    is Chromium-headless only — exactly our stack. For a web article the rendered DOM IS
    the document, so this is the correct full text (unlike a publisher PDF, where
    ``page.pdf`` would lose the real file — never use it there)."""
    if not url:
        return None
    cache_dir = (cache_dir or _DEFAULT_CACHE_DIR).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = _cache_path("render:" + url, cache_dir)
    if final_path.exists() and final_path.stat().st_size > 0:
        return final_path

    sync_playwright, error_class = _load_playwright()
    if sync_playwright is None:
        return None
    if not _BROWSER_LOCK.acquire(blocking=False):
        LOGGER.info("article render skipped: another browser session is in flight")
        return None
    catch: tuple[type[BaseException], ...] = (OSError,) if error_class is None else (error_class, OSError)
    timeout_ms = int(timeout * 1000)
    body = b""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context().new_page()
                page.goto(url, wait_until="load", timeout=timeout_ms)
                body = page.pdf(format="A4", print_background=False)
            finally:
                browser.close()
    except catch as exc:
        LOGGER.info("article render failed for %s: %s", url, exc)
        body = b""
    finally:
        _BROWSER_LOCK.release()

    if not _looks_pdf(body, max_bytes=max_bytes):
        return None
    tmp_path = cache_dir / f"{final_path.stem}.tmp"
    tmp_path.write_bytes(body)
    tmp_path.replace(final_path)
    return final_path


def _drive_browser(
    sync_playwright: Callable[[], Any],
    error_class: type[BaseException] | None,
    url: str,
    profile_dir: Path,
    timeout: float,
    max_bytes: int,
    headless: bool,
    *,
    cookie_browser: str = "",
    render_fallback: bool = False,
) -> bytes:
    """Launch a persistent context and return the captured PDF bytes (``b''`` on any
    failure). Caught errors: the browser lib's own error class + OSError — mirrors
    ``pdf_fetch.fetch_pdf``'s narrow boundary, never a bare except.

    ``render_fallback``: if the page declares NO PDF (no ``citation_pdf_url`` — i.e. it
    is web content like a Nature news/comment piece, not a real paper), render the page
    itself to a PDF. A page that DOES declare a PDF we just couldn't fetch (gated behind
    a login for THAT publisher) returns ``b''`` so the caller reports it honestly rather
    than reviewing a paywall stub."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(timeout * 1000)
    catch: tuple[type[BaseException], ...] = (OSError,) if error_class is None else (error_class, OSError)
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=headless)
            try:
                if cookie_browser:
                    cookies = _load_browser_cookies(cookie_browser)
                    if cookies:
                        try:
                            ctx.add_cookies(cookies)  # bring the user's live browser session
                            LOGGER.info("injected %d %s cookies into the fetch context", len(cookies), cookie_browser)
                        except catch as exc:  # a malformed cookie must not kill the fetch
                            LOGGER.info("%s cookie injection failed (continuing without): %s", cookie_browser, exc)
                # (1) authenticated direct request — cheapest, carries cookies.
                resp = ctx.request.get(url, timeout=timeout_ms)
                if resp.ok:
                    body = resp.body()
                    if _looks_pdf(body, max_bytes=max_bytes):
                        return body
                # (2) navigate + intercept the first application/pdf response.
                captured: list[bytes] = []

                def _on_response(response: Any) -> None:
                    if captured:
                        return
                    content_type = (response.headers or {}).get("content-type", "")
                    if "application/pdf" not in content_type.lower():
                        return
                    try:
                        captured.append(response.body())
                    except catch as exc:
                        LOGGER.debug("browser: could not read PDF response body: %s", exc)

                page = ctx.new_page()
                page.on("response", _on_response)
                page.goto(url, wait_until="load", timeout=timeout_ms)
                if captured:
                    return captured[0]
                # (3) a landing page that didn't stream a PDF: follow the publisher's
                # declared PDF link. ``citation_pdf_url`` is the Highwire/Google-Scholar
                # meta tag Nature/Springer/Elsevier/Wiley/etc. expose; fetch it through
                # the SAME cookie'd context so the institutional session carries.
                meta = page.query_selector("meta[name='citation_pdf_url']")
                pdf_url = meta.get_attribute("content") if meta else None
                if pdf_url and pdf_url != url:
                    resp2 = ctx.request.get(pdf_url, timeout=timeout_ms)
                    if resp2.ok and _looks_pdf(resp2.body(), max_bytes=max_bytes):
                        return resp2.body()
                    # A real PDF is DECLARED but we couldn't fetch it (gated behind a
                    # login for this publisher). Don't render a paywall stub — let the
                    # caller report "needs login for this publisher" honestly.
                    return b""
                # (4) no declared PDF → this is web content (e.g. a Nature news/comment
                # piece with a DOI). Render the page itself so it can still be reviewed.
                if render_fallback:
                    return page.pdf(format="A4", print_background=False)
                return b""
            finally:
                ctx.close()  # flushes the persistent profile's cookies to disk
    except catch as exc:
        LOGGER.info("browser fetch failed for %s: %s", url, exc)
        return b""


def open_login_window(login_url: str, profile_dir: Path, *, timeout: float = _LOGIN_TIMEOUT_SECS) -> dict[str, Any]:
    """Open a HEADED browser on ``login_url`` so the user logs into their library
    (SSO/2FA) once; the session persists in ``profile_dir``. Blocks until the user
    closes the window (or ``timeout``), then flushes cookies. Returns
    ``{ok, logged_in, error}``."""
    sync_playwright, error_class = _load_playwright()
    if sync_playwright is None:
        return {"ok": False, "logged_in": False, "error": "browser extra not installed (patchright)"}
    if not _BROWSER_LOCK.acquire(blocking=False):
        return {"ok": False, "logged_in": is_logged_in(profile_dir), "error": "another browser session is in flight"}
    profile_dir.mkdir(parents=True, exist_ok=True)
    catch: tuple[type[BaseException], ...] = (OSError,) if error_class is None else (error_class, OSError)
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=False)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if login_url:
                    page.goto(login_url, wait_until="load", timeout=int(min(timeout, 120.0) * 1000))
                # Wait for the user to finish + close the window (pages drop to 0).
                page.wait_for_event("close", timeout=int(timeout * 1000))
            except catch as exc:
                LOGGER.info("login window closed/timed out: %s", exc)
            finally:
                ctx.close()  # flush cookies
        # Mark that the user ran the connect flow (the session now lives in the
        # profile). Not a guarantee it's still valid — an expired session resurfaces
        # honestly as a failed fetch → needs_library_login.
        (Path(profile_dir) / _LOGIN_MARKER).write_text("", encoding="utf-8")
        return {"ok": True, "logged_in": is_logged_in(profile_dir), "error": ""}
    except catch as exc:
        return {"ok": False, "logged_in": is_logged_in(profile_dir), "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _BROWSER_LOCK.release()


def is_available() -> bool:
    """True when a browser automation lib (patchright/playwright) is importable."""
    sync_playwright, _ = _load_playwright()
    return sync_playwright is not None


def is_logged_in(profile_dir: Path) -> bool:
    """Readiness for the Settings panel: has the user completed the headed login flow
    (the `_LOGIN_MARKER` written by `open_login_window`)? NOT a Cookies-file check —
    Chromium writes Cookies on any page visit, so that false-positives. Not a
    guarantee the session is still valid (it can expire) — a stale session surfaces
    honestly as a failed fetch → `needs_library_login`."""
    return (Path(profile_dir) / _LOGIN_MARKER).exists()


__all__ = ["fetch_pdf_via_browser", "render_article_pdf", "open_login_window", "is_logged_in", "is_available"]
