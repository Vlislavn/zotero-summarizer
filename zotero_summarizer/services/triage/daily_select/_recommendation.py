"""Whether one usable (current or outdated-same-PDF) deep review earns a place in analyzed Today (pure).

The review cache owns currentness; this module only reads already-usable
projected entries. Eligibility needs every check: no single grade, verdict,
read decision or retrieval hit is enough. It is not Add eligibility.
"""
from __future__ import annotations

from typing import Any

_ASSESSED = frozenset({"full_text", "non_paper"})
_CONSUMPTION = {"read": "original", "skim": "selected_parts", "skip": "brief"}


def review_entry(candidate: dict[str, Any], reviews: dict[str, Any]) -> dict[str, Any] | None:
    """The ``_join_review`` lookup without side effects: materialized key, then stable key."""
    materialized = (candidate.get("materialized_zotero_key") or "").strip()
    entry = reviews.get(materialized) if materialized else None
    if not entry:
        stable = (candidate.get("stable_feed_key") or "").strip()
        entry = reviews.get(stable) if stable else None
    return entry


def _decision(eligible: bool, reason: str, priority: str | None = None,
              consumption: str | None = None) -> dict[str, Any]:
    return {"eligible": eligible, "reason": reason, "priority": priority, "consumption": consumption}


def recommendation(entry: dict[str, Any] | None) -> dict[str, Any]:
    """``{eligible, reason, priority, consumption}``; the first failed check wins."""
    if entry is None:
        return _decision(False, "no_current_review")
    # The review-fleet package also imports feed selection while initializing.
    # Resolve its pure policy only when evaluating a review, not at package load.
    from zotero_summarizer.services.library.quality_gate import should_auto_hide
    from zotero_summarizer.services.library.review_eligibility import same_pdf_entry
    from zotero_summarizer.services.library.review_fleet.propose import propose_verdict

    if entry.get("needs_pdf"):
        return _decision(False, "source_unavailable")
    if not same_pdf_entry(entry):
        return _decision(False, "no_current_review")
    quality = entry.get("quality") or {}
    if quality.get("basis") not in _ASSESSED:
        return _decision(False, "quality_not_assessed")
    if should_auto_hide(relevance_score=None, quality=quality)[0]:
        return _decision(False, "quality_excluded")
    # The standing-goal board is deliberately NOT a check here: measured against the
    # owner's verdicts it was at chance (AUC 0.50, speed/goal-gate.md) while excluding
    # 15 current reviews from Today. It stays a badge on the paper, never a filter.
    digest = entry.get("model_digest") or entry.get("digest")
    priority = propose_verdict(digest, quality, goal_summaries=entry.get("goal_summaries")).proposed
    if priority == "dont_read":
        return _decision(False, "dont_read")
    read = str((entry.get("digest") or {}).get("read_decision") or "")
    return _decision(True, "eligible", priority, _CONSUMPTION.get(read))
