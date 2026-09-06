"""Switching approve/reject cannot erase the last durable feedback on failure."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from zotero_summarizer.api.routes.results import router
from zotero_summarizer.domain import EXPLICIT_FEEDBACK_SIGNALS, TRIAGE_APPROVED_TAG, TRIAGE_REJECTED_TAG
from zotero_summarizer.services import results
from zotero_summarizer.storage import repositories as db


def _event(item_id, signal):
    return {"item_id": item_id, "feedback_type": "explicit" if signal in EXPLICIT_FEEDBACK_SIGNALS else "implicit_engagement",
            "signal": signal, "original_priority": "should_read", "inferred_relevance": 3.0}


def _database(monkeypatch, tmp_path):
    path = tmp_path / "triage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return path


@pytest.mark.parametrize("verdict,previous", [("approve", "explicit_reject"), ("reject", "explicit_approve")])
def test_feedback_http_failure_preserves_previous_decision_and_retry_switches_it(monkeypatch, tmp_path, verdict, previous):
    path = _database(monkeypatch, tmp_path)
    db.insert_result("A", "Paper A", {"reading_priority": "should_read", "composite_relevance_score": 4.0})
    db.insert_feedback_events([_event("A", previous), _event("A", "brain_tag"), _event("B", previous)])
    before = db.get_feedback_events()
    log = Mock()
    monkeypatch.setattr(results.interaction_log, "log_human_feedback", log)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TRIGGER fail_feedback BEFORE INSERT ON user_feedback
            BEGIN SELECT RAISE(ABORT, 'forced feedback failure'); END""")
    app = FastAPI()
    app.include_router(router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/triage/results/A/feedback", json={"verdict": verdict})

        assert response.status_code == 500
        assert db.get_feedback_events() == before
        assert db.get_pending_change_count() == 0
        log.assert_not_called()

        with sqlite3.connect(path) as conn:
            conn.execute("DROP TRIGGER fail_feedback")
        response = client.post("/api/triage/results/A/feedback", json={"verdict": verdict})

    signal = f"explicit_{verdict}"
    assert response.status_code == 200
    assert response.json() == {"item_id": "A", "verdict": verdict, "signal": signal, "queued": 1}
    assert {(row["item_id"], row["signal"]) for row in db.get_feedback_events()} == {
        ("A", signal), ("A", "brain_tag"), ("B", previous),
    }
    feedback = db.get_latest_feedback_for_items(["A"], list(EXPLICIT_FEEDBACK_SIGNALS))["A"]
    assert feedback["inferred_relevance"] == (5.0 if verdict == "approve" else 1.0)
    assert feedback["original_priority"] == "should_read"
    tags = [TRIAGE_APPROVED_TAG, TRIAGE_REJECTED_TAG] if verdict == "approve" else [TRIAGE_REJECTED_TAG, TRIAGE_APPROVED_TAG]
    assert json.loads(db.get_pending_changes()[0]["payload_json"]) == {"add_tags": tags[:1], "remove_tags": tags[1:]}
    log.assert_called_once()


def test_shared_feedback_writer_applies_batch_in_order_without_erasing_implicit_signals(monkeypatch, tmp_path):
    _database(monkeypatch, tmp_path)
    db.insert_feedback_events([_event("A", "brain_tag"), _event("B", "explicit_reject")])

    inserted = db.insert_feedback_events([
        _event("A", "explicit_approve"), _event("A", "explicit_reject"),
        {**_event("A", "explicit_approve"), "inferred_relevance": 5.0},
    ])

    assert inserted == 3
    assert {(row["item_id"], row["signal"]) for row in db.get_feedback_events()} == {
        ("A", "explicit_approve"), ("A", "brain_tag"), ("B", "explicit_reject"),
    }
    assert {row["item_id"]: row["signal"] for row in db.get_latest_explicit_feedback()} == {
        "A": "explicit_approve", "B": "explicit_reject",
    }
    assert db.get_latest_feedback_for_items(["A"], list(EXPLICIT_FEEDBACK_SIGNALS))["A"]["inferred_relevance"] == 5.0


def test_failed_second_event_rolls_back_entire_feedback_batch(monkeypatch, tmp_path):
    path = _database(monkeypatch, tmp_path)
    db.insert_feedback_events([_event("A", "explicit_approve"), _event("B", "explicit_reject")])
    before = db.get_feedback_events()
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON user_feedback
            WHEN NEW.item_id = 'B'
            BEGIN SELECT RAISE(ABORT, 'forced second failure'); END""")

    with pytest.raises(sqlite3.IntegrityError, match="forced second failure"):
        db.insert_feedback_events([_event("A", "explicit_reject"), _event("B", "explicit_approve")])

    assert db.get_feedback_events() == before


@pytest.mark.parametrize("field", ["item_id", "feedback_type", "signal"])
def test_invalid_feedback_row_rejects_the_whole_batch(monkeypatch, tmp_path, field):
    _database(monkeypatch, tmp_path)
    db.insert_feedback_events([_event("A", "explicit_approve")])
    before = db.get_feedback_events()

    with pytest.raises(ValueError, match="Feedback events require"):
        db.insert_feedback_events([_event("A", "explicit_reject"), {**_event("B", "brain_tag"), field: " "}])

    assert db.get_feedback_events() == before


def test_concurrent_feedback_writers_leave_one_explicit_decision(monkeypatch, tmp_path):
    _database(monkeypatch, tmp_path)
    ready = Barrier(2)

    def write(signal):
        ready.wait(timeout=3)
        return db.insert_feedback_events([_event("A", signal)])

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(write, EXPLICIT_FEEDBACK_SIGNALS)) == [1, 1]

    rows = db.get_feedback_events()
    assert len(rows) == 1 and rows[0]["signal"] in EXPLICIT_FEEDBACK_SIGNALS


@pytest.mark.parametrize("relevance", [None, "", 0.0])
def test_feedback_relevance_does_not_replace_invalid_or_zero_values_with_default(monkeypatch, tmp_path, relevance):
    _database(monkeypatch, tmp_path)
    event = {**_event("A", "brain_tag"), "inferred_relevance": relevance}

    if relevance == 0.0:
        assert db.insert_feedback_events([event]) == 1
        assert db.get_feedback_events()[0]["inferred_relevance"] == 0.0
    else:
        with pytest.raises((TypeError, ValueError)):
            db.insert_feedback_events([event])
        assert db.get_feedback_events() == []
