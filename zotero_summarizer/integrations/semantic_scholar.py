"""Keyless Semantic Scholar topic search — a relevance-ranked, abstract-rich channel.

A thin, host-locked leaf over the public Semantic Scholar Graph paper search
(``api.semanticscholar.org/graph/v1/paper/search``). Its relevance ranking and
abstract coverage are strong, and each hit exposes cross-source ids
(DOI/arXiv/PubMed) that dedupe cleanly into the version families.

The KEYLESS tier is aggressively rate-limited (shared ~1 req/s pool, frequent
429s). We gate on a conservative 1 req/s ``RateLimiter`` and treat a 429 / any
transport-or-parse error as an empty channel — the leaf best-effort contract
(``europepmc.py``): a throttled source contributes nothing this run, never raises.

Host is hard-locked to ``api.semanticscholar.org`` (SSRF / injection guard).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from zotero_summarizer.integrations._rate_limiter import RateLimiter

_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
_TIMEOUT_SECS = 12.0
# Keyless S2 is ~1 req/s shared; anything faster gets a 429. Conservative + shared.
_RATE_LIMITER = RateLimiter(1)
_FIELDS = "title,abstract,year,venue,authors,externalIds,openAccessPdf"


@dataclass(frozen=True, slots=True)
class SemanticScholarHit:
    """One Semantic Scholar paper-search result."""

    title: str
    abstract: str
    doi: str = ""
    arxiv_id: str = ""
    pmid: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    is_open_access: bool = False
    url: str = ""


def _hit_from_paper(paper: dict) -> SemanticScholarHit | None:
    title = (paper.get("title") or "").strip()
    if not title:
        return None
    ext = paper.get("externalIds") or {}
    oa = paper.get("openAccessPdf") or {}
    authors = [
        (a.get("name") or "").strip()
        for a in (paper.get("authors") or [])
        if isinstance(a, dict) and (a.get("name") or "").strip()
    ]
    year = paper.get("year")
    return SemanticScholarHit(
        title=title,
        abstract=(paper.get("abstract") or "").strip(),
        doi=str(ext.get("DOI") or "").strip(),
        arxiv_id=str(ext.get("ArXiv") or "").strip(),
        pmid=str(ext.get("PubMed") or "").strip(),
        authors=authors,
        year=int(year) if isinstance(year, int) else None,
        venue=(paper.get("venue") or "").strip(),
        is_open_access=bool(oa.get("url")),
        url=str(oa.get("url") or "").strip(),
    )


def search_semantic_scholar(
    query: str,
    *,
    limit: int = 20,
    http_client: httpx.Client | None = None,
) -> list[SemanticScholarHit]:
    """Topic search Semantic Scholar (relevance order). Returns ``[]`` on any error
    (throttling 429, network, non-200, malformed JSON) — never raises."""
    query = (query or "").strip()
    if not query:
        return []
    params = {"query": query, "limit": max(1, min(limit, 100)), "fields": _FIELDS}
    _RATE_LIMITER.acquire()
    client = http_client or httpx.Client(timeout=_TIMEOUT_SECS)
    try:
        resp = client.get(_BASE, params=params, headers={"User-Agent": "zotero-summarizer/1.0"})
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except (httpx.HTTPError, OSError, ValueError):
        return []
    finally:
        if http_client is None:
            client.close()
    papers = payload.get("data") or []
    hits = [_hit_from_paper(p) for p in papers if isinstance(p, dict)]
    return [h for h in hits if h is not None]


__all__ = ["SemanticScholarHit", "search_semantic_scholar"]
