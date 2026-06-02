"""repositories: verdicts queries (split)."""
from __future__ import annotations

import json  # noqa: F401
import sqlite3  # noqa: F401
from datetime import date, datetime, timezone
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from zotero_summarizer.domain import (  # noqa: F401
    ChangeStatus,
    EXPLICIT_FEEDBACK_SIGNALS,
    READING_PRIORITY_SORT_RANK,
)
from zotero_summarizer.storage.repositories import (  # noqa: F401
    _AB_DECISION_THRESHOLD,
    _AB_WINNING_MARGIN,
    _VALID_AB_WINNERS,
    _VALID_VERDICTS,
    _connect_to,
    _get_columns,
    _get_conn,
    _json_to_list,
    _normalize_order,
    _normalize_sort,
    _rows_to_dicts,
    _sort_expression,
)


def insert_role_value_verdict(
    db_path: Path,
    *,
    item_key: str,
    role: str,
    verdict: str,
    composite_score: float | None = None,
    surprise_score: float | None = None,
    corpus_affinity: float | None = None,
) -> int:
    """Record one role-value verdict (one row per item_key+role); returns its id.

    Re-rating the same slot OVERWRITES the prior verdict: the user's mental
    model is "this is my rating of this paper", not an append-only event log.
    We delete any existing (item_key, role) rows in the same transaction
    before inserting, which also collapses legacy duplicates the moment a
    paper is re-rated. ``list_role_verdicts_summary`` counts rows, so one row
    per slot keeps the win-rate stats from being skewed by double-clicks.

    Fails fast on bad inputs:
    - empty item_key / role
    - verdict not in {'worth', 'waste', 'unknown'}
    """
    safe_item_key = str(item_key or "").strip()
    safe_role = str(role or "").strip()
    safe_verdict = str(verdict or "").strip()
    if not safe_item_key:
        raise ValueError("item_key is required")
    if not safe_role:
        raise ValueError("role is required")
    if safe_verdict not in _VALID_VERDICTS:
        raise ValueError(
            f"verdict must be one of {_VALID_VERDICTS}; got {verdict!r}"
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = _connect_to(db_path)
    try:
        conn.execute(
            "DELETE FROM role_value_verdicts WHERE item_key = ? AND role = ?",
            (safe_item_key, safe_role),
        )
        cursor = conn.execute(
            """
            INSERT INTO role_value_verdicts (
                item_key, role, verdict, composite_score, surprise_score,
                corpus_affinity, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                safe_item_key,
                safe_role,
                safe_verdict,
                None if composite_score is None else float(composite_score),
                None if surprise_score is None else float(surprise_score),
                None if corpus_affinity is None else float(corpus_affinity),
                now_iso,
            ),
        )
        conn.commit()
        new_id = cursor.lastrowid
        if new_id is None:
            raise RuntimeError("INSERT did not produce a lastrowid")
        return int(new_id)
    finally:
        conn.close()


def get_role_verdicts_by_keys(
    db_path: Path, item_keys: list[str]
) -> dict[str, str]:
    """Return ``{item_key: latest_verdict}`` for the given keys.

    Used to hydrate the Today slate so a previously-rated card shows its
    "Rated: …" badge after a page reload. Picks the most recent verdict per
    key (``MAX(id)``) so legacy pre-dedup duplicates resolve to the newest.
    Keys with no verdict are simply absent from the result.
    """
    keys = [str(k or "").strip() for k in item_keys if str(k or "").strip()]
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT item_key, verdict
            FROM role_value_verdicts
            WHERE id IN (
                SELECT MAX(id) FROM role_value_verdicts
                WHERE item_key IN ({placeholders})
                GROUP BY item_key
            )
            """,
            keys,
        ).fetchall()
    finally:
        conn.close()
    return {str(r["item_key"]): str(r["verdict"]) for r in rows}


def get_label_priorities_by_pks(
    db_path: Path, pks: list[int]
) -> dict[int, str]:
    """Return ``{processed_feed_items.id: user_priority}`` for the given PKs.

    Labels live in ``label_verdicts`` keyed ``feed:<feed_item_id>`` while the
    slate carries the ``processed_feed_items`` PK, so this joins the two to
    hydrate the Today card's must/should/could/don't button after a reload.
    PKs without a manual label are absent from the result.
    """
    safe_pks = [int(p) for p in pks if isinstance(p, int) or str(p).isdigit()]
    if not safe_pks:
        return {}
    placeholders = ",".join("?" for _ in safe_pks)
    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT p.id AS pk, lv.user_priority AS user_priority
            FROM processed_feed_items p
            JOIN label_verdicts lv
              ON lv.item_key = 'feed:' || p.feed_item_id
            WHERE p.id IN ({placeholders})
            """,
            safe_pks,
        ).fetchall()
    finally:
        conn.close()
    return {int(r["pk"]): str(r["user_priority"]) for r in rows}


def list_role_verdicts_summary(db_path: Path) -> dict[str, dict[str, Any]]:
    """Return per-role win-rate stats with binomial Wilson 95% CI.

    Schema per role::

        {"worth": int, "waste": int, "unknown": int,
         "win_rate": float | None, "ci_low": float | None,
         "ci_high": float | None, "n": int}

    ``win_rate = worth / (worth + waste)``. Roles with ``worth + waste < 5``
    receive ``ci_low = ci_high = None`` (sample too small for reliable CI).
    """
    from scipy.stats import binomtest

    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            """
            SELECT role, verdict, COUNT(*) AS cnt
            FROM role_value_verdicts
            GROUP BY role, verdict
            """
        ).fetchall()
    finally:
        conn.close()

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        role = str(row["role"])
        verdict = str(row["verdict"])
        if verdict not in _VALID_VERDICTS:
            raise ValueError(
                f"Found invalid verdict {verdict!r} for role {role!r} in DB"
            )
        bucket = out.setdefault(
            role, {"worth": 0, "waste": 0, "unknown": 0}
        )
        bucket[verdict] = int(row["cnt"] or 0)

    for role, bucket in out.items():
        worth = int(bucket["worth"])
        waste = int(bucket["waste"])
        decided = worth + waste
        bucket["n"] = decided
        if decided == 0:
            bucket["win_rate"] = None
            bucket["ci_low"] = None
            bucket["ci_high"] = None
            continue
        bucket["win_rate"] = worth / decided
        if decided < 5:
            bucket["ci_low"] = None
            bucket["ci_high"] = None
            continue
        ci = binomtest(worth, decided).proportion_ci(0.95, method="wilson")
        bucket["ci_low"] = float(ci.low)
        bucket["ci_high"] = float(ci.high)
    return out


def insert_weekly_ab_verdict(
    db_path: Path,
    *,
    week_start: str,
    winner: str,
    slate_a_keys: list[str],
    slate_b_keys: list[str],
) -> int:
    """Insert one weekly A/B verdict; returns the new row id.

    Fails fast on bad inputs:
    - week_start must be YYYY-MM-DD
    - winner not in {'roles', 'pure_score', 'tied'}
    - slate_a_keys / slate_b_keys must be non-empty lists of strings
    """
    safe_week_start = str(week_start or "").strip()
    safe_winner = str(winner or "").strip()
    if not safe_week_start:
        raise ValueError("week_start is required")
    # Lenient ISO-date validation: YYYY-MM-DD only.
    parsed = datetime.strptime(safe_week_start, "%Y-%m-%d").date()
    if not isinstance(parsed, date):
        raise ValueError(f"week_start must be YYYY-MM-DD; got {week_start!r}")
    if safe_winner not in _VALID_AB_WINNERS:
        raise ValueError(
            f"winner must be one of {_VALID_AB_WINNERS}; got {winner!r}"
        )
    if not isinstance(slate_a_keys, list) or not slate_a_keys:
        raise ValueError("slate_a_keys must be a non-empty list")
    if not isinstance(slate_b_keys, list) or not slate_b_keys:
        raise ValueError("slate_b_keys must be a non-empty list")
    for key in (*slate_a_keys, *slate_b_keys):
        if not isinstance(key, str) or not key.strip():
            raise ValueError("slate keys must be non-empty strings")

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = _connect_to(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO weekly_ab_verdicts (
                week_start, winner, slate_a_keys, slate_b_keys, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                safe_week_start,
                safe_winner,
                json.dumps(list(slate_a_keys), ensure_ascii=False),
                json.dumps(list(slate_b_keys), ensure_ascii=False),
                now_iso,
            ),
        )
        conn.commit()
        new_id = cursor.lastrowid
        if new_id is None:
            raise RuntimeError("INSERT did not produce a lastrowid")
        return int(new_id)
    finally:
        conn.close()


def list_ab_decision_status(db_path: Path) -> dict[str, Any]:
    """Return the current weekly A/B decision status.

    Decision rule: after :data:`_AB_DECISION_THRESHOLD` (8) verdicts total, if
    ``roles >= _AB_WINNING_MARGIN`` (6) the decision locks on ``"roles"``; if
    ``pure_score >= _AB_WINNING_MARGIN`` the decision locks on ``"pure_score"``.
    Otherwise the decision stays ``None``.
    """
    conn = _connect_to(db_path)
    try:
        rows = conn.execute(
            """
            SELECT winner, COUNT(*) AS cnt
            FROM weekly_ab_verdicts
            GROUP BY winner
            """
        ).fetchall()
    finally:
        conn.close()

    counts: dict[str, int] = {w: 0 for w in _VALID_AB_WINNERS}
    for row in rows:
        winner = str(row["winner"])
        if winner not in _VALID_AB_WINNERS:
            raise ValueError(f"Found invalid winner {winner!r} in weekly_ab_verdicts")
        counts[winner] = int(row["cnt"] or 0)

    total = sum(counts.values())
    decision: str | None = None
    decision_locked = False
    if total >= _AB_DECISION_THRESHOLD:
        if counts["roles"] >= _AB_WINNING_MARGIN:
            decision = "roles"
            decision_locked = True
        elif counts["pure_score"] >= _AB_WINNING_MARGIN:
            decision = "pure_score"
            decision_locked = True

    remaining = max(0, _AB_DECISION_THRESHOLD - total)
    return {
        "total": total,
        "roles_wins": counts["roles"],
        "pure_score_wins": counts["pure_score"],
        "tied": counts["tied"],
        "decision_locked": decision_locked,
        "decision": decision,
        "remaining_until_decision": remaining,
    }


__all__ = [
    "insert_role_value_verdict",
    "get_role_verdicts_by_keys",
    "get_label_priorities_by_pks",
    "list_role_verdicts_summary",
    "insert_weekly_ab_verdict",
    "list_ab_decision_status",
]
