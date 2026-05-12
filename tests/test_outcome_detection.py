"""Phase 1.5: outcome detection — _compute_outcome_from_membership + schema flow."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from zotero_summarizer.services.feeds import _compute_outcome_from_membership
from zotero_summarizer.storage import feeds as fs


def _fresh_db() -> sqlite3.Connection:
    """Return a fresh in-memory triage_history-style DB with the feeds schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    fs.init_feeds_schema(conn)
    return conn


# --- outcome reduction (precedence + signal weights) -----------------------


def test_engagement_tag_wins_even_if_trashed():
    m = {"exists": True, "is_trashed": True, "collection_keys": [], "has_engagement_tag": True, "is_in_inbox": False}
    assert _compute_outcome_from_membership(m) == fs.OUTCOME_ENGAGED


def test_hard_deleted_item():
    m = {"exists": False, "is_trashed": False, "collection_keys": [], "has_engagement_tag": False, "is_in_inbox": False}
    assert _compute_outcome_from_membership(m) == fs.OUTCOME_UNKNOWN


def test_trashed_with_no_engagement():
    m = {"exists": True, "is_trashed": True, "collection_keys": [], "has_engagement_tag": False, "is_in_inbox": False}
    assert _compute_outcome_from_membership(m) == fs.OUTCOME_TRASHED


def test_deleted_from_all_collections():
    m = {"exists": True, "is_trashed": False, "collection_keys": [], "has_engagement_tag": False, "is_in_inbox": False}
    assert _compute_outcome_from_membership(m) == fs.OUTCOME_DELETED_ALL


def test_kept_in_inbox_only():
    m = {"exists": True, "is_trashed": False, "collection_keys": ["EQIM47Z6"], "has_engagement_tag": False, "is_in_inbox": True}
    assert _compute_outcome_from_membership(m) == fs.OUTCOME_KEPT_INBOX


def test_moved_to_other_collection():
    m = {"exists": True, "is_trashed": False, "collection_keys": ["RESEARCH1"], "has_engagement_tag": False, "is_in_inbox": False}
    assert _compute_outcome_from_membership(m) == fs.OUTCOME_MOVED_COLLECTION


def test_inbox_plus_collection_counts_as_moved():
    """Item still in Inbox AND another collection = user filed it; weak positive."""
    m = {"exists": True, "is_trashed": False, "collection_keys": ["EQIM47Z6", "RESEARCH1"], "has_engagement_tag": False, "is_in_inbox": True}
    assert _compute_outcome_from_membership(m) == fs.OUTCOME_MOVED_COLLECTION


# --- weights ----------------------------------------------------------------


def test_signal_weights_asymmetric_and_strongly_negative_on_delete():
    """Schnabel et al. 2016 / industry: delete >> ignore. We sit at 6:1 (-3 vs -0.5)."""
    assert fs.OUTCOME_WEIGHT[fs.OUTCOME_DELETED_ALL] == -3.0
    assert fs.OUTCOME_WEIGHT[fs.OUTCOME_TRASHED] == -3.0
    assert fs.OUTCOME_WEIGHT[fs.OUTCOME_KEPT_INBOX] == -0.5
    assert fs.OUTCOME_WEIGHT[fs.OUTCOME_ENGAGED] == 3.0
    # 6× asymmetry (delete/ignore)
    assert abs(fs.OUTCOME_WEIGHT[fs.OUTCOME_DELETED_ALL]) / abs(fs.OUTCOME_WEIGHT[fs.OUTCOME_KEPT_INBOX]) == 6.0


# --- materialization schedules outcome window ------------------------------


def test_record_materialization_sets_eligible_at_and_marks_pending():
    conn = _fresh_db()
    feed_item = {"feed_library_id": 2, "item_id": 100, "guid": "g", "title": "P"}
    fs.record_decision(conn, run_id="r1", feed_item=feed_item, decision=fs.DECISION_TRIAGED_PENDING)
    fs.update_to_decision(
        conn,
        feed_library_id=2,
        feed_item_id=100,
        decision=fs.DECISION_SELECTED,
        decision_reason="elbow",
    )
    ok = fs.record_materialization(
        conn,
        feed_library_id=2,
        feed_item_id=100,
        materialized_zotero_key="ZK001",
        outcome_window_days=7,
    )
    assert ok
    row = conn.execute(
        "SELECT materialized_zotero_key, outcome_eligible_at, outcome_detected_at, final_outcome FROM processed_feed_items WHERE feed_item_id=?",
        (100,),
    ).fetchone()
    assert row["materialized_zotero_key"] == "ZK001"
    assert row["outcome_eligible_at"] is not None
    assert row["outcome_detected_at"] is None
    assert row["final_outcome"] == fs.OUTCOME_PENDING


# --- due_outcome_checks only returns past-eligible + still-pending rows ----


def test_due_outcome_checks_filters_by_eligible_and_undetected():
    conn = _fresh_db()
    for i, due in enumerate(["2020-01-01 00:00:00", "2099-01-01 00:00:00"], start=1):
        fs.record_decision(
            conn, run_id=f"r{i}",
            feed_item={"feed_library_id": 2, "item_id": 100 + i, "guid": f"g{i}", "title": f"P{i}"},
            decision=fs.DECISION_TRIAGED_PENDING,
        )
        conn.execute(
            "UPDATE processed_feed_items SET materialized_zotero_key=?, outcome_eligible_at=?, final_outcome=? WHERE feed_item_id=?",
            (f"ZK{i:03d}", due, fs.OUTCOME_PENDING, 100 + i),
        )
    conn.commit()
    rows = fs.due_outcome_checks(conn, limit=10)
    assert len(rows) == 1  # only the past-eligible one
    assert rows[0]["materialized_zotero_key"] == "ZK001"


def test_record_outcome_writes_final_outcome_and_weight():
    conn = _fresh_db()
    fs.record_decision(
        conn, run_id="r",
        feed_item={"feed_library_id": 2, "item_id": 100, "guid": "g", "title": "P"},
        decision=fs.DECISION_TRIAGED_PENDING,
    )
    fs.record_materialization(
        conn, feed_library_id=2, feed_item_id=100,
        materialized_zotero_key="ZK001", outcome_window_days=0,
    )
    fs.record_outcome(
        conn, feed_library_id=2, feed_item_id=100,
        final_outcome=fs.OUTCOME_DELETED_ALL,
        signal_weight=fs.OUTCOME_WEIGHT[fs.OUTCOME_DELETED_ALL],
    )
    row = conn.execute(
        "SELECT final_outcome, outcome_signal_weight, outcome_detected_at FROM processed_feed_items WHERE feed_item_id=?",
        (100,),
    ).fetchone()
    assert row["final_outcome"] == fs.OUTCOME_DELETED_ALL
    assert row["outcome_signal_weight"] == -3.0
    assert row["outcome_detected_at"] is not None
