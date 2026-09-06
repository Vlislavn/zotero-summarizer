"""A resolved feed outcome and its training signal are one SQLite write."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from zotero_summarizer.services.triage.feeds import _outcomes as service
from zotero_summarizer.storage import feeds, repositories as db


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "triage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    monkeypatch.setattr(service, "_triage_conn", lambda: feeds.open_triage_conn(path))
    log = Mock()
    monkeypatch.setattr(service.interaction_log, "log_behavioural_outcome", log)
    with feeds.open_triage_conn(path) as conn:
        feeds.record_decision(
            conn, run_id="seed", feed_item={"feed_library_id": 2, "item_id": 100,
                                           "guid": "paper", "title": "Paper"},
            decision=feeds.DECISION_SELECTED, reading_priority="must_read",
        )
        feeds.record_materialization(conn, feed_library_id=2, feed_item_id=100,
                                     materialized_zotero_key="PAPER001", outcome_window_days=0)
        conn.commit()
    return path, log


def _snapshot(path):
    with feeds.open_triage_conn(path) as conn:
        row = dict(conn.execute("SELECT * FROM processed_feed_items").fetchone())
        feedback = [dict(row) for row in conn.execute("SELECT * FROM user_feedback")]
        return row, feedback


@pytest.mark.parametrize("target", ["feedback", "outcome"])
def test_failed_write_leaves_both_retryable(store, target):
    path, log = store
    trigger = ("BEFORE INSERT ON user_feedback" if target == "feedback" else
               "BEFORE UPDATE OF outcome_detected_at ON processed_feed_items")
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TRIGGER fail_write {trigger} "
                     "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    reader = Mock()
    reader.get_item_membership.return_value = {"exists": True, "is_trashed": True}
    before = _snapshot(path)

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        service._resolve_due_outcomes(reader=reader, limit=5)

    assert _snapshot(path) == before
    log.assert_not_called()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER fail_write")
    assert service._resolve_due_outcomes(reader=reader, limit=5) == 1
    row, feedback = _snapshot(path)
    assert row["final_outcome"] == "trashed"
    assert row["outcome_detected_at"]
    assert len(feedback) == 1
    assert feedback[0]["signal"] == "feed_outcome:trashed"
    assert feedback[0]["inferred_relevance"] == 1.0
    assert service._resolve_due_outcomes(reader=reader, limit=5) == 0
    assert _snapshot(path) == (row, feedback)
    log.assert_called_once()


@pytest.mark.parametrize("membership,outcome,kind,weight,relevance", [
    ({"exists": True, "has_engagement_tag": True}, "engaged", "implicit_engagement", 3.0, 5.0),
    ({"exists": True, "collection_keys": ["Research"]}, "moved_collection", "implicit_engagement", 1.0, 11 / 3),
    ({"exists": True, "is_in_inbox": True, "collection_keys": ["Inbox"]},
     "kept_inbox", "implicit_weak_negative", -0.5, 8 / 3),
    ({"exists": True}, "deleted_all", "implicit_negative_strong", -3.0, 1.0),
    ({"exists": True, "is_trashed": True}, "trashed", "implicit_negative_strong", -3.0, 1.0),
    ({"exists": False}, "unknown", "implicit_negative_strong", -1.0, 7 / 3),
])
def test_resolver_persists_matching_signal(store, membership, outcome, kind, weight, relevance):
    reader = Mock()
    reader.get_item_membership.return_value = membership
    assert service._resolve_due_outcomes(reader=reader, limit=5) == 1
    row, feedback = _snapshot(store[0])
    assert (row["final_outcome"], row["outcome_signal_weight"]) == (outcome, weight)
    assert len(feedback) == 1
    event = feedback[0]
    assert (event["item_id"], event["signal"], event["feedback_type"], event["original_priority"]) == (
        "PAPER001", f"feed_outcome:{outcome}", kind, "must_read",
    )
    assert event["inferred_relevance"] == pytest.approx(relevance)


def test_membership_failure_propagates_without_completion(store):
    reader = Mock()
    reader.get_item_membership.side_effect = RuntimeError("read failure")
    before = _snapshot(store[0])
    with pytest.raises(RuntimeError, match="read failure"):
        service._resolve_due_outcomes(reader=reader, limit=5)
    assert _snapshot(store[0]) == before
    store[1].assert_not_called()


def test_concurrent_snapshots_resolve_once(store):
    barrier = Barrier(2)
    reader = Mock()

    def membership(key):
        barrier.wait(timeout=10)
        return {"exists": True, "is_trashed": True}

    reader.get_item_membership.side_effect = membership
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service._resolve_due_outcomes, reader=reader, limit=5) for _ in range(2)]
        counts = [future.result(timeout=20) for future in futures]
    assert sorted(counts) == [0, 1]
    assert len(_snapshot(store[0])[1]) == 1
    store[1].assert_called_once()
    before = _snapshot(store[0])
    with feeds.open_triage_conn(store[0]) as conn:
        assert not feeds.record_outcome(conn, feed_library_id=2, feed_item_id=100, final_outcome="engaged")
        conn.commit()
    assert _snapshot(store[0]) == before


def test_feedback_uses_outcome_connection_not_global_repository_path(store, tmp_path, monkeypatch):
    other = tmp_path / "other.db"
    monkeypatch.setattr(db, "DB_PATH", other)
    db.init_db()
    reader = Mock()
    reader.get_item_membership.return_value = {"exists": False}

    assert service._resolve_due_outcomes(reader=reader, limit=5) == 1

    assert len(_snapshot(store[0])[1]) == 1
    assert db.get_feedback_events() == []
