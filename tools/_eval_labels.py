"""Shared DE-LEAKED firewalled label loader for the slate/triage eval tools.

WHY THIS EXISTS. Both ``eval_slate_blend.py`` and ``eval_triage_score_calibration.py``
historically loaded firewalled labels from ``processed_feed_items.decision``
(``user_approved`` kept vs ``user_rejected`` trashed). That is the PRE-Zotero-native-labels
source: after ADR-G2 (``label:<priority>`` tags became canonical, ``label_verdicts`` the
rebuildable cache), ``decision`` stopped receiving ``user_approved`` — the live DB has only 15
``user_approved`` rows vs **780 user verdicts** in ``label_verdicts``. Every "firewalled"
slate number was therefore measured on a near-empty kept class (the leak), not on data scarcity.

This module is the ONE place the de-leak lives, so both tools agree. See the plan
``.claude/plans/deleaked-slate-eval.md`` and ``docs/decisions.md`` §GAP G2.

Label semantics (the firewall):
* kept  (positive) = ``label_verdicts.user_priority IN (must_read, should_read, could_read)``
  AND ``source = 'user'``.
* trashed (negative) = ``label_verdicts.user_priority = 'dont_read'`` AND ``source = 'user'``.
* ``machine_add`` (provisional Add-to-library) and ``auto_quality`` (the auto gate) are
  MACHINE outputs, not firewalled user decisions — excluded.

Read-only (``mode=ro``); writes nothing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# source='user' ONLY — the firewall (machine_add/auto_quality are NOT user decisions).
_USER_SOURCE = "user"
# Positive (kept) user priorities; dont_read is the negative (trashed) class.
_KEPT_PRIORITIES = ("must_read", "should_read", "could_read")
_TRASHED_PRIORITY = "dont_read"

# Columns every eval tool needs from the joined feed row (callers may SELECT more).
FEED_COLUMNS = (
    "p.id", "p.title", "p.abstract", "p.decision", "p.composite_score",
    "p.shap_contribs_json", "p.materialized_zotero_key",
)


def load_firewalled_labels(db_path: Path) -> tuple[list[dict[str, Any]], list[int]]:
    """Return ``(rows, labels)`` for the de-leaked firewalled GT.

    ``rows[i]`` is a dict of ``FEED_COLUMNS`` (plus ``verdict_priority``/``verdict_source``);
    ``labels[i]`` is ``1`` (kept) or ``0`` (trashed). User verdicts live on TWO key shapes:
    ``feed:<feed_item_id>`` (Today verdicts, the majority) and library keys
    (``materialized_zotero_key``). Both are joined here so the firewall reaches every user
    verdict, not just the materialized subset. Requires a non-null ``composite_score`` (the rank
    key) and a non-empty abstract (the goal_sim input), matching the eval tools' preconditions.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(_KEPT_PRIORITIES))
    cols = "p.id, p.title, p.abstract, p.decision, p.composite_score, " \
           "p.shap_contribs_json, p.materialized_zotero_key, " \
           "v.user_priority AS verdict_priority, v.source AS verdict_source"
    # Two join paths UNION'd (then DISTINCT on p.id so a row verdicted under both keys is
    # counted once): feed:<id> -> feed_item_id (Today verdicts, the majority); library key
    # -> materialized_zotero_key. One query, read-only.
    try:
        cur = conn.execute(
            f"""SELECT * FROM (
                SELECT {cols} FROM processed_feed_items p
                JOIN label_verdicts v ON v.item_key = 'feed:' || p.feed_item_id
                WHERE v.source = ? AND (v.user_priority IN ({placeholders}) OR v.user_priority = ?)
                  AND p.composite_score IS NOT NULL AND TRIM(p.abstract) != ''
                UNION
                SELECT {cols} FROM processed_feed_items p
                JOIN label_verdicts v ON v.item_key = p.materialized_zotero_key
                WHERE v.source = ? AND (v.user_priority IN ({placeholders}) OR v.user_priority = ?)
                  AND p.composite_score IS NOT NULL AND TRIM(p.abstract) != ''
                ) ORDER BY id""",
            (_USER_SOURCE, *_KEPT_PRIORITIES, _TRASHED_PRIORITY,
             _USER_SOURCE, *_KEPT_PRIORITIES, _TRASHED_PRIORITY),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    labels = [0 if r["verdict_priority"] == _TRASHED_PRIORITY else 1 for r in rows]
    return rows, labels


def legacy_decision_labels(db_path: Path) -> tuple[list[dict[str, Any]], list[int]]:
    """The OLD (leaked) label source — kept ONLY as a rejected-option receipt.

    Loads from ``processed_feed_items.decision`` (``user_approved`` vs ``user_rejected``).
    After the Zotero-native-labels migration this yields a near-empty kept class (15 rows
    live); use it only to reproduce the leaked baseline, never to ship a number. The de-leaked
    ``load_firewalled_labels`` is the firewall now.
    """
    kept = ("user_approved",)
    trashed = ("user_rejected",)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            f"""SELECT {', '.join(FEED_COLUMNS)}
                FROM processed_feed_items p
                WHERE p.decision IN (?, ?)
                  AND p.composite_score IS NOT NULL
                  AND (p.abstract IS NOT NULL AND TRIM(p.abstract) != '')""",
            kept[0], trashed[0],
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    labels = [0 if r["decision"] in trashed else 1 for r in rows]
    return rows, labels
