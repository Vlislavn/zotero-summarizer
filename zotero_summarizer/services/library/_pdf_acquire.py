"""Acquire a reviewable PDF into the local cache, never writing Zotero.

Order: arXiv → Unpaywall → PMC → OpenAlex → optional university browser/web render.
The returned path carries source provenance and a typed failure outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_summarizer.integrations import browser_fetch, pdf_fetch, pubmed
from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi
from zotero_summarizer.services._common import LOGGER, state as get_state
from zotero_summarizer.services.library.university_access import profile_dir as _profile_dir
from zotero_summarizer.settings import offline_requested


@dataclass(slots=True)
class AcquireResult:
    path: Path | None
    needs_login: bool = False
    login_url: str = ""
    web_article: bool = False
    source: str = ""
    source_url: str = ""
    outcome: str = "no_oa_source"


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


def acquire_for_item(item_key: str, reader: Any = None) -> AcquireResult:
    """Read the item's detail and acquire a reviewable PDF — the single-key entry
    point for the per-paper deep-review path (the fleet inlines the equivalent over a
    batch). ``reader`` overrides the resolved library reader so the in-place Today path
    can resolve a feed candidate by ``stable_feed_key``. ``AcquireResult(path=None)``
    when it already has a local PDF (``deep_review`` will use that) or nothing fetchable."""
    from zotero_summarizer.services.zotero.zotero import get_library_reader
    detail = (reader or get_library_reader()).get_item_detail(item_key) or {}
    if detail.get("has_pdf"):
        return AcquireResult(path=None)
    return acquire_pdf_for(item_key, detail, allow_headed_fallback=True)


def _headless_sources(app: Any, config: Any, url: str, doi: str, arxiv_id: str) -> list[tuple[str, str]]:
    """Ordered OA candidates with the resolver rung that produced each URL."""
    sources: list[tuple[str, str]] = []
    direct = pdf_fetch.resolve_pdf_url(
        doi=doi, arxiv_id=arxiv_id, url=url,
        unpaywall=None if offline_requested() else app.unpaywall_client,
    )
    if direct:
        source = "arxiv" if arxiv_id else ("direct" if direct == url else "unpaywall")
        sources.append((source, direct))
    cache = None if offline_requested() else getattr(app, "openalex_cache", None)
    if cache is not None:
        pmc_url = pubmed.resolve_pmc_pdf_url(
            cache=cache, pmid=pubmed._pmid_from_url(url), doi=doi,
            email=str(getattr(config.prestige, "user_agent_email", "") or ""),
        )
        if pmc_url:
            sources.append(("pmc", pmc_url))
    openalex = None if offline_requested() else getattr(app, "openalex_client", None)
    if openalex is not None and doi:
        work = openalex.fetch_work_by_doi(doi)
        if work is not None and work.oa_url:
            sources.append(("openalex", work.oa_url))
    return _dedupe_sources(sources)


def _browser_acquire(
    item_key: str, sources: list[tuple[str, str]], url: str, doi: str,
    scholarly: bool, allow_headed_fallback: bool,
) -> AcquireResult:
    """Browser/paywall and web-article rungs for an explicit review action."""
    config = get_state().app_state.config
    qr, ua = config.quality_review, config.university_access
    proxied = _proxied_url(ua, url, doi)
    web_articles = bool(getattr(qr, "review_web_articles", False))
    if ua.enabled and scholarly and proxied:
        profile = _profile_dir(ua)
        cb = str(getattr(ua, "cookie_browser", "") or "")
        channel = str(getattr(ua, "browser_channel", "") or "")
        for source, candidate in _dedupe_sources([*sources, ("browser", proxied)]):
            path = browser_fetch.fetch_pdf_via_browser(
                candidate, profile_dir=profile, cache_dir=None,
                timeout=ua.fetch_timeout_secs, max_bytes=qr.max_pdf_bytes, headless=ua.headless,
                cookie_browser=cb, channel=channel,
                render_fallback=(web_articles and candidate == proxied),
            )
            if path is not None:
                return AcquireResult(
                    path=path, source=source, source_url=candidate,
                    outcome=f"acquired_{source}",
                )
        if allow_headed_fallback and ua.headless:
            path = browser_fetch.fetch_pdf_via_browser(
                proxied, profile_dir=profile, cache_dir=None,
                timeout=ua.fetch_timeout_secs, max_bytes=qr.max_pdf_bytes, headless=False,
                cookie_browser=cb, channel=channel, render_fallback=web_articles,
            )
            if path is not None:
                return AcquireResult(
                    path=path, source="browser", source_url=proxied,
                    outcome="acquired_browser",
                )
        LOGGER.info("browser PDF fetch yielded nothing for %s → needs_library_login", item_key)
        return AcquireResult(path=None, needs_login=True, login_url=proxied, outcome="needs_login")
    if web_articles and not scholarly and _is_web_article(url):
        rendered = browser_fetch.render_article_pdf(
            url, cache_dir=None, timeout=ua.fetch_timeout_secs, max_bytes=qr.max_pdf_bytes
        )
        if rendered is not None:
            return AcquireResult(
                path=rendered, web_article=True, source="web_article",
                source_url=url, outcome="acquired_web_article",
            )
    return AcquireResult(path=None, outcome="fetch_failed" if sources else "no_oa_source")


def acquire_pdf_for(
    item_key: str, detail: dict[str, Any], *, allow_headed_fallback: bool = False,
    allow_browser: bool = True,
) -> AcquireResult:
    """Resolve + download a PDF for ``item_key`` to the local cache. ``detail`` is the
    Zotero item detail (``url``/``doi``/``has_pdf``). ``allow_headed_fallback`` lets the
    interactive per-paper path retry the publisher landing once with a VISIBLE browser
    when the headless attempt is bot-walled (the fleet leaves it off)."""
    app = get_state()
    config = app.app_state.config
    qr = config.quality_review
    ua = config.university_access
    url = str(detail.get("url") or "")
    doi = str(detail.get("doi") or "")
    arxiv_id = _arxiv_id_from_url_or_doi(url, doi)

    sources = _headless_sources(app, config, url, doi, arxiv_id)
    for source, candidate in sources:
        path = pdf_fetch.fetch_pdf(candidate, max_bytes=qr.max_pdf_bytes, timeout=qr.fetch_timeout_secs)
        if path is not None:
            return AcquireResult(
                path=path, source=source, source_url=candidate, outcome=f"acquired_{source}"
            )

    if offline_requested():
        return AcquireResult(path=None, outcome="offline_uncached")

    # A SCHOLARLY item (arXiv id or DOI) is an academic paper → the browser proxied /
    # cookie rung (paywalled access). A pure web page (no scholarly id) → the
    # web-article render rung below. Splitting on this keeps a blog out of the
    # paywall/needs_login path, and a paywalled paper out of the HTML renderer.
    scholarly = bool(arxiv_id or doi)
    proxied = _proxied_url(ua, url, doi)
    if not allow_browser:
        if ua.enabled and scholarly and proxied:
            return AcquireResult(
                path=None, needs_login=True, login_url=proxied, outcome="needs_login"
            )
        return AcquireResult(path=None, outcome="fetch_failed" if sources else "no_oa_source")

    return _browser_acquire(item_key, sources, url, doi, scholarly, allow_headed_fallback)


def _is_web_article(url: str) -> bool:
    """A web page whose full text is HTML (blog/Substack/news/docs) — an http(s) URL
    that is not itself a PDF. The renderer turns it into a reviewable PDF."""
    low = (url or "").strip().lower()
    return low.startswith(("http://", "https://")) and not low.endswith(".pdf")


def _dedupe_sources(sources: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for source, url in sources:
        if url and url not in seen:
            seen.add(url)
            unique.append((source, url))
    return unique


__all__ = ["AcquireResult", "acquire_pdf_for", "acquire_for_item"]
