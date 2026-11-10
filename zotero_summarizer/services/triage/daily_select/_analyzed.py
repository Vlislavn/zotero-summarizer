"""Analyzed Today: show only usable reviews (current, or outdated of the same PDF —
badged via ``review_freshness``) that pass ``recommendation``.

Below the cards, ``DailySlate.unreviewed`` is a shelf of the best-ranked pool
candidates with no usable review (same rank key; never Add-eligible, no verdicts).
Cards and shelf list one entry per normalized title (best-ranked copy wins).

No roles, quotas, surprise picks or old-row filler: an empty slate is a valid
answer when nothing analyzed qualifies. Order: reading priority, independent
quality grade, the rank key, then newest. The default ranker ``goal_cos_v1``
(``_goal_rank``) keys on goal cosine and drops the quality-grade bonus.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zotero_summarizer.services.model.rank_blend import quality_bonus
from zotero_summarizer.services.triage.daily_select._allocation import _to_slate_paper
from zotero_summarizer.services.triage import today_exposure
from zotero_summarizer.services.triage.daily_select import _exploration
from zotero_summarizer.services.triage.daily_select._candidate import row_created_at
from zotero_summarizer.services.triage.daily_select._goal_rank import GOAL_COS, attach_goal_cos_keys
from zotero_summarizer.services.triage.daily_select._dataclasses import DailySlate
from zotero_summarizer.services.triage.daily_select._recommendation import recommendation, review_entry
from zotero_summarizer.settings import today_exploration_slots, today_ranker

POLICY = "analyzed-today-v1"
UNREVIEWED_ROLE = "unreviewed"
# Shelf = "no review a reader can use": none at all, or one without readable source text.
# pdf_changed / source_unresolved reviews are stale for a changed PDF, not "unreviewed".
_SHELF_REASONS = frozenset({"no_current_review", "source_unavailable"})
_PRIORITY_ORDER = {"must_read": 0, "should_read": 1, "could_read": 2}


def _title_key(cand: dict[str, Any]) -> str:
    """Normalized title: one paper arrives under several feed keys (arXiv versions, cross-posted
    feeds, same-title series), so identity for display is the title, as in the Library queue."""
    return "".join(ch for ch in (cand.get("title") or "").lower() if ch.isalnum())


def _first_per_title(cands: list, seen: set[str], title=_title_key) -> list:
    """Keep the first (best-ranked) entry per title; ``seen`` carries titles already shown."""
    out = []
    for cand in cands:
        key = title(cand)
        if key and key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def exploration_context(now: datetime | None) -> dict[str, Any]:
    """``build_analyzed_slate`` exploration arguments for the LOCAL day of ``now``."""
    day = (now or datetime.now(timezone.utc)).astimezone().date().isoformat()
    limit = today_exploration_slots()
    return {"day": day, "exploration_limit": limit,
            "explored": today_exposure.explored_today(day) if limit else {}}


def build_analyzed_slate(
    candidates: list[dict[str, Any]], reviews: dict[str, Any], *, K: int, lookback_hours: int,
    day: str = "", exploration_limit: int = 0, explored: dict[int, str] | None = None,
) -> DailySlate:
    """Up to ``K`` eligible candidates; ``pool_size`` counts every eligible one.
    ``exploration_limit`` > 0 adds marked below-the-bar cards (``_exploration``)."""
    goal_cos = today_ranker() == GOAL_COS  # goal_cos_v1 drops the capped quality bonus: no measured lift
    if goal_cos:
        attach_goal_cos_keys(candidates)
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    below: dict[str, list[dict[str, Any]]] = {}
    shelf: list[dict[str, Any]] = []
    for cand in candidates:
        entry = review_entry(cand, reviews)
        cand["recommendation"] = decision = recommendation(entry)
        cand["review_freshness"] = (entry or {}).get("review_freshness")
        if entry is None or decision["reason"] in _SHELF_REASONS:
            shelf.append(cand)
        if not decision["eligible"]:
            pool = _exploration.pool_name(cand, entry)
            if pool:
                below.setdefault(pool, []).append(cand)
            continue
        quality = entry.get("quality") or {}
        key = (
            _PRIORITY_ORDER[decision["priority"]],
            0.0 if goal_cos else -quality_bonus(quality.get("quality_band"), quality.get("grade"), use_band=False),
            -cand["rank_score"],
            -row_created_at(cand).timestamp(),
            -cand["id"],
        )
        ranked.append((key, cand))
    ranked.sort(key=lambda pair: pair[0])
    shown: set[str] = set()
    ranked = _first_per_title(ranked, shown, title=lambda pair: _title_key(pair[1]))
    papers = [_to_slate_paper(cand, role="model", selection=cand["selection_evidence"])
              for _, cand in ranked[:K]]
    if exploration_limit:
        picks = _exploration.choose(below, day=day, limit=exploration_limit, carried=explored or {})
        papers = _exploration.splice(papers, [
            _to_slate_paper(cand, role=_exploration.ROLE, selection=cand["selection_evidence"])
            for cand in picks], K=K, day=day)
    shelf.sort(key=lambda cand: (-cand["rank_score"], -row_created_at(cand).timestamp(), -cand["id"]))
    unreviewed = [_to_slate_paper(cand, role=UNREVIEWED_ROLE, selection=cand["selection_evidence"])
                  for cand in _first_per_title(shelf, shown)[:K]]
    return DailySlate(papers=papers, pool_size=len(ranked), lookback_hours=lookback_hours,
                      selection_policy=POLICY, unreviewed=unreviewed)
