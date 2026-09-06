"""A failed review must remain actionable; an approval must reach training."""
import csv
import sqlite3

import pytest

from test_review_workflow import _insert_awaiting, patched_settings  # noqa: F401
from zotero_summarizer.services.library import review, review_summary
from zotero_summarizer.services.golden.hybrid_gt import apply_hybrid
from zotero_summarizer.services.model import classifier, classifier_training
from zotero_summarizer.storage import feeds as fs


def _seed(path):
    with fs.open_triage_conn(path / "triage.db") as conn:
        row_id = _insert_awaiting(conn)
        conn.commit()
        return row_id, dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone())


def _act(row_id, action):
    if action == "must_read" or action == "dont_read":
        return review.relabel(row_id, action)
    return getattr(review, action)(row_id)


@pytest.mark.parametrize("action", ["approve", "reject", "must_read", "dont_read"])
@pytest.mark.parametrize("failure", ["metadata", "verdict", "sample", "decision"])
def test_failed_review_retains_actionable_state_and_can_retry(patched_settings, monkeypatch, action, failure):
    path = patched_settings
    row_id, before = _seed(path)
    csv_path = path / "zotero-summarizer-golden.csv"
    csv_before = csv_path.read_bytes()
    if failure == "metadata":
        def fail_metadata(**kwargs):
            raise OSError("metadata unavailable")
        monkeypatch.setattr(review_summary, "_fetch_feed_metadata", fail_metadata)
        expected_error = OSError
    else:
        with fs.open_triage_conn(path / "triage.db") as conn:
            target = {"verdict": "INSERT ON label_verdicts", "sample": "UPDATE OF training_sample_json ON label_verdicts",
                      "decision": "UPDATE ON processed_feed_items"}[failure]
            conn.execute(f"CREATE TRIGGER fail_review BEFORE {target} "
                         "BEGIN SELECT RAISE(ABORT, 'review rejected'); END")
            conn.commit()
        expected_error = sqlite3.IntegrityError
    with pytest.raises(expected_error):
        _act(row_id, action)
    assert csv_path.read_bytes() == csv_before
    assert apply_hybrid([], path / "triage.db") == []
    with fs.open_triage_conn(path / "triage.db") as conn:
        after = dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone())
        assert after == before
        assert conn.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0] == 0
        if failure in {"verdict", "sample", "decision"}:
            conn.execute("DROP TRIGGER fail_review")
            conn.commit()
    monkeypatch.setattr(review_summary, "_fetch_feed_metadata", lambda **kw: {"abstract": "Full abstract"})
    _act(row_id, action)
    with fs.open_triage_conn(path / "triage.db") as conn:
        decision = conn.execute("SELECT decision, decision_reason FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone()
        assert decision[0] in {fs.DECISION_USER_APPROVED, fs.DECISION_USER_REJECTED}
        expected_reason = (
            f"user_relabel:{action}:from_awaiting_review" if action in {"must_read", "dont_read"}
            else f"{decision[0]}_in_review_ui"
        )
        assert decision[1] == expected_reason
        assert conn.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0] == 1
    with csv_path.open() as source:
        assert len(apply_hybrid(list(csv.DictReader(source)), path / "triage.db")) == 1
    assert csv_path.read_bytes() == csv_before


def test_approve_reaches_production_training_input(patched_settings, monkeypatch):
    path = patched_settings
    row_id, before = _seed(path)
    monkeypatch.setattr(review_summary, "_fetch_feed_metadata", lambda **kw: {"abstract": "Full abstract"})
    review.approve(row_id)

    class TrainingInputReached(Exception):
        pass

    def inspect_training_rows(rows, **kwargs):
        assert len(rows) == 1
        assert rows[0]["title"] == before["title"]
        assert rows[0]["abstract"] == "Full abstract"
        assert rows[0]["gold_priority_final"] == "should_read"
        assert rows[0]["_hybrid_source"] == "user"
        raise TrainingInputReached

    monkeypatch.setattr(classifier, "_filter_train_rows", inspect_training_rows)
    with pytest.raises(TrainingInputReached):
        classifier_training.train_and_save(
            path / "zotero-summarizer-golden.csv", classifier_name="lightgbm",
            corpus_db_path=path / "corpus.db", goals_config=None, triage_db_path=path / "triage.db",
        )
