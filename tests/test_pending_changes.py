from __future__ import annotations

from zotero_summarizer.storage import repositories as triage_db


def test_pending_changes_insert_and_query(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    inserted = triage_db.insert_pending_changes(
        item_key="ABCD1234",
        item_title="Example Paper",
        changes=[
            {
                "change_type": "tag_changes",
                "payload": {"add_tags": ["zs:must_read"], "remove_tags": []},
            },
            {
                "change_type": "add_note",
                "payload": {"note_title": "Triage", "note_html": "<p>Note</p>"},
            },
        ],
    )

    assert inserted == 2
    assert triage_db.get_pending_change_count("pending") == 2

    rows = triage_db.get_pending_changes(status="pending", limit=10)

    assert len(rows) == 2
    assert rows[0]["item_key"] == "ABCD1234"
    assert rows[0]["status"] == "pending"


def test_pending_changes_status_updates(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    triage_db.insert_pending_changes(
        item_key="ABCD1234",
        item_title="Example Paper",
        changes=[
            {"change_type": "tag_changes", "payload": {"add_tags": ["topic:test"]}},
            {"change_type": "add_note", "payload": {"note_html": "<p>Note</p>"}},
        ],
    )

    pending_rows = triage_db.get_pending_changes(status="pending", limit=10)
    ids = [row["id"] for row in pending_rows]

    updated = triage_db.set_pending_changes_status([ids[0]], "applied", "")

    assert updated == 1
    assert triage_db.get_pending_change_count("pending") == 1

    failed_update = triage_db.set_pending_changes_status([ids[1]], "failed", "test failure")

    assert failed_update == 1
    failed_rows = triage_db.get_pending_changes(status="failed", limit=10)
    assert len(failed_rows) == 1
    assert failed_rows[0]["error_message"] == "test failure"


def test_pending_changes_reject_does_not_override_applied(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    triage_db.insert_pending_changes(
        item_key="WXYZ9999",
        item_title="Guarded Transition",
        changes=[
            {"change_type": "tag_changes", "payload": {"add_tags": ["topic:guard"]}},
        ],
    )

    pending_rows = triage_db.get_pending_changes(status="pending", limit=10)
    assert len(pending_rows) == 1
    change_id = int(pending_rows[0]["id"])

    applied = triage_db.set_pending_changes_status([change_id], "applied", "")
    assert applied == 1

    rejected_after_apply = triage_db.set_pending_changes_status([change_id], "rejected", "should not mutate")
    assert rejected_after_apply == 0

    rows = triage_db.get_pending_changes_by_ids([change_id])
    assert len(rows) == 1
    assert rows[0]["status"] == "applied"
    assert rows[0]["applied_at"]


def test_update_pending_change_payload_requires_pending_status(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()

    triage_db.insert_pending_changes(
        item_key="EDIT1234",
        item_title="Editable Pending",
        changes=[
            {
                "change_type": "tag_changes",
                "payload": {"add_tags": ["topic:old"], "remove_tags": []},
            }
        ],
    )

    rows = triage_db.get_pending_changes(status="pending", limit=10)
    assert len(rows) == 1
    change_id = int(rows[0]["id"])

    updated = triage_db.update_pending_change_payload(
        change_id,
        {"add_tags": ["topic:new"], "remove_tags": ["topic:old"]},
    )
    assert updated is True

    changed_row = triage_db.get_pending_changes_by_ids([change_id])[0]
    assert "topic:new" in changed_row["payload_json"]

    applied = triage_db.set_pending_changes_status([change_id], "applied", "")
    assert applied == 1

    not_updated = triage_db.update_pending_change_payload(
        change_id,
        {"add_tags": ["topic:blocked"], "remove_tags": []},
    )
    assert not_updated is False
