"""Keyless Crossref topic search — the broad scholarly-metadata channel.

A thin, host-locked leaf over the public Crossref REST works search
(``api.crossref.org/works``). Keyless and unmetered; a ``mailto`` puts the
request in Crossref's faster "polite pool". Covers ~150M works across every
publisher, so it widens recall well past arXiv+Europe PMC. Every hit carries a
DOI, so it deduplicates cleanly into the existing version families.

Returns best-effort ``CrossrefHit`` rows; a transport/parse error degrades to
``[]`` (never raises) so federation continues with whatever answered — the leaf
best-effort contract (see ``europepmc.py``/``arxiv.py``).

Host is hard-locked to ``api.crossref.org``: the query is a user topic, so
pinning the host is the SSRF / indirect-injection guard.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import httpx

from zotero_summarizer.integrations._rate_limiter import RateLimiter

_BASE = "https://api.crossref.org/works"
_TIMEOUT_SECS = 12.0
# Crossref asks the polite pool for ~50 req/s max; 5 req/s is courteous. Shared.
_RATE_LIMITER = RateLimiter(5)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class CrossrefHit:
    """One Crossref works-search result."""

    title: str
    abstract: str
    doi: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""


def _abstract_text(raw: str) -> str:
    """Crossref abstracts are JATS-wrapped (``<jats:p>…``) when present at all
    (many rows have none). Strip tags to plain text — mirrors europepmc's."""
    if not raw:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw))).strip()


def _year_from_item(item: dict) -> int | None:
    # "published" (falling back to issued/created): {"date-parts": [[2024, 3, 1]]}.
    for key in ("published", "issued", "created"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])
        if parts and parts[0] and str(parts[0][0]).isdigit():
            return int(parts[0][0])
    return None


def _authors_from_item(item: dict) -> list[str]:
    out: list[str] = []
    for a in item.get("author") or []:
        if not isinstance(a, dict):
            continue
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
        name = name or (a.get("name") or "").strip()
        if name:
            out.append(name)
    return out


def _hit_from_item(item: dict) -> CrossrefHit | None:
    titles = item.get("title") or []
    title = (titles[0] if titles else "").strip()
    if not title:
        return None
    containers = item.get("container-title") or []
    return CrossrefHit(
        title=title,
        abstract=_abstract_text(item.get("abstract") or ""),
        doi=(item.get("DOI") or "").strip(),
        authors=_authors_from_item(item),
        year=_year_from_item(item),
        venue=(containers[0] if containers else "").strip(),
    )


def search_crossref(
    query: str,
    *,
    rows: int = 20,
    mailto: str | None = None,
    http_client: httpx.Client | None = None,
) -> list[CrossrefHit]:
    """Topic search Crossref (relevance order). Returns ``[]`` on any error
    (network, non-200, malformed JSON) — never raises."""
    query = (query or "").strip()
    if not query:
        return []
    params: dict[str, str | int] = {"query": query, "rows": max(1, min(rows, 100))}
    if (mailto or "").strip():
        params["mailto"] = mailto.strip()
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
    items = ((payload.get("message") or {}).get("items")) or []
    hits = [_hit_from_item(it) for it in items if isinstance(it, dict)]
    return [h for h in hits if h is not None]


__all__ = ["CrossrefHit", "search_crossref"]
