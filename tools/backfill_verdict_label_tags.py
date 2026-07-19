"""One-shot: stamp ``label:<priority>`` tags on already-materialized verdict papers.

Papers materialized by the verdict-auto-add backfill got the Zotero item but NOT
the user's ``label:<priority>`` ground-truth tag — the tag write is now part of
``review.materialize_row`` going forward, but these 24 predate it. This adds the
tag per each paper's saved verdict, through the SAME lock-tolerant writer the
materialization used (``apply_changes`` retries on lock), so it works while Zotero
stays open; the tag appears after a Zotero restart, exactly like the items did.

    ZOTERO_SUMMARIZER_HOME=/ws uv run python tools/backfill_verdict_label_tags.py            # dry run
    ZOTERO_SUMMARIZER_HOME=/ws uv run python tools/backfill_verdict_label_tags.py --apply    # writes

Default is a DRY RUN. ``--apply`` snapshots zotero.sqlite first (consistent online
backup, WAL-safe), then writes. Idempotent: an item already carrying the correct
``label:*`` tag is skipped (the mutually-exclusive tag builder returns a no-op).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

_POSITIVE = ("must_read", "should_read", "could_read")


def _pairs(db_path) -> list[dict]:
    """Materialized feed papers with a deliberate user verdict → (zotero_key,
    latest verdict). One row per Zotero key; the newest verdict is the current
    truth. Only positive verdicts materialize, so that is all we tag."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT p.materialized_zotero_key AS key, lv.user_priority AS verdict,
                   lv.created_at AS ts, p.title AS title
            FROM label_verdicts lv
            JOIN processed_feed_items p
              ON (p.stable_feed_key = lv.item_key OR ('feed:' || p.feed_item_id) = lv.item_key)
            WHERE lv.source = 'user'
              AND lv.item_key LIKE 'feed:%'
              AND lv.user_priority IN ('must_read', 'should_read', 'could_read')
              AND p.materialized_zotero_key IS NOT NULL
              AND p.materialized_zotero_key <> ''
            """
        ).fetchall()
    finally:
        conn.close()
    latest: dict[str, dict] = {}
    for r in rows:
        k = str(r["key"])
        cur = latest.get(k)
        if cur is None or str(r["ts"] or "") >= str(cur["ts"] or ""):
            latest[k] = {"key": k, "verdict": r["verdict"], "ts": r["ts"], "title": r["title"]}
    return sorted(latest.values(), key=lambda r: r["key"])


def _backup_zotero(zotero_sqlite) -> str:
    """Snapshot via ``immutable=1`` + ``VACUUM INTO`` — a single clean-file copy of
    the checkpointed DB state that IGNORES Zotero's live locks (``mode=ro`` blocks
    under an active 9am sync; the online ``backup()`` API starves — it restarts on
    every mid-copy write). immutable reads the main-DB file as-is (last checkpoint,
    excludes uncommitted WAL frames) — slightly stale but internally consistent
    (verified ``integrity_check=ok``), which is all a rollback net needs."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = zotero_sqlite.with_name(f"zotero.sqlite.labeltags-{stamp}.bak")
    src = sqlite3.connect(f"file:{zotero_sqlite}?immutable=1", uri=True)
    try:
        src.execute("VACUUM INTO ?", (str(dest),))
    finally:
        src.close()
    return str(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write to Zotero (default: dry run)")
    args = ap.parse_args()

    from zotero_summarizer.domain import label_tag_for_priority
    from zotero_summarizer.integrations._zotero_write_common import ZoteroWriteError
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services.zotero.pending import build_label_tag_change

    settings = get_settings()
    zotero_sqlite = settings.zotero_data_dir / "zotero.sqlite"
    pairs = _pairs(settings.triage_db_path)
    print(f"workspace : {settings.project_root}")
    print(f"zotero    : {zotero_sqlite}")
    print(f"materialized feed papers with a positive verdict: {len(pairs)}\n")
    for p in pairs:
        print(f"  {p['verdict']:11} {p['key']:10} {str(p['title'])[:60]}")
    print()

    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply to write the label tags.")
        return 0
    if not zotero_sqlite.exists():
        print(f"ERROR: {zotero_sqlite} not found — set ZOTERO_DATA_DIR / ZOTERO_SUMMARIZER_HOME.")
        return 2

    print(f"zotero.sqlite backed up → {_backup_zotero(zotero_sqlite)}\n")
    reader = ZoteroReader(settings.zotero_data_dir)
    writer = ZoteroWriter(settings.zotero_data_dir)
    tagged, already, failed = 0, 0, 0
    for p in pairs:
        detail = reader.get_item_detail(p["key"])
        if detail is None:
            print(f"  MISSING   {p['key']} — not found in Zotero (skipped)")
            failed += 1
            continue
        current = [str(t or "").strip() for t in (detail.get("tags") or []) if str(t or "").strip()]
        payload = build_label_tag_change(current, p["verdict"])
        if not payload["add_tags"] and not payload["remove_tags"]:
            already += 1
            print(f"  ok        {p['key']} already {label_tag_for_priority(p['verdict'])}", flush=True)
            continue
        try:
            result = writer.apply_changes(
                [{"id": 0, "item_key": p["key"], "change_type": "tag_changes", "payload_json": payload}],
                False,  # one batch backup already taken above
            )
        except (ZoteroWriteError, sqlite3.OperationalError) as exc:
            # Zotero held the lock through every retry (per-item batch boundary,
            # like add_to_library): report it, keep going — a re-run tags it later
            # (idempotent: already-tagged items are skipped).
            failed += 1
            print(f"  LOCKED    {p['key']} — {exc}", flush=True)
            continue
        if result.get("failed"):
            failed += 1
            print(f"  FAILED    {p['key']} — {result['failed'][0].get('error')}", flush=True)
        else:
            tagged += 1
            print(f"  tagged    {p['key']} → {label_tag_for_priority(p['verdict'])}", flush=True)

    print(f"\nDONE — tagged={tagged} already={already} failed={failed}")
    print("The label tags appear in Zotero after you RESTART Zotero.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
