"""Keyless arXiv topic search — the preprint channel for Targeted Search.

A thin, host-locked leaf over the public arXiv Atom API
(``export.arxiv.org/api/query``). Keyless (no budget, unlike OpenAlex): arXiv
asks only for a polite request rate, honored by the shared 3 req/s limiter.
Returns a list of best-effort ``ArxivHit`` rows; a transport/parse error degrades
to ``[]`` (never raises) so federation continues with whatever answered — the
same best-effort contract as ``github.py``/``unpaywall.py``.

Host is hard-locked to ``export.arxiv.org``: the query text is a user topic, so
pinning the host is the SSRF / indirect-injection guard (paper/topic text can
never steer the request elsewhere).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from zotero_summarizer.domain import normalize_arxiv_id
from zotero_summarizer.integrations._rate_limiter import RateLimiter

_BASE = "https://export.arxiv.org/api/query"
_TIMEOUT_SECS = 12.0
# arXiv publishes no hard keyless quota but asks for ~1 req / 3 s on the API; a
# 3 req/s ceiling is polite and matches the pubmed leaf. Shared per-process.
_RATE_LIMITER = RateLimiter(3)
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


@dataclass(frozen=True, slots=True)
class ArxivHit:
    """One arXiv search result. ``arxiv_id`` is version-stripped (dedup key)."""

    arxiv_id: str
    title: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    primary_category: str = ""
    pdf_url: str = ""


def _text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    return (node.findtext(path, default="", namespaces=_NS) or "").strip()


def _hit_from_entry(entry: ET.Element) -> ArxivHit | None:
    raw_id = _text(entry, "a:id")  # e.g. http://arxiv.org/abs/2209.07858v2
    arxiv_id = normalize_arxiv_id(raw_id)
    title = " ".join(_text(entry, "a:title").split())
    abstract = " ".join(_text(entry, "a:summary").split())
    if not arxiv_id or not title:
        return None
    published = _text(entry, "a:published")  # 2022-08-23T23:37:14Z
    year: int | None = None
    if len(published) >= 4 and published[:4].isdigit():
        year = int(published[:4])
    authors = [
        n for n in (_text(a, "a:name") for a in entry.findall("a:author", _NS)) if n
    ]
    pdf_url = ""
    for link in entry.findall("a:link", _NS):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href", "") or ""
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    cat_node = entry.find("arxiv:primary_category", _NS)
    return ArxivHit(
        arxiv_id=arxiv_id,
        title=title,
        abstract=abstract,
        authors=authors,
        year=year,
        doi=_text(entry, "arxiv:doi"),
        primary_category=(cat_node.get("term", "") if cat_node is not None else ""),
        pdf_url=pdf_url,
    )


def search_arxiv(
    query: str,
    *,
    max_results: int = 20,
    http_client: httpx.Client | None = None,
) -> list[ArxivHit]:
    """Topic search arXiv, best relevance first. Returns ``[]`` on any error
    (network, non-200, malformed XML) — never raises."""
    query = (query or "").strip()
    if not query:
        return []
    params = {
        "search_query": f"all:{query}",
        "max_results": max(1, min(max_results, 50)),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    _RATE_LIMITER.acquire()
    client = http_client or httpx.Client(timeout=_TIMEOUT_SECS)
    try:
        resp = client.get(_BASE, params=params, headers={"User-Agent": "zotero-summarizer/1.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
    except (httpx.HTTPError, OSError, ET.ParseError):
        return []
    finally:
        if http_client is None:
            client.close()
    hits = [_hit_from_entry(e) for e in root.findall("a:entry", _NS)]
    return [h for h in hits if h is not None]


__all__ = ["ArxivHit", "search_arxiv"]
