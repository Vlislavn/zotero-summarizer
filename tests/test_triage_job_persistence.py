from __future__ import annotations

from zotero_summarizer.storage import repositories as triage_db


def test_triage_job_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    triage_db.upsert_triage_job(
        {
            "job_id": "job_test_1",
            "status": "running",
            "started_at": "2026-04-02T18:00:00+00:00",
            "updated_at": "2026-04-02T18:01:00+00:00",
            "total": 3,
            "completed": 1,
            "current_item_key": "ABC12345",
            "current_title": "Example Paper",
            "queue_changes": True,
            "item_keys": ["ABC12345", "DEF67890", "GHI99999"],
            "results": [{"item_key": "ABC12345", "reading_priority": "could_read"}],
            "errors": [],
        }
    )

    loaded = triage_db.get_triage_job("job_test_1")

    assert loaded is not None
    assert loaded["job_id"] == "job_test_1"
    assert loaded["status"] == "running"
    assert loaded["queue_changes"] is True
    assert loaded["item_keys"] == ["ABC12345", "DEF67890", "GHI99999"]
    assert loaded["results"][0]["item_key"] == "ABC12345"


def test_mark_running_triage_jobs_interrupted(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    triage_db.upsert_triage_job(
        {
            "job_id": "job_running",
            "status": "running",
            "started_at": "2026-04-02T18:00:00+00:00",
            "updated_at": "2026-04-02T18:00:00+00:00",
            "total": 2,
            "completed": 1,
            "queue_changes": True,
            "item_keys": ["A", "B"],
            "results": [],
            "errors": [],
        }
    )
    triage_db.upsert_triage_job(
        {
            "job_id": "job_completed",
            "status": "completed",
            "started_at": "2026-04-02T18:00:00+00:00",
            "updated_at": "2026-04-02T18:02:00+00:00",
            "total": 2,
            "completed": 2,
            "queue_changes": True,
            "item_keys": ["C", "D"],
            "results": [],
            "errors": [],
        }
    )

    updated = triage_db.mark_running_triage_jobs_interrupted()

    assert updated == 1
    assert triage_db.get_triage_job("job_running")["status"] == "interrupted"
    assert triage_db.get_triage_job("job_completed")["status"] == "completed"
