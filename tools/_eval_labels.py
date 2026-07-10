"""Shared DE-LEAKED firewalled label loader for the slate/triage eval tools.

WHY THIS EXISTS. Both ``eval_slate_blend.py`` and ``eval_triage_score_calibration.py``
historically loaded firewalled labels from ``processed_feed_items.decision``
(``user_approved`` kept vs ``user_rejected`` trashed). That is the PRE-Zotero-native-labels
source: after ADR-G2 (``label:<priority>`` tags became canonical, ``label_verdicts`` the
rebuildable cache), ``decision`` stopped receiving ``user_approved`` — the live DB has only 15
``user_approved`` rows vs **1569 user verdicts** in ``label_verdicts``. Every "firewalled"
slate number was therefore measured on a near-empty kept class (the leak), not on data scarcity.

This module is the ONE place the de-leak lives, so both tools agree. It joins the verdicts
across ALL THREE live key shapes (``stable_feed_key`` / ``feed:<feed_item_id>`` /
``materialized_zotero_key``) — an earlier two-path version silently dropped the 721
``feed:g:<sha>`` content-hash verdicts (the majority), yielding kept=5 where the true firewall
is kept=42. See ``load_firewalled_labels`` for the per-shape join + one-row-per-verdict dedup,
the plan ``.claude/plans/deleaked-slate-eval.md`` and ``docs/decisions.md`` §GAP G2.

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
import sys
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


def _key_path(item_key: str) -> str:
    """Which of the 3 join paths an ``item_key`` shape matches (for the yield ladder)."""
    if item_key.startswith("feed:g:"):
        return "stable_feed_key"
    if item_key.startswith("feed:"):
        return "feed:<id>"
    return "materialized_zotero_key"


def load_firewalled_labels(db_path: Path) -> tuple[list[dict[str, Any]], list[int]]:
    """Return ``(rows, labels)`` for the de-leaked firewalled GT.

    ``rows[i]`` is a dict of ``FEED_COLUMNS`` (plus ``verdict_item_key``/``verdict_priority``/
    ``verdict_source``); ``labels[i]`` is ``1`` (kept) or ``0`` (trashed).

    User verdicts live on THREE key shapes, ALL joined here so the firewall reaches every user
    verdict (measured on the live DB, source='user'):

    * ``feed:g:<sha>`` (721 verdicts) — the content-hash key, matches ``stable_feed_key`` by RAW
      equality (the column already stores the full ``feed:g:<sha>`` string; NO ``'feed:'||``
      prefix). This is the majority Today shape after the stable-key migration; the old
      two-path loader dropped ALL of them (the leak this repair closes).
    * ``feed:<feed_item_id>`` (725 verdicts) — the legacy numeric key, matches
      ``'feed:'||feed_item_id``.
    * a bare Zotero item key (~123 verdicts) — matches ``materialized_zotero_key``.

    Because a content-hash key fans out across every day the same paper reappeared (721 verdicts
    → ~1650 feed rows), the GT is **one row per VERDICT**: the latest verdict per ``item_key``,
    matched to its MOST-RECENT scored feed row (``ORDER BY p.id DESC``). Requires only a non-null
    ``composite_score`` (the rank key). The old ``TRIM(abstract) != ''`` precondition is DROPPED —
    title-only ``goal_sim`` is a supported floor, and it silently halved the kept class
    (15 → 42 kept on the live DB). Prints a join-yield ladder to stderr.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(_KEPT_PRIORITIES))
    cols = "p.id, p.title, p.abstract, p.decision, p.composite_score, " \
           "p.shap_contribs_json, p.materialized_zotero_key, " \
           "lv.item_key AS verdict_item_key, lv.user_priority AS verdict_priority, " \
           "lv.source AS verdict_source"
    # One row per user verdict: pick the LATEST verdict per item_key (a re-label supersedes),
    # then its most-recent scored feed row across the 3 key shapes. The correlated subquery
    # collapses the content-hash fan-out (one sha verdict -> many daily feed rows) to a single
    # representative, so the negative class is not inflated by re-appearances.
    params = (_USER_SOURCE, *_KEPT_PRIORITIES, _TRASHED_PRIORITY)
    try:
        cur = conn.execute(
            f"""WITH latest_verdict AS (
                    SELECT item_key, user_priority, source,
                           ROW_NUMBER() OVER (
                               PARTITION BY item_key ORDER BY created_at DESC, id DESC
                           ) AS rn
                    FROM label_verdicts
                    WHERE source = ?
                      AND (user_priority IN ({placeholders}) OR user_priority = ?)
                )
                SELECT {cols}
                FROM latest_verdict lv
                JOIN processed_feed_items p ON p.id = (
                    SELECT p2.id FROM processed_feed_items p2
                    WHERE (p2.stable_feed_key = lv.item_key
                           OR ('feed:' || p2.feed_item_id) = lv.item_key
                           OR p2.materialized_zotero_key = lv.item_key)
                      AND p2.composite_score IS NOT NULL
                    ORDER BY p2.id DESC LIMIT 1
                )
                WHERE lv.rn = 1
                ORDER BY p.id""",
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
        total_verdicts = conn.execute(
            f"""SELECT COUNT(DISTINCT item_key) FROM label_verdicts
                WHERE source = ? AND (user_priority IN ({placeholders}) OR user_priority = ?)""",
            params,
        ).fetchone()[0]
    finally:
        conn.close()
    labels = [0 if r["verdict_priority"] == _TRASHED_PRIORITY else 1 for r in rows]
    _print_yield_ladder(rows, labels, total_verdicts)
    return rows, labels


def _print_yield_ladder(rows: list[dict[str, Any]], labels: list[int], total_verdicts: int) -> None:
    """Emit the join-yield ladder to stderr so the eval's GT size is never silently wrong."""
    per_path: dict[str, int] = {}
    for r in rows:
        per_path[_key_path(r["verdict_item_key"])] = per_path.get(_key_path(r["verdict_item_key"]), 0) + 1
    kept = sum(labels)
    print("[eval_labels] firewalled GT join-yield ladder:", file=sys.stderr)
    print(f"  user verdicts (distinct item_key) : {total_verdicts}", file=sys.stderr)
    print(f"  joined to a scored feed row       : {len(rows)}  (dropped {total_verdicts - len(rows)})",
          file=sys.stderr)
    for path in ("stable_feed_key", "feed:<id>", "materialized_zotero_key"):
        print(f"    via {path:24s}: {per_path.get(path, 0)}", file=sys.stderr)
    print(f"  kept={kept}  trashed={len(rows) - kept}", file=sys.stderr)


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
