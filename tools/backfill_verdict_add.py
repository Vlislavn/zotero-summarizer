"""One-shot: add already-verdicted Today-feed papers to Zotero.

Papers given a POSITIVE verdict (must/should/could) in the Today deep-review used
to only record a training label — they silently never reached Zotero (the separate
"Add to library" click was required). This backfills them through the exact path
the new verdict-auto-add uses (``daily_actions.materialize_feed_verdict``), which
records NO new label (the verdict is already saved) and is idempotent.

Run against the LIVE workspace (its .env → real Zotero):

    ZOTERO_SUMMARIZER_HOME=/path/to/workspace uv run python tools/backfill_verdict_add.py            # dry run
    ZOTERO_SUMMARIZER_HOME=/path/to/workspace uv run python tools/backfill_verdict_add.py --apply    # writes

Default is a DRY RUN (report only). ``--apply`` snapshots zotero.sqlite first (a
consistent online backup, safe under WAL), then materializes each paper. Papers
already represented in the library (materialized, or a dedup-library duplicate)
are skipped — never re-added.

TIP: for a clean run, stop the app server first so its triage-DB writes don't
contend with the backfill (24 rows, seconds).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

_POSITIVE = ("must_read", "should_read", "could_read")
_RANK = {"must_read": 3, "should_read": 2, "could_read": 1}
# Decisions that mean the paper is already in the library under another copy — never re-add.
_ALREADY_IN_LIBRARY = ("rejected_dedup_library",)


def _candidates(db_path) -> list[dict]:
    """Distinct feed papers with a DELIBERATE positive verdict (``source='user'``)
    that are not in the library yet — one row per resolved stable key, strongest
    verdict wins.

    Materialization is tested per-PAPER, not per-row: a feed paper re-arrives as
    several ``processed_feed_items`` rows, so ``NOT EXISTS(any sibling with a
    materialized key)`` is the correct "already added?" test (a per-row check
    inflates the set with siblings of already-added papers). A paper carrying a
    ``rejected_dedup_library`` row is already present under another copy → skip."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT p.stable_feed_key AS key, p.decision AS decision,
                   p.title AS title, p.created_at AS created_at,
                   lv.user_priority AS verdict
            FROM label_verdicts lv
            JOIN processed_feed_items p
              ON p.stable_feed_key = lv.item_key OR ('feed:' || p.feed_item_id) = lv.item_key
            WHERE lv.source = 'user'
              AND lv.item_key LIKE 'feed:%'
              AND lv.user_priority IN ('must_read', 'should_read', 'could_read')
              AND NOT EXISTS (
                SELECT 1 FROM processed_feed_items q
                WHERE (q.stable_feed_key = lv.item_key OR ('feed:' || q.feed_item_id) = lv.item_key)
                  AND q.materialized_zotero_key IS NOT NULL AND q.materialized_zotero_key <> ''
              )
            """
        ).fetchall()
    finally:
        conn.close()
    # Group the (possibly several) rows per resolved stable key into one paper.
    papers: dict[str, dict] = {}
    for r in rows:
        k = str(r["key"])
        p = papers.get(k)
        if p is None:
            p = {"key": k, "verdict": r["verdict"], "title": r["title"],
                 "decision": r["decision"], "dedup_library": False}
            papers[k] = p
        if _RANK[r["verdict"]] > _RANK[p["verdict"]]:
            p["verdict"] = r["verdict"]
        # A newer row's decision/title represents the current state best.
        if str(r["created_at"] or "") >= str(p.get("_ts") or ""):
            p["_ts"], p["decision"], p["title"] = r["created_at"], r["decision"], r["title"]
        if r["decision"] in _ALREADY_IN_LIBRARY:
            p["dedup_library"] = True
    return sorted(papers.values(), key=lambda r: -_RANK[r["verdict"]])


def _backup_zotero(zotero_sqlite) -> str:
    """Consistent online snapshot of zotero.sqlite (safe while Zotero is open/WAL)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = zotero_sqlite.with_name(f"zotero.sqlite.backfill-{stamp}.bak")
    src = sqlite3.connect(f"file:{zotero_sqlite}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return str(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write to Zotero (default: dry run)")
    args = ap.parse_args()

    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services.triage import daily_actions

    settings = get_settings()
    db_path = settings.triage_db_path
    zotero_sqlite = settings.zotero_data_dir / "zotero.sqlite"
    print(f"workspace : {settings.project_root}")
    print(f"triage db : {db_path}")
    print(f"zotero    : {zotero_sqlite}")
    print()

    cands = _candidates(db_path)
    to_add = [c for c in cands if not c["dedup_library"]]
    skip = [c for c in cands if c["dedup_library"]]

    print(f"Deliberate positive-verdict feed papers not yet materialized: {len(cands)}")
    print(f"  → will add : {len(to_add)}")
    print(f"  → skip (already in library via dedup): {len(skip)}")
    print()
    for c in to_add:
        print(f"  [ADD ] {c['verdict']:11} {c['decision']:22} {str(c['title'])[:70]}")
    for c in skip:
        print(f"  [SKIP] {c['verdict']:11} {c['decision']:22} {str(c['title'])[:70]}")
    print()

    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply to materialize.")
        return 0

    if not zotero_sqlite.exists():
        print(f"ERROR: {zotero_sqlite} not found — set ZOTERO_DATA_DIR / ZOTERO_SUMMARIZER_HOME.")
        return 2
    backup = _backup_zotero(zotero_sqlite)
    print(f"zotero.sqlite backed up → {backup}\n")

    added, pending, skipped, failed = 0, 0, 0, 0
    for c in to_add:
        res = daily_actions.materialize_feed_verdict(c["key"])
        status = res["status"]
        if status == "added":
            added += 1
        elif status == "already_in_library":
            skipped += 1
        elif status in ("zotero_pending", "zotero_unavailable"):
            pending += 1
        else:
            failed += 1
        print(f"  {status:20} {res.get('zotero_key') or '-':10} {str(c['title'])[:60]}")

    print(f"\nDONE — added={added} pending={pending} already={skipped} failed={failed}")
    print("New items appear in Zotero after you RESTART Zotero.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
