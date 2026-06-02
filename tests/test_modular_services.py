from __future__ import annotations

from zotero_summarizer.services.zotero.pending import PendingChangePlanner
from zotero_summarizer.services.triage.triage_jobs import TriageJobService
from zotero_summarizer.storage.repositories import TriageRepository


def test_pending_change_planner_builds_review_queue_changes():
    planner = PendingChangePlanner()

    changes = planner.triage_changes(
        item_key="ITEM1",
        item_title="Paper",
        reading_priority="must_read",
        tags=["topic:agents", "topic:agents"],
        note_html="<p>Summary</p>",
        suggested_collections=["Inbox", "Inbox"],
    )

    assert [change.change_type for change in changes] == [
        "tag_changes",
        "add_note",
        "add_to_collection",
    ]
    assert changes[0].payload["add_tags"] == ["zs:must_read", "topic:agents"]
    assert PendingChangePlanner.to_repository_rows(changes)[0]["change_type"] == "tag_changes"


def test_triage_repository_uses_injected_db_path(tmp_path):
    repo = TriageRepository(tmp_path / "triage_history.db")
    repo.init()
    repo.insert_pending_changes(
        "ITEM1",
        "Paper",
        [{"change_type": "tag_changes", "payload": {"add_tags": ["topic:test"]}}],
    )

    rows = repo.get_pending_changes()

    assert len(rows) == 1
    assert rows[0]["item_key"] == "ITEM1"


def test_triage_job_service_normalizes_new_job():
    job = TriageJobService.new_job([" A ", "A", "", "B"], queue_changes=False)
    public = TriageJobService.public_job(job)

    assert job["item_keys"] == ["A", "B"]
    assert job["queue_changes"] is False
    assert public.total == 2
    assert public.status == "running"
