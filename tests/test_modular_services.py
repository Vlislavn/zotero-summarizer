from __future__ import annotations

from zotero_summarizer.services.zotero.pending import PendingChangePlanner
from zotero_summarizer.services.triage.triage_jobs import new_job
from zotero_summarizer.storage.repositories import (
    get_pending_changes,
    init_db,
    insert_pending_changes,
    with_db_path,
)


def test_pending_change_planner_builds_review_queue_changes():
    planner = PendingChangePlanner()

    changes = planner.triage_changes(
        item_key="ITEM1",
        item_title="Paper",
        tags=["topic:agents", "topic:agents"],
        note_html="<p>Summary</p>",
        suggested_collections=["Inbox", "Inbox"],
    )

    assert [change.change_type for change in changes] == [
        "tag_changes",
        "add_note",
        "add_to_collection",
    ]
    # Triage no longer auto-writes a machine `zs:<priority>` tag — only the
    # LLM topical tags. The human `label:<priority>` is the sole priority tag.
    assert changes[0].payload["add_tags"] == ["topic:agents"]
    assert PendingChangePlanner.to_repository_rows(changes)[0]["change_type"] == "tag_changes"


def test_with_db_path_scopes_to_injected_db(tmp_path):
    with with_db_path(tmp_path / "triage_history.db"):
        init_db()
        insert_pending_changes(
            "ITEM1",
            "Paper",
            [{"change_type": "tag_changes", "payload": {"add_tags": ["topic:test"]}}],
        )
        rows = get_pending_changes()

    assert len(rows) == 1
    assert rows[0]["item_key"] == "ITEM1"


def test_triage_job_service_normalizes_new_job():
    job = new_job([" A ", "A", "", "B"], queue_changes=False)

    assert job["item_keys"] == ["A", "B"]
    assert job["queue_changes"] is False
    assert job["total"] == 2
    assert job["status"] == "running"
