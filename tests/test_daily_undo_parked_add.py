"""Undo on a Today card also releases the card's parked Zotero Add (#205)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tests.test_offline_sync import _db
from zotero_summarizer.api.routes import _daily_verdict
from zotero_summarizer.services.golden import label_verdicts
from zotero_summarizer.services.triage.daily_select import assemble_daily_slate
from zotero_summarizer.storage import feeds


def test_undo_returns_parked_add_to_a_shortlistable_undecided_row(tmp_path, monkeypatch):
    db = _db(tmp_path)
    with feeds.open_triage_conn(db) as conn:
        feeds.record_decision(conn, run_id="r", feed_item={
            "feed_library_id": 1, "item_id": 7, "guid": "parked", "title": "Parked paper",
        }, decision=feeds.DECISION_TRIAGED_PENDING, decision_reason="pending_daily_selection",
            composite_score=3.0, planned_zotero_key="PLANNED1")
        conn.commit()
        row = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
        conn.execute("BEGIN IMMEDIATE")
        feeds.mark_pending_if_current(conn, row, "verdict_add_zotero_pending")
        conn.commit()
    key = row["stable_feed_key"]
    label_verdicts.set_label_verdict(db, item_key=key, original_derived_priority="should_read",
                                     user_priority="must_read", surface="annotate_verdict")
    monkeypatch.setattr(_daily_verdict, "_db_path", lambda: db)
    monkeypatch.setattr(label_verdicts, "log_committed_transition", lambda **_: None)

    reply = asyncio.run(_daily_verdict.undo_daily_verdict(_daily_verdict.DailyVerdictUndoRequest(item_id=row["id"])))

    assert reply["deleted"] is True and reply["released_pending_add"] == 1
    with feeds.open_triage_conn(db) as conn:
        after = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
        assert feeds.current_feed_verdict(conn, after) is None
    assert (after["decision"], after["decision_reason"], after["zotero_sync_status"]) == (
        "triaged_pending", "pending_daily_selection", None)
    assert after["final_outcome"] is None and after["planned_zotero_key"] == "PLANNED1"
    slate = assemble_daily_slate(db_path=db, K=5, now=datetime.now(timezone.utc))
    assert row["id"] in {paper.item_id for paper in slate.papers}
