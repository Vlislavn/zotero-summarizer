"""Random below-the-bar cards in analyzed Today (pure; no I/O).

Ranked-only exposure leaves no unbiased data about what the bar hides, so up to
two analyzed papers per local day are shown from just below it, marked
``role="exploration"`` and logged with the slate. One pool remains (the
card's ``recommendation.reason`` names it in the log): ``quality_excluded`` —
quality band ``flag`` only (grade D stays hidden). The former ``no_goal_match``
pool is gone with the goal gate: the board measured at chance against the owner's
verdicts, so a current review is no longer excluded for missing a standing goal.

Pick = deterministic rendezvous hash of ``(day, stable key)``, up to the day
limit from that pool. An exploration card is added to free
slots, and may displace only the lowest-ranked eligible card when that is not a
``must_read``; otherwise the slot is dropped. The day limit counts items already
shown today (``carried``), so repeated assemblies never exceed it.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from typing import Any

from zotero_summarizer.services.triage.daily_select._dataclasses import SlatePaper

POOL = "quality_excluded"
ROLE = "exploration"


def pool_name(cand: dict[str, Any], entry: dict[str, Any] | None) -> str | None:
    """The exploration pool of an INELIGIBLE candidate, else ``None``."""
    reason = cand["recommendation"]["reason"]
    if reason == "quality_excluded":
        quality = (entry or {}).get("quality") or {}
        grade = str(quality.get("grade") or "").strip().upper()
        if str(quality.get("quality_band") or "").strip().lower() == "flag" and grade != "D":
            return reason
    return None


def _digest(day: str, cand: dict[str, Any]) -> str:
    key = cand.get("stable_feed_key") or cand["item_key"]
    return hashlib.sha256(f"explore-v1|{day}|{key}".encode()).hexdigest()


def choose(
    pools: dict[str, list[dict[str, Any]]], *, day: str, limit: int, carried: dict[int, str],
) -> list[dict[str, Any]]:
    """Today's exploration candidates: still-unhandled ``carried`` ones plus new picks
    while ``limit`` allows. ``carried`` = ``{item_id: pool}`` already shown today."""
    pool = pools.get(POOL, [])
    kept = [c for c in pool if c["id"] in carried][:limit]
    fresh = sorted((c for c in pool if c["id"] not in carried), key=lambda c: _digest(day, c))
    return kept + fresh[:max(0, limit - len(carried))]


def splice(papers: list[SlatePaper], picks: list[SlatePaper], *, K: int, day: str) -> list[SlatePaper]:
    """Insert ``picks`` at seeded positions, never exceeding ``K`` or dropping a must-pick."""
    base, accepted = list(papers), []
    for pick in picks:
        if len(base) + len(accepted) >= K:
            if not base or base[-1].recommendation.get("priority") == "must_read":
                continue
            base.pop()
        accepted.append(replace(pick, role=ROLE))
    for pick in accepted:
        position = random.Random(f"{day}|{pick.item_id}").randint(0, len(base))
        base.insert(position, pick)
    return base
