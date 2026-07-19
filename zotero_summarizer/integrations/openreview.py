"""Authenticated OpenReview peer-review source — a SEARCH-ONLY prestige channel.

OpenReview.net carries accepted papers at major ML venues (ICLR/NeurIPS/ICML/
COLM/TMLR/MIDL...) with their decision tier (Oral/Spotlight/Poster) and numeric
reviewer ratings. Anonymous access is walled (403 ChallengeRequired on structured
``/notes``); an authenticated session (username/password → JWT) clears it. This
leaf is that session, host-locked to ``api2.openreview.net``.

Credentials come from the environment (``OPENREVIEW_USERNAME`` /
``OPENREVIEW_PASSWORD``) — never config, never logged. Absent creds or
``allow_network=False`` → the channel is disabled and ``search_openreview``
returns ``[]`` (the leaf best-effort contract shared with ``semantic_scholar.py`` /
``europepmc.py``: a disabled or throttled source contributes nothing, never raises).

The peer-review signal this exposes (tier + rating) drives SEARCH ranking ONLY. It
is deliberately kept OUT of the triage citation-prestige blend, which rejects venue
prestige as gameable (Leiden Manifesto). Raw ``mean_rating`` is returned as-is;
per-venue-year normalization needs the whole pool and so lives in the ranker, not
here. See ``services/search`` for how the signal is consumed.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import httpx

from zotero_summarizer.integrations._rate_limiter import RateLimiter
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache

LOGGER = logging.getLogger(__name__)

_BASE = "https://api2.openreview.net"
_TIMEOUT_SECS = 20.0
# Authenticated pool — polite, well under any throttle.
_RATE_LIMITER = RateLimiter(3)

# Accepted main-conference tier vocabulary in ``content.venue`` (e.g. "ICLR 2025
# Oral"). A venue that reads as reject/withdraw/still-under-submission is NOT
# accepted and is dropped (never a negative signal). Workshop acceptances are a
# SEPARATE tier — detected from the venueid path (``.../Workshop/...``), since a
# workshop's own "Poster"/"Spotlight" label is not a main-conference prestige
# signal (workshops accept most submissions).
_TIER_PATTERNS = (
    ("oral", re.compile(r"\boral\b", re.I)),
    ("spotlight", re.compile(r"\b(spotlight|notable)\b", re.I)),
    ("poster", re.compile(r"\bposter\b", re.I)),
)
_WORKSHOP = re.compile(r"\bworkshop\b", re.I)
_ACCEPT = re.compile(r"\b(oral|spotlight|notable|poster|accept|camera|proceedings)\b", re.I)
_REJECT = re.compile(r"\b(reject|withdraw|desk|submitted\s+to)\b", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b")
_RATING_LEAD_INT = re.compile(r"-?\d+")


@dataclass(frozen=True, slots=True)
class OpenReviewHit:
    """One accepted OpenReview paper with its peer-review evidence.

    ``mean_rating`` is the RAW per-venue reviewer scale (ICLR 1-10, others differ);
    the ranker normalizes per venue-year. ``tier`` ∈ {oral, spotlight, poster,
    workshop}. Only accepted papers are ever constructed, so ``decision`` is always
    ``"accepted"`` (rejected papers are dropped upstream → they carry no signal)."""

    title: str
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""      # e.g. "ICLR 2025 Oral"
    venueid: str = ""    # e.g. "ICLR.cc/2025/Conference"
    forum_id: str = ""
    url: str = ""
    decision: str = "accepted"
    tier: str = "poster"
    mean_rating: float | None = None
    n_reviews: int = 0
    doi: str = ""


def _val(field_obj: object) -> object:
    """OpenReview API v2 wraps every content field as ``{"value": ...}``."""
    return field_obj.get("value") if isinstance(field_obj, dict) else field_obj


def _classify_tier(venue: str, venueid: str) -> str:
    if "/workshop" in venueid.lower() or _WORKSHOP.search(venue):
        return "workshop"
    for name, pat in _TIER_PATTERNS:
        if pat.search(venue):
            return name
    return "poster"


def _is_accepted(venue: str) -> bool:
    return bool(_ACCEPT.search(venue)) and not _REJECT.search(venue)


def _parse_rating(raw: object) -> float | None:
    """Reviewer rating → number. Handles int/float and ``"8: accept"`` strings."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        m = _RATING_LEAD_INT.search(raw)
        if m:
            return float(m.group())
    return None


class OpenReviewClient:
    """Authenticated api2.openreview.net client. Disabled (``enabled == False``)
    when creds are absent or ``allow_network=False`` — then every call is a no-op."""

    def __init__(
        self,
        *,
        venue_hosts: tuple[str, ...],
        year_min: int,
        username: str | None = None,
        password: str | None = None,
        cache: OpenAlexCache | None = None,
        http_client: httpx.Client | None = None,
        allow_network: bool = True,
    ) -> None:
        self.venue_hosts = tuple(h for h in venue_hosts if h)
        self.year_min = int(year_min)
        # Secrets read at point of use; the values never enter config or logs.
        self._user = (username if username is not None else os.getenv("OPENREVIEW_USERNAME") or "").strip()
        self._pw = (password if password is not None else os.getenv("OPENREVIEW_PASSWORD") or "").strip()
        self.cache = cache
        self._http = http_client or httpx.Client(
            timeout=_TIMEOUT_SECS, follow_redirects=True,
            headers={"User-Agent": "zotero-summarizer/1.0"},
        )
        self.allow_network = bool(allow_network)
        self._token: str | None = None

    @property
    def enabled(self) -> bool:
        return self.allow_network and bool(self._user and self._pw)

    def _login(self) -> bool:
        resp = self._http.post(f"{_BASE}/login", json={"id": self._user, "password": self._pw})
        if resp.status_code != 200:
            LOGGER.warning("openreview login failed: HTTP %d", resp.status_code)
            return False
        self._token = (resp.json() or {}).get("token")
        return bool(self._token)

    def _get(self, path: str, params: dict, *, _retry_auth: bool = True) -> dict | None:
        """Authenticated GET. Logs in on first use; on a 401 re-logs in once + retries."""
        if self._token is None and not self._login():
            return None
        _RATE_LIMITER.acquire()
        resp = self._http.get(
            f"{_BASE}{path}", params=params, headers={"Authorization": f"Bearer {self._token}"}
        )
        if resp.status_code == 401 and _retry_auth:
            self._token = None
            return self._get(path, params, _retry_auth=False)
        if resp.status_code != 200:
            LOGGER.debug("openreview GET %s: HTTP %d", path, resp.status_code)
            return None
        return resp.json()

    def search(self, query: str, *, limit: int) -> list[dict]:
        payload = self._get("/notes/search", {"query": query, "limit": max(1, min(limit, 100))})
        return (payload or {}).get("notes") or []

    def forum_reviews(self, forum_id: str) -> tuple[float | None, int]:
        """Mean Official_Review rating + count for a forum. Cached per forum
        (``openreview:reviews:<id>``) since it is stable and the per-paper fan-out
        is the channel's cost."""
        cache_key = f"openreview:reviews:{forum_id}"
        if self.cache is not None:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return (hit.get("mean"), int(hit.get("n") or 0))
        payload = self._get("/notes", {"forum": forum_id})
        ratings: list[float] = []
        for note in (payload or {}).get("notes") or []:
            if not any("Official_Review" in inv for inv in (note.get("invitations") or [])):
                continue
            r = _parse_rating(_val((note.get("content") or {}).get("rating")))
            if r is not None:
                ratings.append(r)
        mean = sum(ratings) / len(ratings) if ratings else None
        if self.cache is not None and payload is not None:
            self.cache.set(cache_key, {"mean": mean, "n": len(ratings)})
        return (mean, len(ratings))


def _in_venue_window(client: OpenReviewClient, venueid: str) -> bool:
    if not any(h.lower() in venueid.lower() for h in client.venue_hosts):
        return False
    years = [int(y) for y in _YEAR.findall(venueid)]
    return bool(years) and max(years) >= client.year_min


def _hit_from_content(client: OpenReviewClient, content: dict, forum_id: str) -> OpenReviewHit | None:
    title = str(_val(content.get("title")) or "").strip()
    venue = str(_val(content.get("venue")) or "").strip()
    venueid = str(_val(content.get("venueid")) or "").strip()
    if not title or not _is_accepted(venue) or not _in_venue_window(client, venueid):
        return None
    authors = _val(content.get("authors")) or []
    year_m = _YEAR.search(venueid) or _YEAR.search(venue)
    mean_rating, n_reviews = client.forum_reviews(forum_id)
    return OpenReviewHit(
        title=title,
        abstract=str(_val(content.get("abstract")) or "").strip(),
        authors=[str(a).strip() for a in authors if str(a).strip()],
        year=int(year_m.group()) if year_m else None,
        venue=venue,
        venueid=venueid,
        forum_id=forum_id,
        url=f"https://openreview.net/forum?id={forum_id}",
        tier=_classify_tier(venue, venueid),
        mean_rating=mean_rating,
        n_reviews=n_reviews,
    )


def search_openreview(
    client: OpenReviewClient, query: str, *, limit: int = 25
) -> list[OpenReviewHit]:
    """Topic-search OpenReview → accepted papers at configured venues in the year
    window, each with its reviewer-rating aggregate. Returns ``[]`` when the client
    is disabled or on any transport/parse error — never raises (leaf contract)."""
    query = (query or "").strip()
    if not client.enabled or not query:
        return []
    try:
        notes = client.search(query, limit=limit)
        by_forum: dict[str, dict] = {}
        for note in notes:
            content = note.get("content") or {}
            forum_id = note.get("id") if "title" in content else note.get("forum")
            if forum_id and "title" in content:
                by_forum.setdefault(forum_id, content)
        hits = [_hit_from_content(client, content, fid) for fid, content in by_forum.items()]
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        LOGGER.debug("openreview search failed: %s", exc)
        return []
    return [h for h in hits if h is not None]


__all__ = ["OpenReviewHit", "OpenReviewClient", "search_openreview"]
