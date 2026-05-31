"""Library score-distribution + prestige-floor helpers (split from
``reading_queue`` to keep that module ≤500 LOC).

Pure functions over already-scored cache entries — no I/O, no runtime state:

  * :func:`_entry_prestige` — read ``(prestige_score, known)`` from a cache entry
  * :func:`prestige_floor` — the data-driven median-of-known quality floor
  * :func:`score_distribution` — bin scores into the Library histogram, tallying
    ``by_band`` AFTER the floor (low-prestige top items count one band lower)

``reading_queue`` re-exports these so ``reading_queue.prestige_floor`` etc. stay
the public seam for ``score_tags`` and the tests.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.domain import apply_prestige_floor, score_to_priority

# Score histogram: 8 fixed 0.5-wide bins across the 1–5 relevance scale, each
# coloured by the band its centre falls in (domain.score_to_priority).
_HIST_EDGES: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)


def _entry_prestige(entry: dict[str, Any] | None) -> tuple[float | None, bool]:
    """``(prestige_score, prestige_known)`` from a score-cache entry. ``known`` =
    OpenAlex returned a real field-normalized ``citation_percentile``; a missing
    record OR a cold-start / uncited work (no percentile) is *unknown* so the
    quality floor never penalises it (a young paper isn't "low prestige")."""
    sc = (entry or {}).get("scoring") or {}
    inp = sc.get("prestige_inputs") or {}
    known = inp.get("citation_percentile") is not None
    return sc.get("prestige_score"), bool(known)


def prestige_floor(pairs: list[tuple[float | None, bool]]) -> float | None:
    """Data-driven quality floor = the MEDIAN of the library's KNOWN prestige
    scores — a parameter-free "at least typical quality" bar (no tuned quantile).
    Returns None when nothing has known prestige, so the floor is inert on a
    library with no OpenAlex coverage. ``pairs`` are ``(prestige_score, known)``."""
    vals = sorted(p for p, known in pairs if known and p is not None)
    if not vals:
        return None
    return float(vals[len(vals) // 2])


def score_distribution(records: list[dict[str, Any]], floor: float | None = None) -> dict[str, Any]:
    """Bin the unread queue's relevance scores into the histogram the Library
    page renders. X = score bin (coloured by the bin's score band); ``by_band``
    is the EFFECTIVE-band tally AFTER the prestige floor (low-prestige top items
    count one band lower), so the legend reflects the quality-gated bands the
    tags use. Records with no score (unscored) are reported separately."""
    bins = [
        {"lo": _HIST_EDGES[i], "hi": _HIST_EDGES[i + 1], "count": 0,
         "band": score_to_priority((_HIST_EDGES[i] + _HIST_EDGES[i + 1]) / 2.0)}
        for i in range(len(_HIST_EDGES) - 1)
    ]
    by_band: dict[str, int] = {"must_read": 0, "should_read": 0, "could_read": 0, "dont_read": 0}
    scored = 0
    unscored = 0
    for rec in records:
        s = rec.get("relevance_score")
        if s is None:
            unscored += 1
            continue
        scored += 1
        s = min(5.0, max(1.0, float(s)))
        idx = min(len(bins) - 1, int((s - 1.0) / 0.5))
        bins[idx]["count"] += 1
        band = apply_prestige_floor(
            score_to_priority(s), rec.get("prestige_score"),
            prestige_known=bool(rec.get("prestige_known")), floor=floor,
        )
        by_band[band] += 1
    return {
        "bins": bins, "by_band": by_band, "total_scored": scored, "unscored": unscored,
        "prestige_floor": floor,
    }


__all__ = ["_HIST_EDGES", "_entry_prestige", "prestige_floor", "score_distribution"]
