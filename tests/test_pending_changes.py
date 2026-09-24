from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

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


def test_pending_history_item_filter_precedes_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()
    triage_db.insert_pending_changes("TARGET01", "Target", [{"change_type": "add_note", "payload": {"note_html": "old"}}])
    for index in range(8):
        triage_db.insert_pending_changes(
            f"OTHER{index:03d}", "Unrelated", [{"change_type": "add_note", "payload": {"note_html": "new"}}],
        )
    rows = triage_db.get_pending_changes(status=None, limit=1, item_key="TARGET01")
    assert len(rows) == 1 and rows[0]["item_key"] == "TARGET01"


def test_pending_exact_signature_lookup_checks_all_statuses_without_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()
    payload = {"add_tags": ["tag:x"], "remove_tags": []}
    triage_db.insert_pending_changes(
        "TARGET01", "Target", [{"change_type": "tag_changes", "payload": payload}],
    )
    row = triage_db.get_pending_changes(item_key="TARGET01", limit=1)[0]
    triage_db.set_pending_changes_status([row["id"]], "rejected")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert triage_db.pending_change_exists("TARGET01", "tag_changes", serialized)
    assert not triage_db.pending_change_exists(
        "TARGET01", "tag_changes", serialized, status="pending",
    )


def test_pending_signature_insert_is_atomic_across_concurrent_callers(monkeypatch, tmp_path):
    db_path = tmp_path / "triage_history.db"
    monkeypatch.setattr(triage_db, "DB_PATH", db_path)
    triage_db.init_db()
    payload = {"add_tags": ["ri:weekly", "ri:yes"], "remove_tags": []}

    def insert():
        return triage_db.insert_pending_change_if_absent(
            "TARGET01", "Target", "tag_changes", payload,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: insert(), range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7
    assert len(triage_db.get_pending_changes(status=None, limit=10, item_key="TARGET01")) == 1


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

    failed_update = triage_db.set_pending_changes_status(
        [ids[1]], "failed", "test failure"
    )

    assert failed_update == 1
    failed_rows = triage_db.get_pending_changes(status="failed", limit=10)
    assert len(failed_rows) == 1
    assert failed_rows[0]["error_message"] == "test failure"

    retried = triage_db.set_pending_changes_status(
        [ids[1]],
        "applied",
        "",
        expected_status="failed",
    )
    assert retried == 1
    assert triage_db.get_pending_change_count("failed") == 0


def test_item_remains_open_until_all_sibling_changes_finish(monkeypatch, tmp_path):
    monkeypatch.setattr(triage_db, "DB_PATH", tmp_path / "triage_history.db")
    triage_db.init_db()
    triage_db.insert_pending_changes(
        "PAPER1",
        "Paper",
        [
            {"change_type": "tag_changes", "payload": {}},
            {"change_type": "add_note", "payload": {}},
        ],
    )
    rows = triage_db.get_pending_changes("pending", 10)

    triage_db.set_pending_changes_status([rows[0]["id"]], "applied", "")
    assert triage_db.item_keys_without_open_changes(["PAPER1"]) == []

    triage_db.set_pending_changes_status([rows[1]["id"]], "applied", "")
    assert triage_db.item_keys_without_open_changes(["PAPER1"]) == ["PAPER1"]


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

    rejected_after_apply = triage_db.set_pending_changes_status(
        [change_id], "rejected", "should not mutate"
    )
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
