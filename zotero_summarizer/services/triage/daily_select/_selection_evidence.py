"""Pure projections of already-observed Today selection operations.

Only the small JSON evidence home is copied. Source bodies, review payloads,
configuration and scoring remain with their existing owners.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from zotero_summarizer.domain import ReadingPriority

_KNOWN_PRIORITIES = tuple(priority.value for priority in ReadingPriority)


def _finite_or_none(value: Any) -> float | None:
    """Make an observation JSON-safe without changing its operational value."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def source_observations(row: dict[str, Any]) -> dict[str, Any]:
    """Observe native columns; the candidate owner supplies parsed quality origin."""
    priority = row.get("reading_priority")
    return {
        "stored": {
            "composite_recorded": row.get("composite_score") is not None,
            "reading_priority": (
                priority if isinstance(priority, str) and priority in _KNOWN_PRIORITIES else None
            ),
        },
        "review": {
            "join": "not_observed",
            "join_basis": None,
            "quality_origin": "none",
            "needs_pdf": None,
            "digest_usable": None,
        },
        "rank": {"basis": "not_observed", "key": None},
        "selection": {"basis": "not_observed"},
    }


def review_observations(
    evidence: dict[str, Any] | None,
    *,
    join_basis: str | None,
    entry: dict[str, Any] | None,
    digest_usable: bool | None,
) -> dict[str, Any]:
    """Project the chosen join and the caller's one existing usability result."""
    observed = deepcopy(evidence) if evidence is not None else {}
    review = observed.setdefault("review", {})
    matched = entry is not None
    needs_pdf = entry.get("needs_pdf") if matched else None
    review.update({
        "join": "matched" if matched else "unavailable",
        "join_basis": join_basis if matched else None,
        "needs_pdf": needs_pdf if type(needs_pdf) is bool else None,
        "digest_usable": digest_usable if matched and type(digest_usable) is bool else None,
    })
    if matched:
        review["quality_origin"] = "current_entry"
    return observed


def rank_observations(
    evidence: dict[str, Any] | None,
    *,
    quality_first: bool,
    key: Any,
) -> dict[str, Any]:
    """Project the resolved mode and the same key returned by the cohort scorer."""
    observed = deepcopy(evidence) if evidence is not None else {}
    observed["mode"] = "quality_first" if quality_first else "control"
    observed["rank"] = {
        "basis": "quality_first" if quality_first else "relevance_goal_citation",
        "key": _finite_or_none(key),
    }
    return observed


def picker_observations(
    evidence: dict[str, Any] | None,
    *,
    requested_role: str,
    basis: str,
    key: Any,
    bonus: Any = None,
    bonus_policy: str = "not_used",
    band_restricted: bool = False,
) -> dict[str, Any]:
    """Replace the whole role trace with values captured by the actual picker."""
    observed = deepcopy(evidence) if evidence is not None else {}
    observed["selection"] = {
        "requested_role": requested_role,
        "basis": basis,
        "key": _finite_or_none(key),
        "quality_bonus": _finite_or_none(bonus),
        "bonus_policy": bonus_policy,
        "band_restricted": band_restricted,
    }
    return observed


__all__ = [
    "source_observations",
    "review_observations",
    "rank_observations",
    "picker_observations",
]
