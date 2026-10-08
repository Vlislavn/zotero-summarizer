"""Browser-driven PDF fetch for university institutional access (leaf).

For non-arXiv / paywalled papers (Cloudflare-protected like bioRxiv, or behind a
journal subscription) a headless ``httpx`` GET can't pass the challenge / SSO —
which is why ``deep_review`` normally relies on Zotero's "Find Available PDF". This
module drives a REAL browser instead, reusing a PERSISTENT profile the user logs
into once (``open_login_window``), so the EZproxy/Shibboleth/OpenAthens session and
the Cloudflare ``cf_clearance`` cookie carry across runs.

The "just import" stack: **patchright** (a drop-in patched Playwright with
undetectable CDP — passes Cloudflare managed challenges), falling back to plain
``playwright`` when patchright isn't installed. Both expose the same
``sync_playwright`` API, so the code below is identical either way.

Layering: this is an integrations LEAF — it imports only stdlib + the optional
browser lib + sibling integration constants. It takes ``profile_dir``/``cache_dir``
as arguments (a ``services`` concern resolves them from ``Settings``); it never
reaches for config or services.

An absent optional dependency or a non-PDF result returns ``None``; acquisition
errors propagate. All launched browsers use the same public-only egress boundary.
Single browser at a time (a module lock) — both for the unified-memory RAM budget
and to dodge Chromium's per-profile ``SingletonLock``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, NamedTuple
from urllib.parse import urljoin

from zotero_summarizer.integrations._browser_article import (
    article_text_limit,
    collect_article_text,
    render_text_pdf,
    validate_article_timeout,
)
from zotero_summarizer.integrations._browser_response import (
    ResponseCapture,
    install_response_capture as _install_response_capture,
    looks_pdf as _looks_pdf,
)
from zotero_summarizer.integrations._browser_network import public_browser_options
from zotero_summarizer.integrations.app_rss import validate_rss_url

from zotero_summarizer.integrations.pdf_fetch import _DEFAULT_MAX_BYTES, valid_pdf_path

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


class _BrowserLib(NamedTuple):
    """The injected playwright library pair — the ``sync_playwright`` factory and the
    browser lib's own error class always travel together, so ``_drive_browser`` takes
    them bundled instead of as two separate required params."""

    sync_playwright: Callable[[], Any]
    error_class: type[BaseException] | None


class _BrowserOutput(NamedTuple):
    """Browser bytes plus their provenance, determined at acquisition time."""

    body: bytes
    is_rendered_text: bool


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


def _cache_path(url: str, cache_dir: Path) -> Path:
    url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{url_key}.pdf"


def article_snapshot_path(url: str, cache_dir: Path) -> Path:
    """Return the distinct cache path for a text-only snapshot of this raw URL."""
    url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / "article-snapshots" / f"{url_key}.pdf"


def _write_cache(final_path: Path, body: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{final_path.name}.", suffix=".tmp", dir=final_path.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, final_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _bounded_article_text_limit(max_bytes: int) -> int:
    text_limit = article_text_limit(max_bytes)
    if type(text_limit) is not int or not 0 < text_limit <= max_bytes:
        raise ValueError("article text limit must be a positive bounded integer")
    return text_limit


def _article_text_pdf(page: Any, max_bytes: int, timeout: float) -> bytes:
    text_limit = _bounded_article_text_limit(max_bytes)
    text = collect_article_text(page, max_bytes=text_limit, timeout=timeout)
    try:
        source_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("article text contains invalid Unicode") from exc
    if source_size > text_limit:
        raise ValueError("article text exceeds max_bytes")
    return render_text_pdf(text, max_bytes=max_bytes)


def _navigate_page(page: Any, url: str, timeout_ms: int, capture: ResponseCapture,
                   error_class: type[BaseException] | None) -> None:
    prior_pdf_count = len(capture.pdf_bodies)
    prior_miss_count = len(capture.missing_pdf_statuses)
    try:
        if error_class is None:
            page.goto(url, wait_until="load", timeout=timeout_ms)
        else:
            try:
                page.goto(url, wait_until="load", timeout=timeout_ms)
            except error_class:
                has_pdf = len(capture.pdf_bodies) > prior_pdf_count and bool(capture.pdf_bodies[-1])
                has_pdf_miss = len(capture.missing_pdf_statuses) > prior_miss_count
                if not (has_pdf or has_pdf_miss):
                    raise
    finally:
        if capture.errors:
            raise capture.errors[0]


def fetch_pdf_via_browser(
    url: str,
    *,
    profile_dir: Path,
    cache_dir: Path,
    timeout: float = 60.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    headless: bool = True,
    cookie_browser: str = "",
    channel: str = "",
    render_fallback: bool = False,
) -> Path | None:
    """Fetch ``url`` to a local PDF using the persistent browser profile; return the
    cached source-PDF or opted-in text-snapshot path, or ``None``. Captured source PDFs
    share ``pdf_fetch``'s URL key; article snapshots use the separate
    ``article-snapshots/<SHA-256(raw URL)>.pdf`` path.
    ``channel`` selects the browser distribution (``chrome`` = real Chrome, whose
    fingerprint matches injected ``cf_clearance``; ``""`` = bundled Chromium).

    Navigate with the profile's cookies and intercept bounded response streams;
    follow the landing page's PDF metadata/download links when necessary. No
    APIResponse.body() or unbounded context-request buffer is used.
    When ``cookie_browser`` is set (e.g. ``chrome``), the user's existing session from
    THAT browser is injected first (no separate in-app login). ``None`` on a missing
    dep or a non-PDF. Navigation, transport and security errors propagate."""
    if not url:
        return None
    cache_dir = cache_dir.expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = _cache_path(url, cache_dir)
    if valid_pdf_path(final_path, max_bytes=max_bytes):
        return final_path
    article_path = article_snapshot_path(url, cache_dir)
    if render_fallback and valid_pdf_path(article_path, max_bytes=max_bytes):
        return article_path

    sync_playwright, error_class = _load_playwright()
    if sync_playwright is None:
        return None

    if not _BROWSER_LOCK.acquire(blocking=False):
        LOGGER.info("browser fetch skipped: another browser session is in flight")
        return None
    try:
        output = _drive_browser(_BrowserLib(sync_playwright, error_class), url, profile_dir, timeout, max_bytes,
                                headless, cookie_browser=cookie_browser, channel=channel,
                                render_fallback=render_fallback)
    finally:
        _BROWSER_LOCK.release()

    if not _looks_pdf(output.body, max_bytes=max_bytes):
        return None
    cache_path = article_path if output.is_rendered_text else final_path
    if output.is_rendered_text:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cache(cache_path, output.body)
    return cache_path


def render_article_pdf(
    url: str,
    *,
    cache_dir: Path,
    timeout: float = 60.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Path | None:
    """Capture a publisher PDF or convert a no-PDF article to bounded text-only PDF.

    Uses an ephemeral browser context. Captured publisher PDFs use the raw-URL cache;
    generated text snapshots use ``article_snapshot_path``. The raw cache is checked
    first. Missing dependencies/non-PDF responses return ``None``; navigation and
    extraction errors propagate.
    """
    if not url:
        return None
    cache_dir = cache_dir.expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_pdf_path = _cache_path(url, cache_dir)
    if valid_pdf_path(source_pdf_path, max_bytes=max_bytes):
        return source_pdf_path
    final_path = article_snapshot_path(url, cache_dir)
    if valid_pdf_path(final_path, max_bytes=max_bytes):
        return final_path

    timeout = validate_article_timeout(timeout)
    sync_playwright, error_class = _load_playwright()
    if sync_playwright is None:
        return None
    text_limit = _bounded_article_text_limit(max_bytes)
    if not _BROWSER_LOCK.acquire(blocking=False):
        LOGGER.info("article render skipped: another browser session is in flight")
        return None
    timeout_ms = int(timeout * 1000)
    try:
        with public_browser_options(url, timeout) as network, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, **network)
            try:
                ctx = browser.new_context()
                page = ctx.new_page()
                cdp = ctx.new_cdp_session(page)
                capture = _install_response_capture(
                    cdp, network, max_bytes, document_limit=text_limit,
                )
                _navigate_page(page, url, timeout_ms, capture, error_class)
                captured_pdf = bool(
                    capture.pdf_bodies and _looks_pdf(capture.pdf_bodies[0], max_bytes=max_bytes)
                )
                if captured_pdf:
                    body = capture.pdf_bodies[0]
                elif capture.missing_pdf_statuses:
                    body = b""
                else:
                    body = _article_text_pdf(page, max_bytes, timeout)
            finally:
                browser.close()
    finally:
        _BROWSER_LOCK.release()

    if not _looks_pdf(body, max_bytes=max_bytes):
        return None
    cache_path = source_pdf_path if captured_pdf else final_path
    if not captured_pdf:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cache(cache_path, body)
    return cache_path


# JS that returns the page's PDF links: anchors whose text is "Download PDF" (the real
# control) OR whose href ends in .pdf. Authoritative when the citation_pdf_url meta is a
# redirect trap (Nature serves <article>.pdf as HTML but the button → _reference.pdf).
_PDF_LINK_JS = (
    "els => els.filter(e => /download\\s*(the\\s*)?pdf/i.test(e.textContent||'') "
    "|| (e.href||'').split('?')[0].toLowerCase().endsWith('.pdf')).map(e => e.href).filter(Boolean)"
)


def _pdf_candidates(page: Any, landing_url: str) -> list[str]:
    """Ordered, de-duped PDF links to try for a landing page: the ``citation_pdf_url``
    meta first, then the page's on-page "Download PDF" anchors (the meta can 30x to HTML).
    Excludes the landing URL itself. DOM failures propagate."""
    out: list[str] = []
    meta = page.query_selector("meta[name='citation_pdf_url']")
    content = meta.get_attribute("content") if meta else None
    if content:
        out.append(content)
    out.extend(page.eval_on_selector_all("a", _PDF_LINK_JS) or [])
    seen: set[str] = set()
    deduped: list[str] = []
    for u in out:
        if u and u != landing_url and u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _drive_browser(
    lib: _BrowserLib,
    url: str,
    profile_dir: Path,
    timeout: float,
    max_bytes: int,
    headless: bool,
    *,
    cookie_browser: str = "",
    channel: str = "",
    render_fallback: bool = False,
) -> _BrowserOutput:
    """Launch a public-only persistent context and return bytes with source identity.
    Transport, security, DOM and cookie-injection errors propagate.

    ``render_fallback``: if the page declares NO PDF, extract bounded DOM text and pass
    it to the text-only PDF writer. A declared but unavailable PDF returns no bytes so
    the caller reports it honestly rather than reviewing a stub."""
    sync_playwright, error_class = lib
    profile_dir.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(timeout * 1000)
    with public_browser_options(url, timeout) as network, sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir), channel=(channel or None), headless=headless, no_viewport=True, **network)
        try:
            if cookie_browser:
                cookies = _load_browser_cookies(cookie_browser)
                if cookies:
                    ctx.add_cookies(cookies)
            page = ctx.new_page()
            cdp = ctx.new_cdp_session(page)
            capture = _install_response_capture(cdp, network, max_bytes)
            candidates = [url]
            for index, target in enumerate(candidates):
                target = validate_rss_url(urljoin(url, target))
                _navigate_page(page, target, timeout_ms, capture, error_class)
                if capture.pdf_bodies:
                    return _BrowserOutput(capture.pdf_bodies[0], is_rendered_text=False)
                if index == 0 and not capture.missing_pdf_statuses:
                    candidates.extend(_pdf_candidates(page, url))
            if capture.missing_pdf_statuses or len(candidates) > 1:
                # Real PDF link(s) were DECLARED but none fetched (gated behind a login
                # for this publisher, or a hard interactive challenge). Don't render a
                # paywall stub — let the caller report "needs login" honestly.
                return _BrowserOutput(b"", is_rendered_text=False)
            # A no-PDF article can still be reviewed from bounded DOM text.
            if render_fallback:
                return _BrowserOutput(_article_text_pdf(page, max_bytes, timeout), is_rendered_text=True)
            return _BrowserOutput(b"", is_rendered_text=False)
        finally:
            ctx.close()  # flushes the persistent profile's cookies to disk


def open_login_window(login_url: str, profile_dir: Path, *, channel: str = "",
                      timeout: float = _LOGIN_TIMEOUT_SECS) -> dict[str, Any]:
    """Open a HEADED browser on ``login_url`` so the user logs into their library
    (SSO/2FA) once; the session persists in ``profile_dir``. Blocks until the user
    closes the window (or ``timeout``), then flushes cookies. ``channel`` must match the
    fetch's channel (``chrome``) so ``cf_clearance`` is earned by the SAME binary that
    later fetches. Returns ``{ok, logged_in, error}``."""
    sync_playwright, error_class = _load_playwright()
    if sync_playwright is None:
        return {"ok": False, "logged_in": False, "error": "browser extra not installed (patchright)"}
    if not _BROWSER_LOCK.acquire(blocking=False):
        return {"ok": False, "logged_in": is_logged_in(profile_dir), "error": "another browser session is in flight"}
    profile_dir.mkdir(parents=True, exist_ok=True)
    catch: tuple[type[BaseException], ...] = (OSError,) if error_class is None else (error_class, OSError)
    try:
        with public_browser_options(login_url, timeout) as network, sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(profile_dir), channel=(channel or None), headless=False, no_viewport=True, **network)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if login_url:
                    page.goto(login_url, wait_until="load", timeout=int(min(timeout, 120.0) * 1000))
                # Wait for the user to finish + close the window (pages drop to 0).
                page.wait_for_event("close", timeout=int(timeout * 1000))
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


__all__ = [
    "article_snapshot_path", "fetch_pdf_via_browser", "render_article_pdf",
    "open_login_window", "is_logged_in", "is_available",
]
