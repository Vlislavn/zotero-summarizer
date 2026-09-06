"""Observed feedback metrics use the prediction saved with the decision."""
import sqlite3
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tests.test_feedback_atomicity import _database, _event
from zotero_summarizer.api.routes import corpus, results as routes
from zotero_summarizer.services import results
from zotero_summarizer.storage import repositories as db


def _client():
    app = FastAPI()
    app.include_router(corpus.router)
    app.include_router(routes.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("change", ["retriage", "delete", "forced_priority"])
def test_http_metrics_keep_the_prediction_recorded_with_feedback(tmp_path, monkeypatch, change):
    path = _database(monkeypatch, tmp_path)
    db.insert_result("A", "Paper", {"reading_priority": "dont_read"})
    if change == "forced_priority":
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE triage_results SET reading_priority='must_read', forced_priority='dont_read'")
    monkeypatch.setattr(results.interaction_log, "log_human_feedback", Mock())

    with _client() as client:
        assert client.post("/api/triage/results/A/feedback", json={"verdict": "approve"}).status_code == 200
        original = client.get("/api/calibration/metrics").json()
        assert original["periods"]["all_time"]["false_negative_count"] == 1
        if change == "delete":
            with sqlite3.connect(path) as conn:
                conn.execute("DELETE FROM triage_results")
        else:
            db.insert_result("A", "Later result", {"reading_priority": "must_read"})
        response = client.get("/api/calibration/metrics")

    assert response.status_code == 200
    assert response.json() == original
    period = original["periods"]["all_time"]
    assert period["total_feedback"] == period["with_prediction_count"] == 1
    assert period["agreement_rate"] == period["recall"] == 0
    assert period["precision"] is None


@pytest.mark.parametrize("priority", ["", "unknown"])
def test_unknown_at_decision_is_not_filled_from_later_prediction(tmp_path, monkeypatch, priority):
    _database(monkeypatch, tmp_path)
    db.insert_feedback_events([{**_event("A", "explicit_approve"), "original_priority": priority}])
    db.insert_result("A", "Later result", {"reading_priority": "must_read"})

    with _client() as client:
        response = client.get("/api/calibration/metrics")

    assert response.status_code == 200
    for period in response.json()["periods"].values():
        assert period["total_feedback"] == 1
        assert period["with_prediction_count"] == period["false_negative_count"] == 0
        assert period["agreement_rate"] is period["precision"] is period["recall"] is None


def test_periods_use_one_explicit_feedback_snapshot_and_include_time_boundaries(tmp_path, monkeypatch):
    path = _database(monkeypatch, tmp_path)
    db.insert_feedback_events([
        {**_event(str(age), "explicit_approve"), "original_priority": "should_read"}
        for age in (6, 7, 8, 29, 30, 31)
    ] + [_event("implicit", "brain_tag")])
    with sqlite3.connect(path) as conn:
        for age in (6, 7, 8, 29, 30, 31):
            conn.execute("UPDATE user_feedback SET created_at=datetime('2026-09-05 12:00:00', ?) WHERE item_id=?",
                         (f"-{age} days", str(age)))
    get_conn = db._get_conn

    def fixed_clock_connection():
        conn = get_conn()
        conn.create_function("datetime", 1, lambda _: "2026-09-05 12:00:00")
        return conn

    connect = Mock(wraps=fixed_clock_connection)
    monkeypatch.setattr("zotero_summarizer.storage._repo_feedback._get_conn", connect)

    with _client() as client:
        response = client.get("/api/calibration/metrics")

    assert response.status_code == 200
    for key, expected in [("last_7d", 2), ("last_30d", 5), ("all_time", 6)]:
        period = response.json()["periods"][key]
        assert period["total_feedback"] == period["with_prediction_count"] == expected
        assert period["agreement_rate"] == period["precision"] == period["recall"] == 1
    connect.assert_called_once()


def test_feedback_read_failure_is_not_empty_success(tmp_path, monkeypatch):
    _database(monkeypatch, tmp_path)
    monkeypatch.setattr("zotero_summarizer.storage._repo_feedback._get_conn",
                        Mock(side_effect=sqlite3.OperationalError("test read failure")))
    with _client() as client:
        assert client.get("/api/calibration/metrics").status_code == 500


def test_observed_confusion_counts_only_known_at_decision_predictions(tmp_path, monkeypatch):
    _database(monkeypatch, tmp_path)
    decisions = [
        ("TP", "approve", "must_read"), ("TN", "reject", "dont_read"),
        ("FP", "reject", "should_read"), ("FN", "approve", "could_read"),
        ("missing", "approve", ""), ("invalid", "reject", "unknown"),
    ]
    db.insert_feedback_events([
        {**_event(key, f"explicit_{verdict}"), "original_priority": priority}
        for key, verdict, priority in decisions
    ] + [_event("implicit", "brain_tag")])
    for key, _, priority in decisions:
        db.insert_result(key, key, {"reading_priority": "dont_read" if priority == "must_read" else "must_read"})
    before = db.get_feedback_events()

    with _client() as client:
        response = client.get("/api/calibration/metrics")

    assert response.status_code == 200
    for period in response.json()["periods"].values():
        assert period == {
            "total_feedback": 6, "approved_count": 3, "rejected_count": 3,
            "with_prediction_count": 4, "agreement_count": 2,
            "false_positive_count": 1, "false_negative_count": 1,
            "predicted_positive_count": 2, "actual_positive_count": 2,
            "true_positive_count": 1, "agreement_rate": .5, "precision": .5, "recall": .5,
        }
    assert db.get_feedback_events() == before
