"""Unpaywall client: DOI → open-access PDF URL.

Unpaywall is free (REST only, no API key, email required as a polite-pool
identifier). Cached via :class:`OpenAlexCache` with key prefix
``unpaywall:`` so the cache is shared with OpenAlex.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from zotero_summarizer.domain import normalize_doi
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache


LOGGER = logging.getLogger(__name__)

_UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
_TIMEOUT_SECS = 8.0


class UnpaywallClient:
    def __init__(
        self,
        cache: OpenAlexCache,
        *,
        email: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.cache = cache
        self.email = (email or "").strip()
        self._http = http_client or httpx.Client(timeout=_TIMEOUT_SECS)

    def find_oa_pdf_url(self, doi: str) -> str | None:
        doi_norm = normalize_doi(doi)
        if not doi_norm:
            return None
        if not self.email:
            LOGGER.debug("unpaywall: no email configured; skipping lookup for %s", doi_norm)
            return None
        key = f"unpaywall:{doi_norm}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached.get("pdf_url") or None

        url = f"{_UNPAYWALL_BASE}/{doi_norm}"
        try:
            resp = self._http.get(url, params={"email": self.email})
        except (httpx.HTTPError, OSError) as exc:
            LOGGER.debug("unpaywall GET failed: %s", exc)
            return None
        if resp.status_code == 404:
            self.cache.set(key, {"pdf_url": None})
            return None
        if resp.status_code >= 400:
            LOGGER.debug("unpaywall GET %s: HTTP %d", url, resp.status_code)
            return None
        try:
            payload: dict[str, Any] = resp.json()
        except ValueError:
            return None
        pdf_url = ((payload.get("best_oa_location") or {}).get("url_for_pdf")) or None
        self.cache.set(key, {"pdf_url": pdf_url})
        return pdf_url
