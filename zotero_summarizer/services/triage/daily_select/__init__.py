"""Phase 1.17 Step 1 — daily slate assembly (public package surface).

Public API:

  * :class:`SlatePaper` — one card.
  * :class:`DailySlate` — the assembled slate plus metadata.
  * :func:`assemble_daily_slate` — entry point used by the ``/api/daily`` route.

Composition: (K-2) model + 1 surprise + 1 diversity (default K=5 -> 3/1/1), with
a 25-item backlog cap and 168 h lookback by default. The model quota scales with
K so a larger K actually yields more cards (surprise/diversity stay at 1 each);
without this the fixed 3/1/1 roles capped every slate at 5 regardless of K. The
former ``audit`` slot (gate-rejected
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

from zotero_summarizer.domain import normalize_arxiv_id, normalize_doi
from zotero_summarizer.services.triage.daily_select._allocation import allocate
from zotero_summarizer.services.triage.daily_select._candidate import dedup_keep_newest
from zotero_summarizer.services.triage.daily_select._dataclasses import DailySlate, SlatePaper
from zotero_summarizer.services.triage.daily_select._querying import (
    fetch_decided_content_keys,
    fetch_handled_keys,
    fetch_recent_rows_by_decisions,
    fetch_rows_by_decisions,
    fetch_trashed_guids,
    open_ro,
)

LOGGER = logging.getLogger(__name__)

# Decision strings — duplicated here so this package has no dependency on
# storage.feeds (the storage subagent owns that file). Kept in lock-step
# with storage/feeds.py's DECISION_* constants.
_DECISION_AWAITING_REVIEW = "awaiting_review"
_DECISION_TRIAGED_PENDING = "triaged_pending"
_DECISION_GATE_REJECTED = "gate_rejected"

# "Blocking" states for the content-dedup guard: a paper the user already
# decided on (added/trashed) or that the daemon already filtered as a library /
# processed duplicate. A live awaiting card whose DOI/arXiv matches one of these
# is a duplicate the user has effectively already handled, so the slate drops it.
_BLOCKING_DECISIONS = [
    "selected",                 # kept into the Inbox by daily selection
    "black_swan",               # kept as a surprise pick
    "user_approved",            # kept via the Review UI
    "user_rejected",            # trashed from Today (strong negative)
    "rejected_dedup_library",   # daemon saw it was already in the library
    "rejected_dedup_processed", # daemon saw an earlier copy of this paper
]

# Outcome strings (mirror storage.feeds_constants.OUTCOME_*) for papers thrown
# away inside Zotero — kept in lock-step with that module, duplicated here so the
# package stays free of a storage.feeds import (see the decision-strings note).
_BLOCKING_OUTCOMES = ["trashed", "deleted_all"]

# A paper the user *threw away* — its stable GUID is suppressed from the slate
# forever ("trash → never show again"), regardless of whether it carries a
# DOI/arXiv to content-dedup on. ``user_rejected`` = trashed from Today;
# the outcomes = trashed/deleted inside Zotero.
_TRASH_DECISIONS = ["user_rejected"]
_TRASH_OUTCOMES = _BLOCKING_OUTCOMES

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


def _drop_trashed_guids(rows: list[dict], *, trashed_guids: set[str]) -> list[dict]:
    """Drop rows whose stable GUID matches a paper the user explicitly trashed.

    The durable "trash → never show again" guard: it catches a re-arrival of a
    thrown-away paper even when it carries no DOI/arXiv (so ``_drop_content_dupes``
    can't see it) and arrived under a fresh ``feed_item_id`` (so ``_drop_handled``
    can't see it either). The GUID is the one identifier that survives Zotero
    reassigning feed-item ids across re-ingestions.
    """
    if not trashed_guids:
        return rows
    out: list[dict] = []
    for row in rows:
        guid = str(row.get("guid") or "").strip()
        if guid and guid in trashed_guids:
            continue
        out.append(row)
    return out


def _drop_content_dupes(
    rows: list[dict],
    *,
    blocked_doi: set[str],
    blocked_arxiv: set[str],
) -> list[dict]:
    """Drop awaiting cards that duplicate (by DOI/arXiv) a paper already decided
    on / in the library, and collapse same-paper copies that arrived under
    different GUIDs (keep newest by ``created_at``).

    DOI/arXiv-only: a row carrying neither id is never dropped, so a genuinely
    distinct paper can never be filtered out by mistake. This is the slate-side
    guard for the gap the daemon's identity dedup leaves — a paper re-entering
    under a new GUID, or one added to the library after it was already triaged.
    """
    survivors: list[dict] = []
    seen_doi: set[str] = set()
    seen_arxiv: set[str] = set()
    for row in sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True):
        doi = normalize_doi(str(row.get("doi") or ""))
        arxiv = normalize_arxiv_id(str(row.get("arxiv_id") or ""))
        if (doi and doi in blocked_doi) or (arxiv and arxiv in blocked_arxiv):
            continue
        if (doi and doi in seen_doi) or (arxiv and arxiv in seen_arxiv):
            continue
        if doi:
            seen_doi.add(doi)
        if arxiv:
            seen_arxiv.add(arxiv)
        survivors.append(row)
    return survivors


def _fetch_primary_unhandled(
    conn,
    *,
    lookback_hours: int,
    backlog_cap: int,
    now: datetime,
) -> tuple[list[dict], bool]:
    """Primary-pool rows (awaiting_review + triaged_pending) within the window,
    minus papers the user already acted on AND minus content duplicates (by
    DOI/arXiv) of a paper already decided on / in the library, with the
    never-empty recent fallback. Returns ``(rows, fellback_to_recent)``.

    The single source of truth for "what's genuinely awaiting the user" — both
    the slate and the header counter consume this so they never disagree.
    """
    handled_guids, handled_label_keys = fetch_handled_keys(conn)
    blocked_doi, blocked_arxiv = fetch_decided_content_keys(
        conn,
        blocking_decisions=_BLOCKING_DECISIONS,
        blocking_outcomes=_BLOCKING_OUTCOMES,
    )
    trashed_guids = fetch_trashed_guids(
        conn,
        trashed_decisions=_TRASH_DECISIONS,
        trashed_outcomes=_TRASH_OUTCOMES,
    )

    def _clean(rows: list[dict]) -> list[dict]:
        rows = _drop_trashed_guids(rows, trashed_guids=trashed_guids)
        rows = _drop_handled(
            rows, handled_guids=handled_guids, handled_label_keys=handled_label_keys,
        )
        return _drop_content_dupes(
            rows, blocked_doi=blocked_doi, blocked_arxiv=blocked_arxiv,
        )

    primary_decisions = [_DECISION_AWAITING_REVIEW, _DECISION_TRIAGED_PENDING]
    rows = _clean(fetch_rows_by_decisions(
        conn, decisions=primary_decisions, lookback_hours=lookback_hours, now=now,
    ))
    fellback = False
    if not rows:
        rows = _clean(fetch_recent_rows_by_decisions(
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
    if roles is not None:
        effective_roles = dict(roles)
    else:
        # Scale the model quota with K so a larger K yields more cards; without
        # this the fixed 3/1/1 default capped every slate at 5 (allocate() fills
        # each role once). surprise/diversity stay at 1. K=5 -> {3,1,1} (legacy).
        effective_roles = dict(_DEFAULT_ROLES)
        effective_roles["model"] = max(_DEFAULT_ROLES["model"], K - 2)
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
