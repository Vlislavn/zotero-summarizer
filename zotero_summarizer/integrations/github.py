"""Validate a GitHub repo URL: does it resolve, and what does it describe?

A keyless, host-locked leaf used by ``services.library._code_link`` to confirm a
code-availability link found in a paper's full text actually points at a LIVE
repository, and to read the repo's own description so the caller can judge
whether it matches the paper. We scrape the public repo HTML page rather than the
REST API on purpose: the unauthenticated API caps at 60 req/hr, which a batch
("review cool papers") fleet run would exhaust, while the HTML page is not metered
the same way and carries the OpenGraph description/title we need in the SAME
request that proves liveness.

Host is hard-locked to ``github.com`` — every URL the caller passes comes from a
github.com-only regex, and pinning it here is the indirect-prompt-injection / SSRF
guard so paper text can never steer an outbound request elsewhere.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass

import httpx

_BASE = "https://github.com"
# Best-effort enrichment on a background path — a short connect+read budget for one
# repo page. Named constant + env override, never a bare wall-clock literal.
_DEFAULT_TIMEOUT = 6.0
# A realistic UA — github serves a stripped page (no OG tags) to an empty UA.
_UA = "Mozilla/5.0 (compatible; zotero-summarizer/1.0; +https://github.com)"
_OG_DESC_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', re.I
)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', re.I
)


@dataclass(frozen=True)
class RepoCheck:
    """Outcome of one repo-existence probe.

    ``exists`` is tri-state: ``True`` (HTTP 200), ``False`` (404/410 — dead /
    renamed-away / private), or ``None`` (network error / unexpected status /
    offline — UNKNOWN, never asserted as dead). ``description``/``title`` come from
    the repo page's OpenGraph tags (empty when absent)."""

    exists: bool | None
    status: int | None
    description: str = ""
    title: str = ""


def _timeout() -> float:
    raw = (os.getenv("ZS_GITHUB_TIMEOUT") or "").strip()
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT
    except ValueError:
        return _DEFAULT_TIMEOUT


def _og(pattern: re.Pattern[str], body: str) -> str:
    m = pattern.search(body)
    return html.unescape(m.group(1).strip()) if m else ""


def check_repo(owner: str, repo: str, *, timeout: float | None = None) -> RepoCheck:
    """Probe ``github.com/{owner}/{repo}``. Read-only, host-locked, never raises —
    a transport error degrades to ``exists=None`` (unknown), not a crash."""
    url = f"{_BASE}/{owner}/{repo}"
    try:
        resp = httpx.get(
            url, follow_redirects=True, timeout=timeout or _timeout(),
            headers={"User-Agent": _UA, "Accept": "text/html"},
        )
    except httpx.HTTPError:
        return RepoCheck(exists=None, status=None)
    if resp.status_code == 200:
        body = resp.text
        return RepoCheck(
            exists=True, status=200,
            description=_og(_OG_DESC_RE, body), title=_og(_OG_TITLE_RE, body),
        )
    if resp.status_code in (404, 410):
        return RepoCheck(exists=False, status=resp.status_code)
    return RepoCheck(exists=None, status=resp.status_code)


__all__ = ["RepoCheck", "check_repo"]
