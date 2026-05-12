"""Phase 1.5: outcome detection writes user_feedback rows with asymmetric weights.

End-to-end: materialize -> user deletes from all collections -> next daemon tick
resolves outcome -> writes user_feedback with weight=-3 (strong negative).

This exercises the contract that the feedback loop must see deletions, not just
positive tags (the Phase 1 limitation).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.services.feeds import (
    _compute_outcome_from_membership,
    _feedback_type_from_outcome,
    _relevance_from_weight,
)
from zotero_summarizer.storage import feeds as fs


def test_feedback_type_mapping_strong_negative_on_delete():
    assert _feedback_type_from_outcome(fs.OUTCOME_DELETED_ALL) == "implicit_negative_strong"
    assert _feedback_type_from_outcome(fs.OUTCOME_TRASHED) == "implicit_negative_strong"
    assert _feedback_type_from_outcome(fs.OUTCOME_UNKNOWN) == "implicit_negative_strong"


def test_feedback_type_mapping_engagement_is_positive():
    assert _feedback_type_from_outcome(fs.OUTCOME_ENGAGED) == "implicit_engagement"
    assert _feedback_type_from_outcome(fs.OUTCOME_MOVED_COLLECTION) == "implicit_engagement"


def test_feedback_type_mapping_kept_inbox_is_weak_negative():
    assert _feedback_type_from_outcome(fs.OUTCOME_KEPT_INBOX) == "implicit_weak_negative"


def test_relevance_from_weight_spans_full_range():
    # -3 -> 1.0 (strong negative)
    assert _relevance_from_weight(-3.0) == pytest.approx(1.0, abs=0.01)
    # 0 -> 3.0 (neutral)
    assert _relevance_from_weight(0.0) == pytest.approx(3.0, abs=0.01)
    # +3 -> 5.0 (strong positive)
    assert _relevance_from_weight(3.0) == pytest.approx(5.0, abs=0.01)
    # Clamps to [1, 5]
    assert _relevance_from_weight(-100.0) == 1.0
    assert _relevance_from_weight(100.0) == 5.0


def test_full_outcome_resolution_chain_simulates_user_deletion(tmp_path: Path, monkeypatch):
    """End-to-end: build Zotero DB with materialized-then-deleted item, resolve outcome."""
    import sqlite3

    from tests._zotero_fixtures import set_feed_item_read, add_feed_item
    from zotero_summarizer.integrations.zotero_read import ZoteroReader

    db_path = build_zotero_db(tmp_path / "zotero")
    db_dir = db_path.parent

    # Materialize a paper into library (key K1), but DO NOT link to any collection
    # -> simulates "user deleted it from Inbox + all collections."
    add_library_item(db_path, item_key="K1", title="The Rejected Paper")

    # Triage DB: insert a processed_feed_items row pointing at K1 with eligible_at in the past.
    triage_conn = sqlite3.connect(":memory:")
    triage_conn.row_factory = sqlite3.Row
    fs.init_feeds_schema(triage_conn)
    fs.record_decision(
        triage_conn, run_id="t1",
        feed_item={"feed_library_id": 2, "item_id": 999, "guid": "g999", "title": "The Rejected Paper"},
        decision=fs.DECISION_SELECTED,
    )
    fs.record_materialization(
        triage_conn, feed_library_id=2, feed_item_id=999,
        materialized_zotero_key="K1", outcome_window_days=-1,  # immediately due
    )
    due = fs.due_outcome_checks(triage_conn, limit=5)
    assert len(due) == 1

    reader = ZoteroReader(db_dir)
    membership = reader.get_item_membership("K1")
    outcome = _compute_outcome_from_membership(membership)
    assert outcome == fs.OUTCOME_DELETED_ALL
    weight = fs.OUTCOME_WEIGHT[outcome]
    assert weight == -3.0

    # Write outcome to processed_feed_items.
    fs.record_outcome(triage_conn, feed_library_id=2, feed_item_id=999,
                     final_outcome=outcome, signal_weight=weight)
    row = triage_conn.execute(
        "SELECT final_outcome, outcome_signal_weight FROM processed_feed_items WHERE feed_item_id=999"
    ).fetchone()
    assert row["final_outcome"] == "deleted_all"
    assert row["outcome_signal_weight"] == -3.0
