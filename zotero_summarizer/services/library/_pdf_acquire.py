"""Acquire a reviewable PDF for a library item, returning a LOCAL cache path.

The review fleet calls this for a pick with no Zotero attachment. It resolves the
best source and downloads to ``pdf_fetch``'s cache (NO Zotero write — so a verdict
works while Zotero is open), in this order:

    1. arXiv direct  ─┐
    2. Unpaywall OA   ├─ headless (``pdf_fetch``) — fast, no browser
    3. OpenAlex oa_url┘
    4. EZproxy / publisher ── browser (``browser_fetch``) using the university
       persistent profile — the only rung that passes Cloudflare / SSO paywalls.

Returns ``AcquireResult(path, needs_login)``: ``needs_login`` is True when a
proxied/publisher source WAS available but the browser couldn't fetch it because the
``browser`` extra is missing or the profile isn't logged in — the fleet surfaces
that as the actionable ``needs_library_login`` outcome (vs ``no_fetchable_source``).

Layering: a ``services`` module — it reads app state via ``get_state()`` and calls
the ``integrations`` leaves (``pdf_fetch``/``browser_fetch``). It never writes Zotero.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_summarizer.integrations import browser_fetch, pdf_fetch
from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi
from zotero_summarizer.services._common import LOGGER, state as get_state
from zotero_summarizer.services.library.university_access import profile_dir as _profile_dir


@dataclass(slots=True)
class AcquireResult:
    path: Path | None
    needs_login: bool = False


def _proxied_url(ua: Any, url: str, doi: str) -> str:
    """The institutional URL to drive in the browser: the publisher ``url`` (or a
    ``doi.org`` resolver link), optionally behind the EZproxy prefix. Empty when there
    is no target at all. For SSO/OpenAthens (no prefix) the persisted session carries
    access, so the bare target is correct."""
    target = url or (f"https://doi.org/{doi}" if doi else "")
    if not target:
        return ""
    prefix = str(getattr(ua, "ezproxy_prefix", "") or "").strip()
    return f"{prefix}{target}" if prefix else target


def acquire_pdf_for(item_key: str, detail: dict[str, Any]) -> AcquireResult:
    """Resolve + download a PDF for ``item_key`` to the local cache. ``detail`` is the
    Zotero item detail (``url``/``doi``/``has_pdf``)."""
    app = get_state()
    config = app.app_state.config
    qr = config.quality_review
    ua = config.university_access
    url = str(detail.get("url") or "")
    doi = str(detail.get("doi") or "")
    arxiv_id = _arxiv_id_from_url_or_doi(url, doi)

    # --- headless rungs: arXiv → Unpaywall → OpenAlex oa_url ----------------
    headless_urls: list[str] = []
    direct = pdf_fetch.resolve_pdf_url(doi=doi, arxiv_id=arxiv_id, url=url, unpaywall=app.unpaywall_client)
    if direct:
        headless_urls.append(direct)
    openalex = getattr(app, "openalex_client", None)
    if openalex is not None and doi:
        work = openalex.fetch_work_by_doi(doi)
        if work is not None and work.oa_url:
            headless_urls.append(work.oa_url)

    for candidate in _dedupe(headless_urls):
        path = pdf_fetch.fetch_pdf(candidate, max_bytes=qr.max_pdf_bytes, timeout=qr.fetch_timeout_secs)
        if path is not None:
            return AcquireResult(path=path)

    # --- browser rung: proxied / publisher (Cloudflare / SSO paywall) -------
    proxied = _proxied_url(ua, url, doi)
    if not (ua.enabled and proxied):
        return AcquireResult(path=None)

    profile = _profile_dir(ua)
    # Retry the OA links via the real browser too (they may sit behind a Cloudflare
    # landing the headless client couldn't pass), then the proxied publisher URL.
    for candidate in _dedupe([*headless_urls, proxied]):
        path = browser_fetch.fetch_pdf_via_browser(
            candidate, profile_dir=profile, cache_dir=None,
            timeout=ua.fetch_timeout_secs, max_bytes=qr.max_pdf_bytes, headless=ua.headless,
        )
        if path is not None:
            return AcquireResult(path=path)

    # A proxied/paywalled source EXISTED but the browser couldn't fetch it → the
    # session is missing/expired (or the `browser` extra is absent): the actionable
    # ``needs_library_login`` signal. (We do NOT gate on a cookie-presence guess —
    # Chromium writes cookies on any visit, so that mislabels a paywall as "no source".)
    LOGGER.info("review-fleet: browser PDF fetch yielded nothing for %s → needs_library_login", item_key)
    return AcquireResult(path=None, needs_login=True)


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


__all__ = ["AcquireResult", "acquire_pdf_for"]
