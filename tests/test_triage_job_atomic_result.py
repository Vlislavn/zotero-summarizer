"""A failed item must not leave results or a partially populated review queue."""
import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_queue_changes import _build_summary
from tests.test_triage_job_controls import _new_job, _state
from zotero_summarizer.services.triage import triage_jobs as jobs
from zotero_summarizer.storage import repositories as db


def _setup_job(monkeypatch, tmp_path):
    path = tmp_path / "triage.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    reader = SimpleNamespace(get_item_detail=lambda key: {"title": key, "pdf_path": "/stub.pdf"})
    pipeline = Mock(return_value=_build_summary())
    monkeypatch.setattr(jobs, "get_zotero_reader_or_raise", lambda: reader)
    monkeypatch.setattr(jobs, "TRIAGE_JOB_CONCURRENCY", 1)
    monkeypatch.setattr(jobs.summarization, "run_pipeline", pipeline)
    job = _new_job("atomic", ["A"])
    _state().triage_jobs = {"atomic": job}
    return path, job, pipeline


def _counts(path):
    with sqlite3.connect(path) as conn:
        return (
            conn.execute("SELECT count(*) FROM triage_results").fetchone()[0],
            conn.execute("SELECT count(*) FROM pending_changes").fetchone()[0],
        )


def test_queue_failure_rolls_back_result_and_earlier_pending_row(monkeypatch, tmp_path):
    path, job, pipeline = _setup_job(monkeypatch, tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TRIGGER fail_note BEFORE INSERT ON pending_changes
            WHEN NEW.change_type = 'add_note'
            BEGIN SELECT RAISE(ABORT, 'forced note failure'); END""")

    asyncio.run(jobs.run_triage_job_worker("atomic", ["A"], True))

    assert job["status"] == "failed"
    assert job["results"] == []
    assert "forced note failure" in job["errors"][0]["error"]
    assert db.get_triage_job("atomic")["errors"] == job["errors"]
    assert _counts(path) == (0, 0)

    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER fail_note")
    asyncio.run(jobs.run_triage_job_worker("atomic", ["A"], True))

    assert job["status"] == "completed" and job["errors"] == []
    assert job["results"][0]["queued_change_count"] == 3
    assert _counts(path) == (1, 3)
    rows = db.get_pending_changes()
    assert {row["item_key"] for row in rows} == {"A"}
    assert {row["item_title"] for row in rows} == {"A"}
    assert {row["change_type"] for row in rows} == {"tag_changes", "add_note", "add_to_collection"}
    asyncio.run(jobs.run_triage_job_worker("atomic", ["A"], True))
    assert _counts(path) == (1, 3) and pipeline.call_count == 2


def test_resume_retries_errors_but_preserves_successful_items(monkeypatch, tmp_path):
    path, job, pipeline = _setup_job(monkeypatch, tmp_path)
    job["status"] = "interrupted"
    job["results"] = [{"item_key": "B", "title": "Already saved", "queued_change_count": 0}]
    job["errors"] = [{"item_key": "A", "error": "previous failure"}, {"item_key": "job", "error": "shutdown"}]

    asyncio.run(jobs.run_triage_job_worker("atomic", ["A", "B"], True))

    assert job["status"] == "completed" and job["errors"] == []
    assert job["completed"] == 2
    assert [row["item_key"] for row in job["results"]] == ["B", "A"]
    assert _counts(path) == (1, 3) and pipeline.call_count == 1


@pytest.mark.parametrize("queue_changes", [False, True])
def test_plan_failure_cannot_save_result_and_disabled_queue_skips_planning(monkeypatch, tmp_path, queue_changes):
    path, job, pipeline = _setup_job(monkeypatch, tmp_path)
    build_note = Mock(side_effect=ValueError("forced plan failure"))
    monkeypatch.setattr(jobs.pending, "build_triage_note_html", build_note)

    asyncio.run(jobs.run_triage_job_worker("atomic", ["A"], queue_changes))

    assert pipeline.call_count == 1
    assert _counts(path) == ((0, 0) if queue_changes else (1, 0))
    if queue_changes:
        assert job["status"] == "failed" and job["results"] == []
        assert job["errors"][0]["error"] == "forced plan failure"
        build_note.assert_called_once()
    else:
        assert job["status"] == "completed" and job["errors"] == []
        assert job["results"][0]["queued_change_count"] == 0
        build_note.assert_not_called()


@pytest.mark.parametrize("item_key,changes", [
    (" ", [{"change_type": "add_note", "payload": {}}]),
    ("A", [{"change_type": "add_note", "payload": {}}, {"change_type": "", "payload": {}}]),
])
@pytest.mark.parametrize("with_result", [False, True])
def test_invalid_plan_raises_without_committing_any_rows(monkeypatch, tmp_path, item_key, changes, with_result):
    path, _, _ = _setup_job(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="Pending changes require"):
        if with_result:
            db.insert_result(item_key, "Paper", _build_summary().model_dump(), pending_changes=changes)
        else:
            db.insert_pending_changes(item_key, "Paper", changes)

    assert _counts(path) == (0, 0)
