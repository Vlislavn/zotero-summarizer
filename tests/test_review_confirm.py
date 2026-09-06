"""Bulk confirmation is a durable user verdict, not a CSV duplicate check."""
import asyncio
import csv
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from test_review_workflow import _insert_awaiting, patched_settings  # noqa: F401
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes.review import ConfirmGateRejectedRequest, confirm_gate_rejected
from zotero_summarizer.services import interaction_log, run_log
from zotero_summarizer.services.golden import hybrid_gt
from zotero_summarizer.services.library import review
from zotero_summarizer.storage import feeds as fs, repositories
from zotero_summarizer.storage.feed_identity import row_feed_keys


def _seed(path, *, count=2):
    with fs.open_triage_conn(path / "triage.db") as conn:
        ids = [_insert_awaiting(conn, feed_item_id=n) for n in range(1, count + 1)]
        conn.execute("UPDATE processed_feed_items SET decision = 'gate_rejected', reading_priority = 'dont_read'")
        rows = [dict(row) for row in conn.execute("SELECT * FROM processed_feed_items ORDER BY id")]
        conn.commit()
    return ids, rows


@pytest.mark.parametrize("existing", [None, "must_read", "dont_read"])
def test_confirmation_commits_verdict_training_and_event_once(patched_settings, monkeypatch, existing):
    path = patched_settings
    ids, rows = _seed(path)
    key = row_feed_keys(rows[0])[0]
    log = path / "events.jsonl"
    monkeypatch.setattr(interaction_log, "settings", lambda: SimpleNamespace(interaction_log_path=log))
    if existing:
        review.append_to_golden(rows[0], label=existing, note="Existing training example")
        repositories.insert_or_update_label_verdict(
            path / "triage.db", item_key=key, original_derived_priority="dont_read",
            user_priority=existing, comment="Prior verdict",
        )
    request = ConfirmGateRejectedRequest(processed_ids=[ids[0], ids[0]])
    result = asyncio.run(confirm_gate_rejected(request))
    verdict = repositories.get_label_verdict(path / "triage.db", key)
    assert verdict is not None
    assert verdict["user_priority"] == "dont_read"
    assert verdict["source"] == "user"
    with (path / "zotero-summarizer-golden.csv").open() as source:
        training = hybrid_gt.apply_hybrid(list(csv.DictReader(source)), path / "triage.db")
    assert len(training) == 1
    assert training[0]["gold_priority_final"] == "dont_read"
    assert training[0]["_hybrid_source"] == "user"
    assert [row["id"] for row in review.list_by_state("gate_rejected")] == [ids[1]]
    events = run_log.load_runs(log)
    feedback = [event for event in events if event["event"] == "human_feedback"]
    assert len(feedback) == 1
    assert feedback[0]["human"]["value"] == "dont_read"
    assert feedback[0]["surface"] == "review_confirm_gate_rejected"
    assert result == {"confirmed": 1, "skipped": 0}
    assert asyncio.run(confirm_gate_rejected(request)) == {"confirmed": 0, "skipped": 1}
    assert run_log.load_runs(log) == events


def test_requested_row_is_not_lost_to_global_limit_or_age(patched_settings):
    path = patched_settings
    ids, _rows = _seed(path, count=5001)
    with fs.open_triage_conn(path / "triage.db") as conn:
        conn.execute("UPDATE processed_feed_items SET created_at = '2020-01-01' WHERE id = ?", (ids[-1],))
        conn.commit()
    assert review.confirm_remaining_gate_rejected([ids[-1]]) == {"confirmed": 1, "skipped": 0}
    with fs.open_triage_conn(path / "triage.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM processed_feed_items WHERE decision = 'gate_rejected'").fetchone()[0] == 5000


def test_already_relabelled_rows_are_not_overridden(patched_settings):
    ids, rows = _seed(patched_settings)
    review.relabel(ids[0], "must_read")
    assert review.confirm_remaining_gate_rejected(ids) == {"confirmed": 1, "skipped": 1}
    verdict = repositories.get_label_verdict(patched_settings / "triage.db", row_feed_keys(rows[0])[0])
    assert verdict["user_priority"] == "must_read"


def test_partial_failure_rolls_back_row_and_retry_skips_completed_rows(patched_settings):
    path = patched_settings
    ids, _rows = _seed(path)
    with fs.open_triage_conn(path / "triage.db") as conn:
        conn.execute(f"CREATE TRIGGER fail_second BEFORE UPDATE ON processed_feed_items WHEN NEW.id = {ids[1]} "
                     "BEGIN SELECT RAISE(ABORT, 'second row failed'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="second row failed"):
        review.confirm_remaining_gate_rejected(ids)
    with fs.open_triage_conn(path / "triage.db") as conn:
        assert [row[0] for row in conn.execute("SELECT decision FROM processed_feed_items ORDER BY id")] == [
            "user_rejected", "gate_rejected",
        ]
        assert conn.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0] == 1
        conn.execute("DROP TRIGGER fail_second")
        conn.commit()
    assert review.confirm_remaining_gate_rejected(ids) == {"confirmed": 1, "skipped": 1}


def test_missing_requested_row_fails_before_any_write(patched_settings):
    ids, _rows = _seed(patched_settings)
    with pytest.raises(APIError) as caught:
        asyncio.run(confirm_gate_rejected(ConfirmGateRejectedRequest(processed_ids=[ids[0], 999999])))
    assert caught.value.status_code == 404
    assert repositories.list_all_label_verdicts(patched_settings / "triage.db") == []


@pytest.mark.parametrize("ids", [[], [0], [-1], [True], ["1"], [1.0], list(range(1, 502))])
def test_confirmation_request_rejects_invalid_ids(ids):
    with pytest.raises(ValidationError):
        ConfirmGateRejectedRequest(processed_ids=ids)
