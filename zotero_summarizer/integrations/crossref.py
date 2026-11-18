"""Keyless Crossref topic search — the broad scholarly-metadata channel.

A thin, host-locked leaf over the public Crossref REST works search
(``api.crossref.org/works``). Keyless and unmetered; a ``mailto`` puts the
request in Crossref's faster "polite pool". Covers ~150M works across every
publisher, so it widens recall well past arXiv+Europe PMC. Every hit carries a
DOI, so it deduplicates cleanly into the existing version families.

Returns best-effort ``CrossrefHit`` rows; a transport/parse error degrades to
``[]`` (never raises) so federation continues with whatever answered — the leaf
best-effort contract (see ``europepmc.py``/``arxiv.py``).

``list_crossref_works`` is the one non-best-effort entry: cursor-paged
``filter``/``select`` listing for a bulk fetch, raising ``CrossrefListError``
(reason, status, retry-after) so the caller can name what failed.

Host is hard-locked to ``api.crossref.org``: the query is a user topic, so
pinning the host is the SSRF / indirect-injection guard.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import httpx

from zotero_summarizer.integrations._rate_limiter import RateLimiter
from zotero_summarizer.integrations._search_http import json_rows, search_hits

_BASE = "https://api.crossref.org/works"
_TIMEOUT_SECS = 12.0
# Crossref asks the polite pool for ~50 req/s max; 5 req/s is courteous. Shared.
_RATE_LIMITER = RateLimiter(5)
_LIST_TIMEOUT_SECS = 60.0  # one page of 1 000 rows with abstracts is several MB
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
    page: str = ""  # first page; conference-supplement abstracts carry ``S209``-style pages
    volume: str = ""


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
        page=str(item.get("page") or "").strip(),
        volume=str(item.get("volume") or "").strip(),
    )


def _search_hits(response: httpx.Response, strict: bool) -> list[CrossrefHit | None]:
    return [_hit_from_item(item) for item in json_rows(response, "message", "items", strict=strict)]


def search_crossref(
    query: str,
    *,
    rows: int = 20,
    mailto: str | None = None,
    http_client: httpx.Client | None = None,
    outcome: dict | None = None,
) -> list[CrossrefHit]:
    """Topic search Crossref (relevance order). Returns ``[]`` on any error
    (network, non-200, malformed JSON) — never raises."""
    query = (query or "").strip()
    params: dict[str, str | int] = {"query": query, "rows": max(1, min(rows, 100))}
    if (mailto or "").strip():
        params["mailto"] = mailto.strip()
    return search_hits(query, _BASE, params, _search_hits, _RATE_LIMITER,
                       timeout=_TIMEOUT_SECS, http_client=http_client, outcome=outcome)


class CrossrefListError(Exception):
    """A listing request failed: ``reason`` ∈ rate_limited / http_error / transport_error / parse_error."""

    def __init__(self, reason: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(reason)
        self.reason, self.status, self.retry_after = reason, status, retry_after


@dataclass(frozen=True, slots=True)
class CrossrefPage:
    hits: list[CrossrefHit]
    next_cursor: str  # "" when the server sent none
    total: int
    seen: int  # rows returned, including ones dropped for having no title


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After", "").strip()
    return float(raw) if raw.isdigit() else None


def list_crossref_works(
    *,
    filters: str,
    select: tuple[str, ...],
    cursor: str = "*",
    rows: int = 1000,
    mailto: str | None = None,
    http_client: httpx.Client | None = None,
) -> CrossrefPage:
    """One cursor page of ``/works`` for ``filters`` (e.g. ``issn:…,from-pub-date:…``),
    asking only for the ``select`` fields. Pass the previous page's ``next_cursor``
    (first page: ``"*"``). Raises ``CrossrefListError``; never returns a silent ``[]``."""
    params: dict[str, str | int] = {"filter": filters, "select": ",".join(select), "rows": rows, "cursor": cursor}
    if (mailto or "").strip():
        params["mailto"] = mailto.strip()
    _RATE_LIMITER.acquire()
    client = http_client or httpx.Client(timeout=_LIST_TIMEOUT_SECS)
    try:
        try:
            response = client.get(_BASE, params=params, headers={"User-Agent": "zotero-summarizer/1.0"})
        except (httpx.HTTPError, OSError) as exc:
            raise CrossrefListError("transport_error") from exc
        if response.status_code != 200:
            raise CrossrefListError("rate_limited" if response.status_code == 429 else "http_error",
                                    status=response.status_code, retry_after=_retry_after(response))
        try:
            message = response.json()["message"]
            items = json_rows(response, "message", "items", strict=True)
            return CrossrefPage([h for h in map(_hit_from_item, items) if h], str(message.get("next-cursor") or ""),
                                int(message.get("total-results") or 0), len(items))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CrossrefListError("parse_error", status=response.status_code) from exc
    finally:
        if http_client is None:
            client.close()


__all__ = ["CrossrefHit", "CrossrefListError", "CrossrefPage", "list_crossref_works", "search_crossref"]
