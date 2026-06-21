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
) -> Path | None:
    """Fetch ``url`` to a local PDF using the persistent browser profile; return the
    cached path or ``None``. Shares ``pdf_fetch``'s cache dir + filename scheme so a
    headless and a browser fetch of the same URL hit one cache.

    Two strategies: (1) an authenticated context request (carries the profile's
    cookies — best for a direct-PDF link behind EZproxy); (2) full navigation with
    response interception (for publishers that stream the PDF inline behind a JS /
    Cloudflare landing page). ``None`` on a missing dep, a non-PDF, or any browser
    error (authorized best-effort contract)."""
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
        body = _drive_browser(sync_playwright, error_class, url, profile_dir, timeout, max_bytes, headless)
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
) -> bytes:
    """Launch a persistent context and return the captured PDF bytes (``b''`` on any
    failure). Caught errors: the browser lib's own error class + OSError — mirrors
    ``pdf_fetch.fetch_pdf``'s narrow boundary, never a bare except."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(timeout * 1000)
    catch: tuple[type[BaseException], ...] = (OSError,) if error_class is None else (error_class, OSError)
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=headless)
            try:
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
                return captured[0] if captured else b""
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


__all__ = ["fetch_pdf_via_browser", "open_login_window", "is_logged_in", "is_available"]
