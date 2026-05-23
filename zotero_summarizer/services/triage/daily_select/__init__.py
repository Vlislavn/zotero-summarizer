"""Phase 1.17 Step 1 — daily slate assembly (public package surface).

Public API:

  * :class:`SlatePaper` — one card.
  * :class:`DailySlate` — the assembled slate plus metadata.
  * :func:`assemble_daily_slate` — entry point used by the ``/api/daily`` route.

Composition: 3 model + 1 surprise + 1 diversity, with a 25-item backlog cap
and 168 h lookback by default. The former ``audit`` slot (gate-rejected
spot-check) was removed from the in-queue slate because an empty primary pool
let it degenerate into an endless one-at-a-time stream of rejected papers;
spot-check now lives in its own labeled Today section + the Review page. The
``audit`` role + ``audit_pool`` plumbing remain for callers that opt in.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from zotero_summarizer.services.triage.daily_select._allocation import allocate
from zotero_summarizer.services.triage.daily_select._candidate import dedup_keep_newest
from zotero_summarizer.services.triage.daily_select._dataclasses import DailySlate, SlatePaper
from zotero_summarizer.services.triage.daily_select._querying import (
    fetch_handled_keys,
    fetch_recent_rows_by_decisions,
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
    "model": 3,
    "surprise": 1,
    "diversity": 1,
}


def _drop_handled(
    rows: list[dict],
    *,
    handled_guids: set[str],
    handled_label_keys: set[str],
) -> list[dict]:
    """Remove rows the user has already acted on (rated or labeled).

    A row is "handled" if its GUID has an after-reading rating, or its
    ``feed:<feed_item_id>`` has a priority label. Handled papers drop out so
    the next-best pick takes their slot (inbox semantics) instead of the
    same cards reappearing every day.
    """
    out: list[dict] = []
    for row in rows:
        guid = str(row.get("guid") or "").strip()
        if guid and guid in handled_guids:
            continue
        fid = row.get("feed_item_id")
        if fid is not None and f"feed:{int(fid)}" in handled_label_keys:
            continue
        out.append(row)
    return out


def _fetch_primary_unhandled(
    conn,
    *,
    lookback_hours: int,
    backlog_cap: int,
    now: datetime,
) -> tuple[list[dict], bool]:
    """Primary-pool rows (awaiting_review + triaged_pending) within the window,
    minus papers the user already acted on, with the never-empty recent
    fallback. Returns ``(rows, fellback_to_recent)``.

    The single source of truth for "what's genuinely awaiting the user" — both
    the slate and the header counter consume this so they never disagree.
    """
    handled_guids, handled_label_keys = fetch_handled_keys(conn)

    def _unhandled(rows: list[dict]) -> list[dict]:
        return _drop_handled(
            rows, handled_guids=handled_guids, handled_label_keys=handled_label_keys,
        )

    primary_decisions = [_DECISION_AWAITING_REVIEW, _DECISION_TRIAGED_PENDING]
    rows = _unhandled(fetch_rows_by_decisions(
        conn, decisions=primary_decisions, lookback_hours=lookback_hours, now=now,
    ))
    fellback = False
    if not rows:
        rows = _unhandled(fetch_recent_rows_by_decisions(
            conn, decisions=primary_decisions, limit=backlog_cap,
        ))
        fellback = bool(rows)
    return rows, fellback


def count_awaiting_unhandled(
    db_path: Path,
    *,
    lookback_hours: int = 168,
    backlog_cap: int = 25,
    now: datetime | None = None,
) -> int:
    """Honest count of papers genuinely awaiting the user's add/trash decision.

    Uses the exact same primary fetch + handled-drop the slate uses, so the
    Today header counter and the slate can never disagree (the old raw
    ``triaged_pending`` count included already-handled papers and lied).
    """
    effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    conn = open_ro(db_path)
    try:
        rows, _fellback = _fetch_primary_unhandled(
            conn, lookback_hours=lookback_hours, backlog_cap=backlog_cap, now=effective_now,
        )
    finally:
        conn.close()
    return len(dedup_keep_newest(rows))


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
        handled_guids, handled_label_keys = fetch_handled_keys(conn)

        def _unhandled(rows: list[dict]) -> list[dict]:
            return _drop_handled(
                rows,
                handled_guids=handled_guids,
                handled_label_keys=handled_label_keys,
            )

        primary_rows, fellback_to_recent = _fetch_primary_unhandled(
            conn, lookback_hours=lookback_hours, backlog_cap=backlog_cap, now=effective_now,
        )
        # Audit pool (gate_rejected) is fetched for callers that opt into the
        # audit role; the default slate no longer allocates it (spot-check now
        # lives in its own clearly-labeled Today section + the Review page).
        audit_rows = _unhandled(fetch_rows_by_decisions(
            conn,
            decisions=[_DECISION_GATE_REJECTED],
            lookback_hours=lookback_hours,
            now=effective_now,
        ))
        if not audit_rows:
            audit_rows = _unhandled(fetch_recent_rows_by_decisions(
                conn, decisions=[_DECISION_GATE_REJECTED], limit=backlog_cap,
            ))
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
        fellback_to_recent=fellback_to_recent,
    )


__all__ = [
    "SlatePaper",
    "DailySlate",
    "assemble_daily_slate",
    "count_awaiting_unhandled",
]
