"""Review facts shared by cohort selection and retained-member status reads."""
from __future__ import annotations

import json
from typing import Any

from zotero_summarizer.services.triage.daily_select._selection_evidence import review_observations


def _quality_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Missing legacy quality is empty; malformed stored quality raises."""
    raw = (row.get("quality_review_json") or "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"row id={row.get('id')!r} quality_review_json is not a JSON object")
    return parsed


def _join_review(candidate: dict[str, Any], reviews: dict[str, Any]) -> bool:
    """Join already-current entries; keep legacy quality on a miss, never rank."""
    from zotero_summarizer.services.library.review_eligibility import usable_review

    materialized = (candidate.get("materialized_zotero_key") or "").strip()
    stable = (candidate.get("stable_feed_key") or "").strip()
    entry = None
    basis = None
    if materialized or stable:
        entry = reviews.get(materialized) if materialized else None
        basis = "materialized"
        if not entry:
            entry = reviews.get(stable) if stable else None
            basis = "stable_feed"
    if entry is not None:
        candidate["review_ready"] = usable_review(entry)
        candidate["quality"] = entry.get("quality") or {}
    candidate["selection_evidence"] = review_observations(
        candidate.get("selection_evidence", {}), join_basis=basis, entry=entry,
        digest_usable=candidate["review_ready"] if entry is not None else None,
    )
    return entry is not None
