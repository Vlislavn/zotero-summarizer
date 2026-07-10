"""Keyless Europe PMC topic search — the biomedical channel for Targeted Search.

A thin, host-locked leaf over the public Europe PMC REST search
(``ebi.ac.uk/europepmc/webservices/rest/search``). Keyless and unmetered
(unlike OpenAlex); ``resultType=core`` carries the abstract, ids (PMID/PMCID/DOI)
and OA flag in one request. Returns best-effort ``EuropePmcHit`` rows; a
transport/parse error degrades to ``[]`` (never raises) so federation continues
with whatever answered — the leaf best-effort contract (README, github.py).

Host is hard-locked to ``ebi.ac.uk``: the query is a user topic, so pinning the
host is the SSRF / indirect-injection guard.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import httpx

from zotero_summarizer.integrations._rate_limiter import RateLimiter

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_TIMEOUT_SECS = 12.0
# Europe PMC allows generous keyless use; a modest 5 req/s is polite. Shared.
_RATE_LIMITER = RateLimiter(5)

_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class EuropePmcHit:
    """One Europe PMC search result (``resultType=core``)."""

    title: str
    abstract: str
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    is_open_access: bool = False


def _hit_from_result(res: dict) -> EuropePmcHit | None:
    title = (res.get("title") or "").strip()
    abstract = (res.get("abstractText") or "").strip()
    if not title:
        return None
    year: int | None = None
    raw_year = str(res.get("pubYear") or "")
    if len(raw_year) >= 4 and raw_year[:4].isdigit():
        year = int(raw_year[:4])
    author_string = (res.get("authorString") or "").strip().rstrip(".")
    authors = [a.strip() for a in author_string.split(",") if a.strip()]
    venue = ((res.get("journalInfo") or {}).get("journal") or {}).get("title") or ""
    return EuropePmcHit(
        title=title,
        abstract=abstract,
        doi=(res.get("doi") or "").strip(),
        pmid=(res.get("pmid") or "").strip(),
        pmcid=(res.get("pmcid") or "").strip(),
        authors=authors,
        year=year,
        venue=venue.strip(),
        is_open_access=(res.get("isOpenAccess") == "Y"),
    )


def search_europepmc(
    query: str,
    *,
    page_size: int = 20,
    http_client: httpx.Client | None = None,
) -> list[EuropePmcHit]:
    """Topic search Europe PMC (relevance order). Returns ``[]`` on any error
    (network, non-200, malformed JSON) — never raises."""
    query = (query or "").strip()
    if not query:
        return []
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": max(1, min(page_size, 100)),
    }
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
    results = ((payload.get("resultList") or {}).get("result")) or []
    hits = [_hit_from_result(r) for r in results if isinstance(r, dict)]
    return [h for h in hits if h is not None]


def _xml_to_text(xml: str) -> str:
    """Plain body text from a JATS full-text document. Prefers the ``<body>``
    span (drops journal metadata / reference lists); falls back to the whole
    document when a paper has no ``<body>`` tag."""
    match = _BODY_RE.search(xml)
    inner = match.group(1) if match else xml
    text = html.unescape(_TAG_RE.sub(" ", inner))
    return _WS_RE.sub(" ", text).strip()


def fetch_fulltext_xml(pmcid: str, *, http_client: httpx.Client | None = None) -> str:
    """Fetch + plain-text the JATS full text of a PMC open-access article via the
    Europe PMC REST full-text endpoint (keyless, machine-readable — the reliable
    OA path where the ``?pdf=render`` link 404s and NCBI's mirror bot-blocks).

    Returns ``""`` when no OA full text exists (many hits are abstract-only — an
    expected state, spec §9) or on any transport/parse error — the same
    never-raises leaf contract as ``search_europepmc`` (see module docstring)."""
    pmcid = (pmcid or "").strip().upper()
    if not pmcid:
        return ""
    if not pmcid.startswith("PMC"):
        pmcid = "PMC" + pmcid
    _RATE_LIMITER.acquire()
    client = http_client or httpx.Client(timeout=_TIMEOUT_SECS)
    try:
        resp = client.get(
            f"{_REST}/{pmcid}/fullTextXML",
            headers={"User-Agent": "zotero-summarizer/1.0"},
        )
        if resp.status_code != 200:
            return ""
        xml = resp.text
    except (httpx.HTTPError, OSError):
        return ""
    finally:
        if http_client is None:
            client.close()
    return _xml_to_text(xml)


__all__ = ["EuropePmcHit", "search_europepmc", "fetch_fulltext_xml"]
