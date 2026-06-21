"""University institutional access: persistent-profile login + readiness.

A thin ``services`` wrapper over ``integrations.browser_fetch`` for the review
fleet's browser PDF acquisition. ``start_login`` opens a HEADED browser once so the
user signs into their library (SSO/2FA); the session persists in the profile and the
fleet's headless fetches reuse it. ``status`` reports whether the feature is enabled,
the browser extra is installed, and a session is present. ``profile_dir`` is the one
place the profile path is resolved (config override, else the ``data/`` default) —
shared with ``_pdf_acquire``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zotero_summarizer.integrations import browser_fetch
from zotero_summarizer.services._common import settings, state as get_state
from zotero_summarizer.services.library import _flight


def profile_dir(ua: Any) -> Path:
    """The browser profile dir: the configured override, else the app-owned default
    under ``data/`` (never hardcoded — Settings derives it)."""
    configured = str(getattr(ua, "browser_profile_dir", "") or "").strip()
    return Path(configured).expanduser() if configured else settings().browser_profile_dir


def status() -> dict[str, Any]:
    """Readiness for the Settings panel: ``{enabled, browser_available, logged_in,
    login_url, ezproxy_prefix_set}``."""
    ua = get_state().app_state.config.university_access
    return {
        "enabled": bool(ua.enabled),
        "browser_available": browser_fetch.is_available(),
        "logged_in": browser_fetch.is_logged_in(profile_dir(ua)),
        "login_url": ua.login_url,
        "ezproxy_prefix_set": bool(ua.ezproxy_prefix),
    }


def start_login() -> dict[str, Any]:
    """Launch the one-time headed login in the background; the user signs in and
    closes the window. Returns ``{started, reason?}``. Refuses when the browser
    extra is missing or a deep review is running (don't stack a browser launch on a
    model load — RAM safety)."""
    ua = get_state().app_state.config.university_access
    if not browser_fetch.is_available():
        return {"started": False,
                "reason": "browser extra not installed — run `uv pip install -e '.[browser]' && patchright install chromium`"}
    from zotero_summarizer.services.library import deep_review
    if deep_review.status().get("status") == "running":
        return {"started": False, "reason": "a deep review is running; try again in a moment"}
    prof = profile_dir(ua)
    _flight.run_in_background(lambda: browser_fetch.open_login_window(ua.login_url, prof))
    return {"started": True, "login_url": ua.login_url}


__all__ = ["profile_dir", "status", "start_login"]
