"""Phase 1.17 Step 1 — daily slate assembly (public package surface).

Public API:

  * :class:`SlatePaper` — one card.
  * :class:`DailySlate` — the assembled slate plus metadata.
  * :func:`assemble_daily_slate` — entry point used by the ``/api/daily`` route.

Composition (user-confirmed): 2 model + 1 surprise + 1 audit + 1 diversity,
with a 25-item backlog cap and 168 h lookback by default. See the Phase
1.17 plan at ``.claude/plans/idea-for-my-zotero-summarizer-harmonic-summit.md``
for the role allocation contract and fallback rules.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from zotero_summarizer.services.daily_select._allocation import allocate
from zotero_summarizer.services.daily_select._candidate import dedup_keep_newest
from zotero_summarizer.services.daily_select._dataclasses import DailySlate, SlatePaper
from zotero_summarizer.services.daily_select._querying import (
    fetch_rows_by_decisions,
    open_ro,
)

LOGGER = logging.getLogger(__name__)

# Decision strings — duplicated here so this package has no dependency on
# storage.feeds (the storage subagent owns that file). Kept in lock-step
# with storage/feeds.py's DECISION_* constants.
_DECISION_AWAITING_REVIEW = "awaiting_review"
_DECISION_TRIAGED_PENDING = "triaged_pending"
_DECISION_GATE_REJECTED = "gate_rejected"

_DEFAULT_ROLES: dict[str, int] = {
    "model": 2,
    "surprise": 1,
    "audit": 1,
    "diversity": 1,
}


def assemble_daily_slate(
    *,
    db_path: Path,
    K: int = 5,
    roles: dict[str, int] | None = None,
    backlog_cap: int = 25,
    lookback_hours: int = 168,
    now: datetime | None = None,
) -> DailySlate:
    """Build today's role-allocated slate.

    Steps (see module docstring for the full contract):

      1. Fetch ``awaiting_review`` + ``triaged_pending`` rows from the last
         ``lookback_hours``.
      2. Dedup by ``item_key`` keeping newest by ``created_at``.
      3. Cap to ``backlog_cap`` by composite_score desc -> candidate pool.
      4. Fetch ``gate_rejected`` rows separately for the audit role.
      5. Greedy role allocation with model_fallback rolling for empty roles.

    Empty pool is a *valid* domain state: the function returns a
    DailySlate with ``papers=[]``. That is NOT error swallowing — the API
    route surfaces an "inbox empty" message to the user.
    """
    if K <= 0:
        raise ValueError(f"K must be positive; got {K}")
    if backlog_cap <= 0:
        raise ValueError(f"backlog_cap must be positive; got {backlog_cap}")
    if lookback_hours <= 0:
        raise ValueError(f"lookback_hours must be positive; got {lookback_hours}")
    effective_roles = dict(roles) if roles is not None else dict(_DEFAULT_ROLES)
    effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    conn = open_ro(db_path)
    try:
        primary_rows = fetch_rows_by_decisions(
            conn,
            decisions=[_DECISION_AWAITING_REVIEW, _DECISION_TRIAGED_PENDING],
            lookback_hours=lookback_hours,
            now=effective_now,
        )
        audit_rows = fetch_rows_by_decisions(
            conn,
            decisions=[_DECISION_GATE_REJECTED],
            lookback_hours=lookback_hours,
            now=effective_now,
        )
    finally:
        conn.close()

    deduped = dedup_keep_newest(primary_rows)
    audit_pool = dedup_keep_newest(audit_rows)
    pool_size = len(deduped)

    deduped.sort(key=lambda c: c["composite_score"], reverse=True)
    capped = deduped[:backlog_cap]
    capped_at = len(capped)

    # Day-stable RNG so the audit pick is reproducible within a calendar day
    # (plan requirement: "stable within a day" for the gate_rejected sample).
    rng = random.Random(int(effective_now.timestamp() // 86400))

    papers, empty_role_events = allocate(
        candidate_pool=capped,
        audit_pool=audit_pool,
        roles=effective_roles,
        K=K,
        rng=rng,
    )

    LOGGER.info(
        "daily_slate: pool=%d capped=%d K=%d papers=%d empty_roles=%s",
        pool_size,
        capped_at,
        K,
        len(papers),
        empty_role_events,
    )

    return DailySlate(
        papers=papers,
        pool_size=pool_size,
        capped_at=capped_at,
        lookback_hours=lookback_hours,
        empty_role_events=empty_role_events,
    )


__all__ = ["SlatePaper", "DailySlate", "assemble_daily_slate"]
