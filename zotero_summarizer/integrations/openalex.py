"""Minimal OpenAlex client for prestige enrichment.

OpenAlex is free and unauthenticated; setting a ``mailto`` query parameter
moves you into the polite pool (faster, lower error rate). Rate limit is
~10 req/s; a threading semaphore keeps us under that bound.

The client returns a normalized :class:`OpenAlexWork` with the fields needed
for :mod:`zotero_summarizer.services.prestige`. Author h-index requires a
follow-up call to ``/authors/{id}`` — we look up at most ``max_authors`` (default
3) to keep latency bounded.

All HTTP responses are cached via :class:`OpenAlexCache`. Network errors and
4xx/5xx responses are swallowed (logged) so prestige never blocks triage.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from zotero_summarizer.integrations.openalex_cache import OpenAlexCache


LOGGER = logging.getLogger(__name__)

_OPENALEX_BASE = "https://api.openalex.org"
_RATE_LIMIT_PER_SEC = 10  # OpenAlex polite-pool ceiling
_TIMEOUT_SECS = 10.0


@dataclass(frozen=True)
class OpenAlexWork:
    """Subset of OpenAlex fields used for prestige scoring."""

    openalex_id: str
    max_author_h_index: int
    venue_works_count: int
    venue_display_name: str
    cited_by_count: int
    is_oa: bool
    oa_url: str | None


class _RateLimiter:
    """Simple per-process token bucket: at most ``rate`` calls per second."""

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


class OpenAlexClient:
    def __init__(
        self,
        cache: OpenAlexCache,
        *,
        mailto: str | None = None,
        max_authors: int = 3,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.cache = cache
        self.mailto = (mailto or "").strip() or None
        self.max_authors = int(max_authors)
        self._http = http_client or httpx.Client(timeout=_TIMEOUT_SECS)

    # ------------------------------------------------------------------ public

    def fetch_work_by_doi(self, doi: str) -> OpenAlexWork | None:
        doi_norm = self._normalize_doi(doi)
        if not doi_norm:
            return None
        key = f"doi:{doi_norm}"
        return self._fetch_work(key, path=f"/works/doi:{doi_norm}")

    def fetch_work_by_title(self, title: str, *, year: int | None = None) -> OpenAlexWork | None:
        title_clean = (title or "").strip()
        if len(title_clean) < 20:  # too short → unreliable match
            return None
        title_hash = hashlib.sha1(title_clean.encode("utf-8")).hexdigest()[:16]
        key = f"title:{title_hash}"
        cached = self.cache.get(key)
        if cached is not None:
            return self._work_from_payload(cached)

        params: dict[str, Any] = {"search": title_clean, "per_page": 1}
        if year is not None:
            params["filter"] = f"publication_year:{year}"
        payload = self._get("/works", params=params)
        if not payload:
            return None
        results = payload.get("results") or []
        if not results:
            return None
        top = results[0]
        # Fuzzy-match guard: only accept if title prefix matches strongly.
        candidate_title = (top.get("title") or top.get("display_name") or "").strip()
        if not _title_match(candidate_title, title_clean):
            return None
        enriched = self._enrich_with_authors(top)
        self.cache.set(key, enriched)
        return self._work_from_payload(enriched)

    # ----------------------------------------------------------------- private

    def _fetch_work(self, cache_key: str, *, path: str) -> OpenAlexWork | None:
        cached = self.cache.get(cache_key)
        if cached is not None:
            return self._work_from_payload(cached)
        payload = self._get(path)
        if not payload:
            return None
        enriched = self._enrich_with_authors(payload)
        self.cache.set(cache_key, enriched)
        return self._work_from_payload(enriched)

    def _enrich_with_authors(self, work: dict[str, Any]) -> dict[str, Any]:
        authorships = work.get("authorships") or []
        h_indices: list[int] = []
        for auth in authorships[: self.max_authors]:
            author = (auth or {}).get("author") or {}
            author_id = author.get("id")
            if not author_id:
                continue
            short_id = author_id.rsplit("/", 1)[-1]  # e.g. 'A1234'
            cache_key = f"author:{short_id}"
            cached = self.cache.get(cache_key)
            if cached is not None:
                h = int(cached.get("h_index") or 0)
            else:
                profile = self._get(f"/authors/{short_id}")
                h = 0
                if profile:
                    h = int(
                        ((profile.get("summary_stats") or {}).get("h_index")) or 0
                    )
                    self.cache.set(cache_key, {"h_index": h})
            h_indices.append(h)
        work["__max_author_h_index"] = max(h_indices) if h_indices else 0
        return work

    @staticmethod
    def _work_from_payload(payload: dict[str, Any]) -> OpenAlexWork | None:
        try:
            venue = (
                (payload.get("primary_location") or {}).get("source")
                or payload.get("host_venue")
                or {}
            )
            oa = payload.get("open_access") or {}
            return OpenAlexWork(
                openalex_id=str(payload.get("id") or ""),
                max_author_h_index=int(payload.get("__max_author_h_index") or 0),
                venue_works_count=int(venue.get("works_count") or 0),
                venue_display_name=str(venue.get("display_name") or ""),
                cited_by_count=int(payload.get("cited_by_count") or 0),
                is_oa=bool(oa.get("is_oa") or False),
                oa_url=(oa.get("oa_url") or None),
            )
        except (ValueError, TypeError) as exc:
            LOGGER.debug("openalex: payload parse failed: %s", exc)
            return None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        _RATE_LIMITER.acquire()
        merged: dict[str, Any] = dict(params or {})
        if self.mailto:
            merged["mailto"] = self.mailto
        url = f"{_OPENALEX_BASE}{path}"
        try:
            resp = self._http.get(url, params=merged)
        except (httpx.HTTPError, OSError) as exc:
            LOGGER.debug("openalex GET failed: %s (%s)", url, exc)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            LOGGER.debug("openalex GET %s: HTTP %d", url, resp.status_code)
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _normalize_doi(doi: str) -> str:
        d = (doi or "").strip().lower()
        if not d:
            return ""
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.strip("/")


def _title_match(candidate: str, target: str) -> bool:
    """Loose check: candidate should share most leading words with target."""
    a = "".join(ch.lower() for ch in candidate if ch.isalnum() or ch.isspace()).split()
    b = "".join(ch.lower() for ch in target if ch.isalnum() or ch.isspace()).split()
    if not a or not b:
        return False
    n = min(len(a), len(b), 6)
    if n == 0:
        return False
    matches = sum(1 for i in range(n) if a[i] == b[i])
    return matches / n >= 0.7
