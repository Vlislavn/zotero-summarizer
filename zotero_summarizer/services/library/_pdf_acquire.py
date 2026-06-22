"""Acquire a reviewable PDF for a library item, returning a LOCAL cache path.

The review fleet calls this for a pick with no Zotero attachment. It resolves the
best source and downloads to ``pdf_fetch``'s cache (NO Zotero write — so a verdict
works while Zotero is open), in this order:

    1. arXiv direct  ─┐
    2. Unpaywall OA   ├─ headless (``pdf_fetch``) — fast, no browser
    3. OpenAlex oa_url┘  (+ PMC for no-DOI PubMed papers)
    4. EZproxy / publisher ── browser (``browser_fetch``) using the university
       persistent profile / Chrome session — for SCHOLARLY paywalled papers.
    5. web-article render ── for a NON-scholarly web page (blog/Substack/news with
       HTML full text but no PDF), ``browser_fetch.render_article_pdf`` renders it to a
       PDF the PDF-only review pipeline can digest (gated on ``review_web_articles``).

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

from zotero_summarizer.integrations import browser_fetch, pdf_fetch, pubmed
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

    # --- headless rungs: arXiv → Unpaywall → PMC → OpenAlex oa_url -----------
    headless_urls: list[str] = []
    direct = pdf_fetch.resolve_pdf_url(doi=doi, arxiv_id=arxiv_id, url=url, unpaywall=app.unpaywall_client)
    if direct:
        headless_urls.append(direct)
    # PMC full text — recovers PubMed papers that are in PMC but carry NO DOI (e.g.
    # AMIA proceedings, where clinical agentic-AI work clusters); the DOI-keyed
    # Unpaywall/OpenAlex rungs can't resolve those, leaving no PDF URL to try. Fresh
    # PMC has no reliable HEADLESS route (bot-wall interstitial), so this URL is really
    # for the browser rung below — the headless attempt falls through. Reuses the cache.
    cache = getattr(app, "openalex_cache", None)
    if cache is not None:
        pmc_url = pubmed.resolve_pmc_pdf_url(
            cache=cache,
            pmid=pubmed._pmid_from_url(url),
            doi=doi,
            email=str(getattr(config.prestige, "user_agent_email", "") or ""),
        )
        if pmc_url:
            headless_urls.append(pmc_url)
    openalex = getattr(app, "openalex_client", None)
    if openalex is not None and doi:
        work = openalex.fetch_work_by_doi(doi)
        if work is not None and work.oa_url:
            headless_urls.append(work.oa_url)

    for candidate in _dedupe(headless_urls):
        path = pdf_fetch.fetch_pdf(candidate, max_bytes=qr.max_pdf_bytes, timeout=qr.fetch_timeout_secs)
        if path is not None:
            return AcquireResult(path=path)

    # A SCHOLARLY item (arXiv id or DOI) is an academic paper → the browser proxied /
    # cookie rung (paywalled access). A pure web page (no scholarly id) → the
    # web-article render rung below. Splitting on this keeps a blog out of the
    # paywall/needs_login path, and a paywalled paper out of the HTML renderer.
    scholarly = bool(arxiv_id or doi)

    # --- browser rung: proxied / publisher (Cloudflare / SSO paywall) -------
    if ua.enabled and scholarly:
        proxied = _proxied_url(ua, url, doi)
        if proxied:
            profile = _profile_dir(ua)
            # Retry the OA links via the real browser too (they may sit behind a
            # Cloudflare landing the headless client couldn't pass), then the proxied URL.
            for candidate in _dedupe([*headless_urls, proxied]):
                path = browser_fetch.fetch_pdf_via_browser(
                    candidate, profile_dir=profile, cache_dir=None,
                    timeout=ua.fetch_timeout_secs, max_bytes=qr.max_pdf_bytes, headless=ua.headless,
                    cookie_browser=str(getattr(ua, "cookie_browser", "") or ""),
                )
                if path is not None:
                    return AcquireResult(path=path)
            # A proxied/paywalled source EXISTED but the browser couldn't fetch it → the
            # session is missing/expired (or the `browser` extra is absent): the
            # actionable ``needs_library_login`` signal. (We do NOT gate on a
            # cookie-presence guess — Chromium writes cookies on any visit, which would
            # mislabel a paywall as "no source".)
            LOGGER.info("review-fleet: browser PDF fetch yielded nothing for %s → needs_library_login", item_key)
            return AcquireResult(path=None, needs_login=True)

    # --- web-article rung: a blog/Substack/news page has HTML full text but NO PDF.
    # Render the page to a PDF so the PDF-only review pipeline can digest it. Last
    # resort, only for non-scholarly web pages, gated by `review_web_articles`.
    if getattr(qr, "review_web_articles", False) and not scholarly and _is_web_article(url):
        rendered = browser_fetch.render_article_pdf(
            url, cache_dir=None, timeout=ua.fetch_timeout_secs, max_bytes=qr.max_pdf_bytes
        )
        if rendered is not None:
            return AcquireResult(path=rendered)

    return AcquireResult(path=None)


def _is_web_article(url: str) -> bool:
    """A web page whose full text is HTML (blog/Substack/news/docs) — an http(s) URL
    that is not itself a PDF. The renderer turns it into a reviewable PDF."""
    low = (url or "").strip().lower()
    return low.startswith(("http://", "https://")) and not low.endswith(".pdf")


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


__all__ = ["AcquireResult", "acquire_pdf_for"]
