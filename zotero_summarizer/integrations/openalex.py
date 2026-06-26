"""Minimal OpenAlex client for prestige enrichment.

OpenAlex is free and unauthenticated; setting a ``mailto`` query parameter
moves you into the polite pool (faster, lower error rate). Rate limit is
~10 req/s; a threading semaphore keeps us under that bound.

The client returns a normalized :class:`OpenAlexWork` with the fields needed
for :mod:`zotero_summarizer.services.model.prestige`. Author h-index requires a
follow-up call to ``/authors/{id}`` — we look up at most ``max_authors`` (default
3) to keep latency bounded.

All HTTP responses are cached via :class:`OpenAlexCache`. Network errors and
4xx/5xx responses are swallowed (logged) so prestige never blocks triage.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from zotero_summarizer.domain import normalize_doi
from zotero_summarizer.integrations._rate_limiter import RateLimiter
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
    # Field- AND year-normalized citation impact (SOTA, robust vs raw counts):
    # citation_percentile in [0,1] (None for too-new / uncited works → cold-start);
    # fwci = field-weighted citation impact (1.0 == field average).
    citation_percentile: float | None = None
    fwci: float | None = None
    # Cold-start author-reputation prior: the MAX across the work's (≤max_authors)
    # authors of each author's FIELD-NORMALIZED standing = median of that author's
    # works' citation_normalized_percentile, in [0,1]. None when the work already
    # has its own percentile (not cold-start, so not computed) or no author has any
    # field-normalized works yet. Used ONLY when ``citation_percentile is None``.
    max_author_field_percentile: float | None = None
    # Reconstructed from OpenAlex's ``abstract_inverted_index`` (already present in
    # the cached work payload). Used to backfill RSS items that arrived with a
    # title but no abstract so the classifier gate can score them. None when the
    # work has no abstract index (some records omit it).
    abstract: str | None = None


_RATE_LIMITER = RateLimiter(_RATE_LIMIT_PER_SEC)


class OpenAlexClient:
    def __init__(
        self,
        cache: OpenAlexCache,
        *,
        mailto: str | None = None,
        max_authors: int = 3,
        http_client: httpx.Client | None = None,
        allow_network: bool = True,
    ) -> None:
        self.cache = cache
        self.mailto = (mailto or "").strip() or None
        self.max_authors = int(max_authors)
        self._http = http_client or httpx.Client(timeout=_TIMEOUT_SECS)
        # When False, the client is CACHE-ONLY: it never makes a network call and
        # a cache miss returns None. Used on interactive request paths (opening a
        # paper's "why this score?" detail) so a click never blocks on a
        # multi-second OpenAlex search; network lookups happen during background
        # triage/rescore, which then populate the cache for these reads.
        self.allow_network = bool(allow_network)

    # ------------------------------------------------------------------ public

    def fetch_work_by_doi(self, doi: str) -> OpenAlexWork | None:
        doi_norm = normalize_doi(doi)
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
        if not enriched.pop("__author_pct_incomplete", False):
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
        if not enriched.pop("__author_pct_incomplete", False):
            self.cache.set(cache_key, enriched)
        return self._work_from_payload(enriched)

    def _enrich_with_authors(self, work: dict[str, Any]) -> dict[str, Any]:
        authorships = work.get("authorships") or []
        # The author FIELD-PERCENTILE prior is only used when THIS work has no
        # field-normalized percentile of its own (cold-start). For established
        # papers (the common case) skip the extra per-author /works calls.
        work_pct = (work.get("citation_normalized_percentile") or {}).get("value")
        want_author_pct = work_pct is None
        h_indices: list[int] = []
        author_pcts: list[float] = []
        pct_incomplete = False
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
            if want_author_pct:
                ap = self._author_field_percentile(short_id)
                if ap is not None:
                    author_pcts.append(ap)
                elif self.cache.get(f"author_pct:{short_id}") is None:
                    # None + nothing cached ⇒ the /works call FAILED (a genuine
                    # empty result caches {"field_percentile": None}). Don't let a
                    # transient failure freeze this work at "no author signal" for
                    # the whole work-cache TTL — mark it so the caller skips
                    # caching and the next pass retries.
                    pct_incomplete = True
        work["__max_author_h_index"] = max(h_indices) if h_indices else 0
        # MAX across the work's authors (mirrors __max_author_h_index): a paper
        # inherits the standing of its STRONGEST author — the common junior-first-
        # author / senior-PI case is exactly what the cold-start prior should
        # reward. The bounded cap + convex map keep one gamed coauthor from
        # dominating (max composite delta ≈ 0.075 on the [1,5] scale).
        work["__max_author_field_percentile"] = max(author_pcts) if author_pcts else None
        if pct_incomplete:
            work["__author_pct_incomplete"] = True
        return work

    def _author_field_percentile(self, short_id: str) -> float | None:
        """Author's field-normalized standing = MEDIAN of the author's recent
        works' OpenAlex ``citation_normalized_percentile`` (field- AND
        year-normalized), in [0, 1]. ``None`` when the author has no works with a
        percentile yet (too new) — a cold-start author is never penalised.

        Cached per author (``author_pct:{id}``); a cache hit (even a stored
        ``None``) is reused so we never re-query an author within the TTL.
        """
        cache_key = f"author_pct:{short_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            v = cached.get("field_percentile")
            return float(v) if v is not None else None
        payload = self._get(
            "/works",
            params={
                "filter": f"authorships.author.id:{short_id}",
                "select": "citation_normalized_percentile",
                "per-page": 50,
                "sort": "publication_date:desc",
            },
        )
        if payload is None:
            return None  # cache-only/no-network or transport error — do not cache
        vals = sorted(
            float(v)
            for w in (payload.get("results") or [])
            if (v := ((w or {}).get("citation_normalized_percentile") or {}).get("value")) is not None
        )
        median = _median(vals)
        self.cache.set(cache_key, {"field_percentile": median})
        return median

    @staticmethod
    def _work_from_payload(payload: dict[str, Any]) -> OpenAlexWork | None:
        try:
            venue = (
                (payload.get("primary_location") or {}).get("source")
                or payload.get("host_venue")
                or {}
            )
            oa = payload.get("open_access") or {}
            # Field+year-normalized impact. citation_normalized_percentile is an
            # object {value, is_in_top_1_percent, ...}; value is None for too-new
            # / uncited works (cold-start). Already present in cached full payloads.
            cnp = payload.get("citation_normalized_percentile") or {}
            pct = cnp.get("value")
            fwci = payload.get("fwci")
            return OpenAlexWork(
                openalex_id=str(payload.get("id") or ""),
                max_author_h_index=int(payload.get("__max_author_h_index") or 0),
                venue_works_count=int(venue.get("works_count") or 0),
                venue_display_name=str(venue.get("display_name") or ""),
                cited_by_count=int(payload.get("cited_by_count") or 0),
                is_oa=bool(oa.get("is_oa") or False),
                oa_url=(oa.get("oa_url") or None),
                citation_percentile=(float(pct) if pct is not None else None),
                fwci=(float(fwci) if fwci is not None else None),
                max_author_field_percentile=(
                    float(payload["__max_author_field_percentile"])
                    if payload.get("__max_author_field_percentile") is not None
                    else None
                ),
                abstract=_abstract_from_inverted_index(
                    payload.get("abstract_inverted_index")
                ),
            )
        except (ValueError, TypeError) as exc:
            LOGGER.debug("openalex: payload parse failed: %s", exc)
            return None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.allow_network:
            return None  # cache-only client: a miss yields None, never a network call
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

def _abstract_from_inverted_index(inv: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plain abstract text from OpenAlex's ``abstract_inverted_index``.

    OpenAlex ships the abstract as ``{word: [positions...]}`` (copyright-friendly).
    Place each word at every position it occupies, order by position, and join.
    Returns None for a missing/empty index.
    """
    if not inv:
        return None
    positions: list[tuple[int, str]] = [
        (pos, word) for word, idxs in inv.items() for pos in idxs
    ]
    if not positions:
        return None
    positions.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positions)


def _median(values: list[float]) -> float | None:
    """Median of a SORTED list (caller sorts). None for an empty list."""
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


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
