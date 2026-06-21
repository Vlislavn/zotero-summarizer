"""PubMed/PMC helpers: resolve a PMC full-text PDF URL for a PubMed item.

Published PubMed papers are often paywalled, but a large share are free-to-read in
PubMed Central (PMC). The DOI-keyed Unpaywall/OpenAlex rungs in
``services.library._pdf_acquire`` miss PMC papers that carry NO DOI (e.g. AMIA
proceedings — exactly where clinical agentic-AI work clusters): there is no PDF URL
for the browser to even try. This leaf resolves PMID/DOI → PMCID via NCBI's keyless
ID-Converter and returns the PMC article PDF URL so the fetch chain has a target.

IMPORTANT (verified 2026-06): there is no reliable *headless* PDF route for fresh
PMC papers — the website ``/pdf/`` serves a bot-wall HTML interstitial, Europe PMC
500s until ingestion, and the NCBI OA service only covers the OA-subset (and its FTP
links are deprecated). So the returned URL is meant for the **browser rung**
(`_pdf_acquire` already retries headless URLs in the real browser, which passes the
interstitial). The headless attempt simply falls through (no ``%PDF`` → None).

Cached via :class:`OpenAlexCache` (key ``pmc:<id>``) and rate-limited to NCBI's
keyless 3 req/s ceiling. Network/parse errors degrade to ``None`` (the caller falls
through to the other rungs) — PMC resolution never blocks a fetch.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import httpx

from zotero_summarizer.domain import normalize_doi
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache


LOGGER = logging.getLogger(__name__)

# The ID-Converter moved hosts in 2025; the old www.ncbi.nlm.nih.gov/pmc/utils/idconv/
# path 301-redirects here. Use the new host directly to avoid a redirect per call.
_IDCONV_BASE = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
_TIMEOUT_SECS = 8.0
_RATE_LIMIT_PER_SEC = 3  # NCBI keyless ceiling

# PubMed item URLs look like https://pubmed.ncbi.nlm.nih.gov/41234567/
_PMID_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{6,9})", re.IGNORECASE)
_PMCID_RE = re.compile(r"^PMC\d+$", re.IGNORECASE)


class _RateLimiter:
    """Per-process token bucket: at most ``rate`` calls per second.

    ponytail: a 12-line copy of OpenAlex's limiter rather than coupling two
    integration leaves through a private import; lift to a shared helper only if a
    third caller appears.
    """

    def __init__(self, rate: int) -> None:
        self._interval = 1.0 / rate
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last + self._interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last = now


_RATE_LIMITER = _RateLimiter(_RATE_LIMIT_PER_SEC)


def _pmid_from_url(url: str) -> str:
    """Extract a PMID from a PubMed item URL; empty string when absent."""
    if not url:
        return ""
    match = _PMID_RE.search(url)
    return match.group(1) if match else ""


def _pmc_pdf_url(pmcid: str) -> str | None:
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/" if pmcid else None


def resolve_pmc_pdf_url(
    *,
    cache: OpenAlexCache,
    pmid: str = "",
    doi: str = "",
    email: str = "",
    http_client: httpx.Client | None = None,
    allow_network: bool = True,
) -> str | None:
    """PMID/DOI → free PMC full-text PDF URL, or ``None`` when the paper is not in PMC.

    Resolves the PMCID via NCBI's keyless ID-Converter and returns
    ``https://pmc.ncbi.nlm.nih.gov/articles/<PMCID>/pdf/``. Cached (``pmc:<id>``);
    best-effort — any failure returns ``None`` so the caller falls through to the
    other rungs. ``allow_network=False`` is cache-only.
    """
    ident = (pmid or "").strip() or normalize_doi(doi)
    if not ident:
        return None
    key = f"pmc:{ident}"
    cached = cache.get(key)
    if cached is not None:
        return _pmc_pdf_url(str(cached.get("pmcid") or ""))
    if not allow_network:
        return None

    pmcid = _fetch_pmcid(ident, email=email, http_client=http_client)
    cache.set(key, {"pmcid": pmcid})
    return _pmc_pdf_url(pmcid)


def _fetch_pmcid(ident: str, *, email: str, http_client: httpx.Client | None) -> str:
    """Call the ID-Converter for ``ident`` (PMID or DOI); return a ``PMC…`` id or ""."""
    _RATE_LIMITER.acquire()
    params: dict[str, str] = {"ids": ident, "format": "json", "tool": "zotero-summarizer"}
    if email:
        params["email"] = email
    client = http_client or httpx.Client(timeout=_TIMEOUT_SECS, follow_redirects=True)
    try:
        resp = client.get(_IDCONV_BASE, params=params)
        if resp.status_code >= 400:
            LOGGER.debug("idconv HTTP %d for %s", resp.status_code, ident)
            return ""
        payload: dict[str, Any] = resp.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        LOGGER.debug("idconv failed for %s: %s", ident, exc)
        return ""
    finally:
        if http_client is None:
            client.close()
    for record in payload.get("records") or []:
        pmcid = str(record.get("pmcid") or "").strip()
        if _PMCID_RE.match(pmcid):
            return pmcid.upper()
    return ""


__all__ = ["resolve_pmc_pdf_url"]
