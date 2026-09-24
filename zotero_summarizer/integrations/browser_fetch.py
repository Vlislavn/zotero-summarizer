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

import base64
import hashlib
import logging
import threading
from pathlib import Path
from typing import Any, Callable, NamedTuple
from urllib.parse import urljoin

from zotero_summarizer.integrations._browser_network import public_browser_options
from zotero_summarizer.integrations.app_rss import validate_rss_url

from zotero_summarizer.integrations.pdf_fetch import (
    _DEFAULT_MAX_BYTES,
    _PDF_MAGIC,
    valid_pdf_path,
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


class _BrowserLib(NamedTuple):
    """The injected playwright library pair — the ``sync_playwright`` factory and the
    browser lib's own error class always travel together, so ``_drive_browser`` takes
    them bundled instead of as two separate required params."""

    sync_playwright: Callable[[], Any]
    error_class: type[BaseException] | None


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


def _read_stream(cdp: Any, handle: str, max_bytes: int) -> bytes:
    """Read at most the limit plus one detection byte; always release the stream."""
    body = bytearray()
    try:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        while True:
            result = cdp.send("IO.read", {"handle": handle, "size": min(64_000, max_bytes - len(body) + 1)})
            chunk = base64.b64decode(result["data"], validate=True) if result.get("base64Encoded") else result["data"].encode()
            if len(body) + len(chunk) > max_bytes:
                raise ValueError("Browser response exceeds max_bytes")
            body.extend(chunk)
            if result["eof"]:
                return bytes(body)
            if not chunk:
                raise ValueError("Browser stream made no progress")
    finally:
        cdp.send("IO.close", {"handle": handle})


def _print_pdf(cdp: Any, max_bytes: int) -> bytes:
    result = cdp.send("Page.printToPDF", {
        "transferMode": "ReturnAsStream", "paperWidth": 8.27, "paperHeight": 11.7,
        "printBackground": False, "marginTop": 0, "marginBottom": 0,
        "marginLeft": 0, "marginRight": 0,
    })
    return _read_stream(cdp, result["stream"], max_bytes)


def _authenticate_proxy(cdp: Any, event: dict, proxy: dict, attempted: set[str]) -> None:
    challenge = event["authChallenge"]
    response = {"response": "CancelAuth"}
    if (challenge["source"] == "Proxy" and challenge["origin"].rstrip("/") == proxy["server"]
            and event["requestId"] not in attempted):
        attempted.add(event["requestId"])
        response = {"response": "ProvideCredentials", "username": proxy["username"], "password": proxy["password"]}
    cdp.send("Fetch.continueWithAuth", {"requestId": event["requestId"], "authChallengeResponse": response})


def _capture_response(cdp: Any, event: dict, captured: list[bytes], max_bytes: int, frame_id: str) -> None:
    request = {"requestId": event["requestId"]}
    headers = event.get("responseHeaders", [])
    is_pdf = any(h["name"].lower() == "content-type" and "application/pdf" in h["value"].lower() for h in headers)
    is_document = event.get("frameId") == frame_id and event["resourceType"] == "Document"
    code = event.get("responseStatusCode", 0)
    if code in {0, 401, 407} or 300 <= code < 400 or not (is_pdf or is_document):
        cdp.send("Fetch.continueRequest", request)
        return
    if captured:
        cdp.send("Fetch.failRequest", {**request, "errorReason": "Aborted"})
        return
    captured.append(b"")  # Reserve the single capture while synchronous CDP pumps other events.
    try:
        stream = cdp.send("Fetch.takeResponseBodyAsStream", request)
        body = _read_stream(cdp, stream["stream"], max_bytes)
    except Exception:
        captured.clear()
        cdp.send("Fetch.failRequest", {**request, "errorReason": "Aborted"})
        raise
    if 200 <= code < 300 and _looks_pdf(body, max_bytes=max_bytes):
        captured[0] = body
        cdp.send("Fetch.failRequest", {**request, "errorReason": "Aborted"})
        return
    captured.clear()
    # The stream is decoded. Preserve HTML/JS and cookies, not stale wire framing.
    headers = [h for h in headers if h["name"].lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
    cdp.send("Fetch.fulfillRequest", {
        **request, "responseCode": code, "responseHeaders": headers,
        "body": base64.b64encode(body).decode("ascii"),
    })


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
    cached path or ``None``. Shares ``pdf_fetch``'s cache dir + filename scheme so a
    headless and a browser fetch of the same URL hit one cache. ``channel`` picks the
    browser distribution (``chrome`` = the real Chrome binary, whose fingerprint matches
    an injected ``cf_clearance`` so Cloudflare publishers accept it; ``""`` = bundled
    chromium).

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

    sync_playwright, error_class = _load_playwright()
    if sync_playwright is None:
        return None

    if not _BROWSER_LOCK.acquire(blocking=False):
        LOGGER.info("browser fetch skipped: another browser session is in flight")
        return None
    try:
        body = _drive_browser(_BrowserLib(sync_playwright, error_class), url, profile_dir, timeout, max_bytes,
                               headless, cookie_browser=cookie_browser, channel=channel,
                               render_fallback=render_fallback)
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
    cache_dir: Path,
    timeout: float = 60.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Path | None:
    """Render a WEB ARTICLE (an HTML page with no PDF — blog / Substack / news / docs)
    to a PDF via headless Chromium's streamed print output, so the PDF-only review pipeline can
    digest it. Returns the cached path or ``None`` (missing dep / non-PDF).
    Navigation, transport and security errors propagate.

    Uses an EPHEMERAL context (a public page needs no session) and a cache key prefixed
    ``render:`` so it never collides with a real fetched PDF at the same URL.
    For a web article the rendered DOM IS
    the document, so this is the correct full text (unlike a publisher PDF, where
    printing would lose the real file — never use it there)."""
    if not url:
        return None
    cache_dir = cache_dir.expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = _cache_path("render:" + url, cache_dir)
    if valid_pdf_path(final_path, max_bytes=max_bytes):
        return final_path

    sync_playwright, _ = _load_playwright()
    if sync_playwright is None:
        return None
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
                page.goto(url, wait_until="load", timeout=timeout_ms)
                body = _print_pdf(ctx.new_cdp_session(page), max_bytes)
            finally:
                browser.close()
    finally:
        _BROWSER_LOCK.release()

    if not _looks_pdf(body, max_bytes=max_bytes):
        return None
    tmp_path = cache_dir / f"{final_path.stem}.tmp"
    tmp_path.write_bytes(body)
    tmp_path.replace(final_path)
    return final_path


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
) -> bytes:
    """Launch a public-only persistent context and return captured PDF bytes.
    Transport, security, DOM and cookie-injection errors propagate.

    ``render_fallback``: if the page declares NO PDF (no ``citation_pdf_url`` — i.e. it
    is web content like a Nature news/comment piece, not a real paper), render the page
    itself to a PDF. A page that DOES declare a PDF we just couldn't fetch (gated behind
    a login for THAT publisher) returns ``b''`` so the caller reports it honestly rather
    than reviewing a paywall stub."""
    sync_playwright, error_class = lib
    profile_dir.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(timeout * 1000)
    catch: tuple[type[BaseException], ...] = (OSError,) if error_class is None else (error_class, OSError)
    with public_browser_options(url, timeout) as network, sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir), channel=(channel or None), headless=headless, no_viewport=True, **network)
        try:
            if cookie_browser:
                cookies = _load_browser_cookies(cookie_browser)
                if cookies:
                    ctx.add_cookies(cookies)
            captured: list[bytes] = []
            page = ctx.new_page()
            cdp = ctx.new_cdp_session(page)
            errors: list[Exception] = []
            cdp.on("error", lambda error: errors.append(error))
            frame_id = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            attempted: set[str] = set()
            cdp.on("Fetch.authRequired", lambda event: _authenticate_proxy(cdp, event, network["proxy"], attempted))
            cdp.on("Fetch.requestPaused", lambda event: _capture_response(cdp, event, captured, max_bytes, frame_id))
            cdp.send("Fetch.enable", {"handleAuthRequests": True, "patterns": [
                {"urlPattern": "*", "requestStage": stage} for stage in ("Request", "Response")
            ]})
            candidates = [url]
            for index, target in enumerate(candidates):
                target = validate_rss_url(urljoin(url, target))
                try:
                    page.goto(target, wait_until="load", timeout=timeout_ms)
                except catch:
                    if not captured or not _looks_pdf(captured[0], max_bytes=max_bytes):
                        raise
                finally:
                    if errors:  # CDP callback errors belong to this navigation, not asyncio's log.
                        raise errors[0]
                if captured:
                    return captured[0]
                if index == 0:
                    candidates.extend(_pdf_candidates(page, url))
            if len(candidates) > 1:
                # Real PDF link(s) were DECLARED but none fetched (gated behind a login
                # for this publisher, or a hard interactive challenge). Don't render a
                # paywall stub — let the caller report "needs login" honestly.
                return b""
            # (4) no declared PDF anywhere → web content (e.g. a Nature news/comment
            # piece with a DOI). Render the page itself so it can still be reviewed.
            if render_fallback:
                return _print_pdf(cdp, max_bytes)
            return b""
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


__all__ = ["fetch_pdf_via_browser", "render_article_pdf", "open_login_window", "is_logged_in", "is_available"]
