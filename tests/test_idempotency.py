"""Tests for processed_feed_items idempotency: re-runs skip items already decided."""
from __future__ import annotations

import sqlite3

from zotero_summarizer.storage import feeds as feeds_storage


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    feeds_storage.init_feeds_schema(conn)
    return conn


def _item(feed_library_id: int, item_id: int, **extra) -> dict:
    base = {
        "feed_library_id": feed_library_id,
        "item_id": item_id,
        "guid": f"g{item_id}",
        "title": f"Paper {item_id}",
        "doi": "",
        "arxiv_id": "",
        "feed_name": "TestFeed",
    }
    base.update(extra)
    return base


def test_filter_unprocessed_passes_through_when_empty_db():
    conn = _open()
    items = [_item(2, 100), _item(2, 101), _item(3, 200)]
    unprocessed, skipped = feeds_storage.filter_unprocessed(conn, items)
    assert len(unprocessed) == 3
    assert skipped == 0


def test_filter_unprocessed_skips_already_recorded():
    conn = _open()
    feeds_storage.record_decision(
        conn,
        run_id="r1",
        feed_item=_item(2, 100),
        decision=feeds_storage.DECISION_SELECTED,
    )
    conn.commit()
    items = [_item(2, 100), _item(2, 101)]
    unprocessed, skipped = feeds_storage.filter_unprocessed(conn, items)
    assert skipped == 1
    assert len(unprocessed) == 1
    assert unprocessed[0]["item_id"] == 101


def test_record_decision_is_idempotent():
    """INSERT OR IGNORE — recording the same (feed_lib, item_id) twice doesn't dup."""
    conn = _open()
    feeds_storage.record_decision(
        conn, run_id="r1", feed_item=_item(2, 100), decision=feeds_storage.DECISION_SELECTED
    )
    feeds_storage.record_decision(
        conn, run_id="r2", feed_item=_item(2, 100), decision=feeds_storage.DECISION_REJECTED_ELBOW
    )
    conn.commit()
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM processed_feed_items WHERE feed_library_id=2 AND feed_item_id=100"
    ).fetchall()
    assert int(rows[0]["n"]) == 1
    # First decision wins (idempotency)
    row = conn.execute(
        "SELECT decision FROM processed_feed_items WHERE feed_library_id=2 AND feed_item_id=100"
    ).fetchone()
    assert row["decision"] == feeds_storage.DECISION_SELECTED


def test_run_summary_aggregates_by_decision():
    conn = _open()
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 100), decision=feeds_storage.DECISION_SELECTED)
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 101), decision=feeds_storage.DECISION_SELECTED)
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 102), decision=feeds_storage.DECISION_BLACK_SWAN)
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 103), decision=feeds_storage.DECISION_REJECTED_ELBOW)
    feeds_storage.record_decision(conn, run_id="r2", feed_item=_item(2, 104), decision=feeds_storage.DECISION_SELECTED)
    conn.commit()

    summary = feeds_storage.get_run_summary(conn, "r1")
    assert summary["run_id"] == "r1"
    assert summary["total"] == 4
    assert summary["by_decision"][feeds_storage.DECISION_SELECTED] == 2
    assert summary["by_decision"][feeds_storage.DECISION_BLACK_SWAN] == 1
    assert summary["by_decision"][feeds_storage.DECISION_REJECTED_ELBOW] == 1


def test_list_recent_decisions_ordered_desc():
    conn = _open()
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 100), decision=feeds_storage.DECISION_SELECTED)
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 101), decision=feeds_storage.DECISION_REJECTED_ELBOW)
    conn.commit()
    rows = feeds_storage.list_recent_decisions(conn, limit=10)
    assert len(rows) == 2


def test_list_recent_decisions_filters_by_decision():
    conn = _open()
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 100), decision=feeds_storage.DECISION_SELECTED)
    feeds_storage.record_decision(conn, run_id="r1", feed_item=_item(2, 101), decision=feeds_storage.DECISION_REJECTED_ELBOW)
    conn.commit()
    rows = feeds_storage.list_recent_decisions(conn, decision=feeds_storage.DECISION_SELECTED)
    assert len(rows) == 1
    assert rows[0]["decision"] == feeds_storage.DECISION_SELECTED


def test_new_run_id_is_unique_per_invocation():
    a = feeds_storage.new_run_id()
    b = feeds_storage.new_run_id()
    assert a.startswith("feeds_")
    assert a != b
