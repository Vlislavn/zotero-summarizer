"""Concrete one-line "why" for a Read-next row, from the cached SHAP reasons.

The cache keeps ``why_reason`` as the top feature's *name* ("Topic match",
"Title length"), which reads the same on a 1.8★ and a 3.9★ row. Here the top
*informative* positive contributions become a plain sentence about the paper;
bookkeeping features (title/abstract length, DOI, venue present, topic drift)
explain nothing to a reader, so they are skipped. ``None`` = no honest reason.
"""
from __future__ import annotations

from typing import Any

_PHRASE: dict[str, str] = {
    "semantic_match_specter2": "topic close to papers you saved",
    "corpus_affinity": "similar to papers you kept",
    "nearest_kept_cosine": "very similar to a paper you kept",
    "positive_centroid_cosine": "fits your library’s themes",
    "recent_centroid_cosine": "fits what you read lately",
    "prestige_score": "well-cited venue or authors",
    "author_overlap_count": "by authors already in your library",
    "year_recency": "recent paper",
}


def concrete_why(entry: dict[str, Any] | None) -> str | None:
    """Top informative positive SHAP feature of a cache ``entry`` as a phrase."""
    shap = ((entry or {}).get("scoring") or {}).get("shap_top") or []
    pos = [s for s in shap if s.get("feature") in _PHRASE and float(s.get("value") or 0) > 0]
    if not pos:
        return None
    return _PHRASE[max(pos, key=lambda s: float(s["value"]))["feature"]]
