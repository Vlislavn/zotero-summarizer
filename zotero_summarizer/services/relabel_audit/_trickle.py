"""Phase 1.17 Step 3 — daily trickle of audit cards.

Returns at most ``max_per_day`` unanswered audit candidates, gated by a
``rate_limit_hours``-window since the user's last verdict. Selection
priority among the unanswered pool is the bucket with the FEWEST answered
responses (ties broken alphabetically); within the bucket the pick is
uniform with a day-stable RNG.

This module never mutates ``responses``; it only writes the top-level
``last_trickle_emitted_at`` field on a non-empty return.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zotero_summarizer.services.relabel_audit._constants import (
    AGE_BUCKET_NAMES,
    AuditCandidate,
    now_iso,
)
from zotero_summarizer.services.relabel_audit._session import (
    read_session,
    responses_from_session,
)


def _candidates(session: dict) -> list[AuditCandidate]:
    return [
        AuditCandidate(
            item_key=str(c["item_key"]),
            title=str(c["title"]),
            authors=str(c["authors"]),
            venue=str(c["venue"]),
            abstract=str(c["abstract"]),
            days_since_added=int(c["days_since_added"]),
            age_bucket=str(c["age_bucket"]),
            original_priority=str(c["original_priority"]),
            original_inferred_relevance=float(c["original_inferred_relevance"]),
        )
        for c in session["candidates"]
    ]


def _pick_from_buckets(
    unanswered: list[AuditCandidate],
    bucket_order: list[str],
    *,
    max_per_day: int,
    rng: random.Random,
) -> list[AuditCandidate]:
    """Sample `max_per_day` items walking buckets in priority order.

    Within each bucket, day-stable shuffle then take the prefix. Bucket
    fall-through happens when the chosen bucket has fewer than the
    remaining ``need``.
    """
    picked: list[AuditCandidate] = []
    remaining = list(unanswered)
    for bucket in bucket_order:
        if len(picked) >= max_per_day:
            break
        pool = [c for c in remaining if c.age_bucket == bucket]
        if not pool:
            continue
        idx = list(range(len(pool)))
        rng.shuffle(idx)
        need = max_per_day - len(picked)
        picked.extend(pool[i] for i in idx[:need])
        chosen = {c.item_key for c in picked}
        remaining = [c for c in remaining if c.item_key not in chosen]
    return picked


def next_audit_for_today(
    session_path: Path,
    *,
    max_per_day: int = 2,
    now: datetime | None = None,
    rate_limit_hours: int = 24,
) -> list[AuditCandidate]:
    """Return up to ``max_per_day`` trickle audit candidates for the day.

    Contract (Phase 1.17 Step 3):
      * Rate-limit: if the user submitted ANY audit verdict within the last
        ``rate_limit_hours``, return ``[]``.
      * Selection within unanswered: prefer the bucket with the FEWEST
        answered responses (ties broken alphabetically by bucket name).
        Falls through to the next bucket if the chosen one has fewer than
        the remaining need.
      * On non-empty return, write ``last_trickle_emitted_at`` (UTC ISO)
        to the session JSON's top level. ``responses`` is never mutated.

    Returning ``[]`` for "rate-limited" or "nothing unanswered" is a
    documented domain state, not error swallowing.
    """
    if max_per_day <= 0:
        raise ValueError(f"max_per_day must be positive; got {max_per_day}")
    if rate_limit_hours <= 0:
        raise ValueError(f"rate_limit_hours must be positive; got {rate_limit_hours}")

    effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session = read_session(session_path)
    answered = responses_from_session(session)
    answered_keys = {r.item_key for r in answered}

    if answered:
        last_ts = max(
            datetime.fromisoformat(r.timestamp_iso.replace("Z", "+00:00"))
            for r in answered
        )
        if last_ts.astimezone(timezone.utc) > effective_now - timedelta(hours=rate_limit_hours):
            return []

    candidates = _candidates(session)
    unanswered = [c for c in candidates if c.item_key not in answered_keys]
    if not unanswered:
        return []

    counts: dict[str, int] = {b: 0 for b in AGE_BUCKET_NAMES}
    for r in answered:
        counts[r.age_bucket] = counts.get(r.age_bucket, 0) + 1
    bucket_order = sorted(AGE_BUCKET_NAMES, key=lambda b: (counts[b], b))

    rng = random.Random(int(effective_now.timestamp() // 86400))
    picked = _pick_from_buckets(
        unanswered, bucket_order, max_per_day=max_per_day, rng=rng,
    )
    if not picked:
        return []

    session["last_trickle_emitted_at"] = now_iso()
    session_path.write_text(
        json.dumps(session, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return picked


__all__ = ["next_audit_for_today"]
