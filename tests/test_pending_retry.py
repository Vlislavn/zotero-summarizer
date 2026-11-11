from __future__ import annotations

import asyncio
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from tests.test_pending_approval_snapshot import KEY, _native_tags, _present, boundary
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import PendingChangeApplyRequest, PendingChangeMutationRequest
from zotero_summarizer.services.zotero import pending, zotero
from zotero_summarizer.storage import repositories


def presented():
    return asyncio.run(pending.list_pending_changes(status="all")).items


def counts(path):
    with closing(sqlite3.connect(path)) as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("itemTags", "itemNotes"))


def _stage(b, count=1):
    for index in range(count):
        b.queue.insert_pending_changes(KEY, "Owned paper", [{
            "change_type": "tag_changes", "payload": {"add_tags": [f"retry-{index}"], "remove_tags": []},
        }])
    return asyncio.run(pending.list_pending_changes()).items


def _apply(rows, **kwargs):
    return asyncio.run(pending.apply_pending_changes(PendingChangeApplyRequest(
        change_ids=[row["id"] for row in rows],
        previews={row["id"]: row["approval_preview"]["preview_sha256"] for row in rows}, **kwargs,
    )))


def test_ordinary_target_switch_requires_new_explicit_presentation(boundary, tmp_path, monkeypatch):
    row = _present(boundary)
    other = build_zotero_db(tmp_path / "other")
    add_library_item(other, item_key=KEY, title="Other owned target")
    writer = ZoteroWriter(other.parent)
    backup = Mock(wraps=writer.backup_database)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(writer, "backup_database", backup)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    with pytest.raises(APIError) as error:
        _apply([row], force=True)
    assert error.value.status_code == 409
    backup.assert_not_called()
    assert counts(other) == counts(boundary.db) == (0, 0)
    refreshed, = presented()
    assert refreshed["approval_preview"]["target"] == writer.target_identity().model_dump()
    assert _apply([refreshed])["applied"] == 1
    assert counts(other) == (1, 0) and counts(boundary.db) == (0, 0)


def test_missing_writer_and_legacy_history_never_gain_approval(boundary, monkeypatch):
    row = _present(boundary)
    repositories.set_pending_changes_status([row["id"]], "applied")
    assert presented()[0]["approval_preview"] is None
    with pytest.raises(APIError):
        _apply([row])
    repositories.insert_pending_changes(KEY, "Pending", [{"change_type": "tag_changes", "payload": {}}])

    def unavailable():
        raise APIError(error="zotero_unavailable", message="Owned unavailable writer", status_code=503)

    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", unavailable)
    assert all(item["approval_preview"] is None for item in presented())
    assert counts(boundary.db) == (0, 0)


@pytest.mark.parametrize("state", ["applied", "applying"])
def test_approved_ordinary_history_without_writer_has_no_approval(boundary, monkeypatch, state):
    b = boundary
    row, = _stage(b)
    writer = zotero.get_zotero_writer_or_raise()
    if state == "applying":
        with monkeypatch.context() as patch:
            patch.setattr(writer, "apply_changes", Mock(side_effect=RuntimeError("Owned uncertain delivery")))
            with pytest.raises(RuntimeError, match="Owned uncertain delivery"):
                _apply([row])
    else:
        assert _apply([row])["applied"] == 1
    saved, = b.queue.get_pending_changes_by_ids([row["id"]])
    saved = deepcopy(saved)
    assert saved["status"] == state
    approved, = [event for event in saved["audit"] if event["event"] == "approved"]
    assert approved["target"] == row["approval_preview"]["target"]
    backup_path = Path(saved["backup_path"])
    assert str(backup_path) == approved["backup_path"]
    backups = set(b.db.parent.glob(writer._BACKUP_GLOB))
    assert backups == {backup_path}
    backup_bytes = backup_path.read_bytes()
    dispatched = deepcopy(b.dispatched)
    with closing(sqlite3.connect(b.db)) as conn:
        native_before = tuple(conn.iterdump())
    assert _native_tags(b.db) == (["retry-0"] if state == "applied" else [])

    backup, native = Mock(wraps=writer.backup_database), Mock(wraps=writer.apply_changes)
    target = Mock(side_effect=AssertionError("History must retain its recorded target"))
    monkeypatch.setattr(writer, "backup_database", backup)
    monkeypatch.setattr(writer, "apply_changes", native)
    monkeypatch.setattr(writer, "target_identity", target)
    available, = presented()
    assert available == {**saved, "approval_preview": row["approval_preview"]}

    def unavailable():
        raise APIError(error="zotero_unavailable", message="Owned unavailable writer", status_code=503)

    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", unavailable)
    unavailable_row, = presented()
    assert {key: value for key, value in unavailable_row.items() if key != "approval_preview"} == saved
    assert b.queue.get_pending_changes_by_ids([row["id"]]) == [saved]
    assert b.dispatched == dispatched
    assert set(b.db.parent.glob(writer._BACKUP_GLOB)) == backups
    assert backup_path.read_bytes() == backup_bytes
    with closing(sqlite3.connect(b.db)) as conn:
        assert tuple(conn.iterdump()) == native_before
    backup.assert_not_called()
    native.assert_not_called()
    target.assert_not_called()
    with pytest.raises(APIError) as error:
        _apply([row])
    assert error.value.error == "zotero_unavailable" and error.value.status_code == 503
    assert unavailable_row["approval_preview"] is None


def test_apply_with_retry_fetches_failed_changes(boundary, monkeypatch):
    b = boundary
    rows = _stage(b, 2)
    writer = zotero.get_zotero_writer_or_raise()

    def fail(conn, change, cols):
        raise ValueError("Owned confirmed failure")

    with monkeypatch.context() as patch:
        patch.setattr(writer, "_dispatch_change", fail)
        failed = _apply(rows)
    assert failed["applied"] == 0 and failed["failed"] == 2
    assert {item["id"] for item in failed["failed_items"]} == {row["id"] for row in rows}
    assert all(item["error"] == "Owned confirmed failure" for item in failed["failed_items"])
    assert _native_tags(b.db) == []
    retry_rows = asyncio.run(pending.list_pending_changes(status="failed")).items
    assert len(retry_rows) == 2

    result = _apply(retry_rows, retry=True)

    assert result["applied"] == 2 and result["failed"] == 0
    assert _native_tags(b.db) == ["retry-0", "retry-1"]
    saved = b.queue.get_pending_changes_by_ids([row["id"] for row in rows])
    assert all(row["status"] == "applied" and row["error_message"] == "" for row in saved)
    assert all([event["event"] for event in row["audit"]] == ["approved", "failed", "approved", "applied"] for row in saved)


def test_apply_without_retry_uses_pending_status(boundary):
    b = boundary
    rows = _stage(b)
    result = _apply(rows)
    assert result["applied"] == 1 and result["failed"] == 0
    assert _native_tags(b.db) == ["retry-0"]
    assert b.queue.get_pending_changes("applied")[0]["id"] == rows[0]["id"]


@pytest.mark.parametrize("open_sibling", [False, True])
def test_apply_never_removes_inbox_implicitly(boundary, monkeypatch, open_sibling):
    b = boundary
    rows = _stage(b, 2 if open_sibling else 1)
    writer = zotero.get_zotero_writer_or_raise()
    remove = Mock(side_effect=AssertionError("Implicit Inbox mutation"))
    monkeypatch.setattr(writer, "remove_items_from_collection", remove)
    with sqlite3.connect(b.db) as conn:
        item_id, = conn.execute("SELECT itemID FROM items WHERE key=?", (KEY,)).fetchone()
        conn.execute("INSERT INTO collectionItems VALUES (?,90)", (item_id,))

    result = _apply(rows[:1])

    assert result["applied"] == 1 and result["failed"] == 0
    remove.assert_not_called()
    with sqlite3.connect(b.db) as conn:
        assert conn.execute("SELECT collectionID FROM collectionItems WHERE itemID=?", (item_id,)).fetchall() == [(90,)]
    assert len(b.queue.get_pending_changes()) == int(open_sibling)
    assert "inbox_removed" not in result and "inbox_removed_error" not in result


def test_retry_flag_defaults_to_false():
    assert PendingChangeMutationRequest(change_ids=[1]).retry is False
    assert PendingChangeApplyRequest(change_ids=[1], previews={1: "a" * 64}).retry is False


def test_apply_has_no_inbox_followup_or_receipt(boundary, monkeypatch):
    b = boundary
    rows = _stage(b)
    writer = zotero.get_zotero_writer_or_raise()
    remove = Mock(side_effect=AssertionError("No followup should be attempted"))
    monkeypatch.setattr(writer, "remove_items_from_collection", remove)

    result = _apply(rows)

    assert result == {"applied": 1, "failed": 0, "failed_items": [], "backup_path": result["backup_path"]}
    assert _native_tags(b.db) == ["retry-0"]
    remove.assert_not_called()
    with sqlite3.connect(result["backup_path"]) as backup:
        assert backup.execute("SELECT COUNT(*) FROM itemTags").fetchone()[0] == 0
