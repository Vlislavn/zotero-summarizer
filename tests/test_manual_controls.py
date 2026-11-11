from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock

import pytest

from tests._direct_write_fixtures import (
    assert_receipts, backups, direct_boundary, native, sql_rows, stored,
)
from tests._zotero_fixtures import add_library_item, add_tag_to_item
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import PendingChangeUpdateRequest, ZoteroItemPriorityUpdateRequest, ZoteroItemTagUpdateRequest
from zotero_summarizer.services.zotero import pending as pending_service
from zotero_summarizer.services.zotero import zotero as zotero_service


def test_zotero_set_item_priority_requires_force_when_connector_running(direct_boundary, monkeypatch):
    b = direct_boundary
    item_id = add_library_item(b.db, item_key="ABCD1234", title="Example Paper")
    for tag in ("zs:could_read", "topic:ml"):
        add_tag_to_item(b.db, item_id=item_id, tag_name=tag)
    writer = b.writer
    dispatch, prepare = Mock(wraps=writer.apply_changes), Mock(wraps=writer.backup_database)
    monkeypatch.setattr(writer, "apply_changes", dispatch)
    monkeypatch.setattr(writer, "backup_database", prepare)
    monkeypatch.setattr(writer, "is_connector_running", lambda: True)
    before, saved, paths = native(b.db), stored(b), backups(b)
    assert saved == [] and paths == []

    response = asyncio.run(zotero_service.zotero_set_item_priority(
        "ABCD1234", ZoteroItemPriorityUpdateRequest(priority="must_read", force=False),
    ))

    assert response["error"] == "zotero_running"
    assert response["requires_force"] is True
    dispatch.assert_not_called()
    prepare.assert_not_called()
    b.refresh.assert_not_awaited()
    assert native(b.db) == before and stored(b) == saved and backups(b) == paths
    assert writer._prepared_backup is None


def test_zotero_set_item_priority_applies_tag_change(direct_boundary, monkeypatch):
    b = direct_boundary
    item_id = add_library_item(b.db, item_key="ABCD1234", title="Priority Paper")
    for tag in ("label:could_read", "topic:test"):
        add_tag_to_item(b.db, item_id=item_id, tag_name=tag)
    writer = b.writer
    dispatch, prepare = Mock(wraps=writer.apply_changes), Mock(wraps=writer.backup_database)
    monkeypatch.setattr(writer, "apply_changes", dispatch)
    monkeypatch.setattr(writer, "backup_database", prepare)
    before, target = native(b.db), writer.target_identity()
    assert stored(b) == [] and backups(b) == []

    response = asyncio.run(zotero_service.zotero_set_item_priority(
        "ABCD1234", ZoteroItemPriorityUpdateRequest(priority="must_read", force=False),
    ))

    assert response["updated"] == 1
    dispatch.assert_called_once()
    changes, create_backup = dispatch.call_args.args
    assert len(changes) == 1 and changes[0]["id"] == response["action_ids"][0]
    payload = json.loads(changes[0]["payload_json"])
    # Human label delta remains exclusive; unrelated namespaces are preserved.
    assert payload["add_tags"] == ["label:must_read"]
    assert payload["remove_tags"] == ["label:could_read"]
    assert create_backup is False
    assert dispatch.call_args.kwargs == {"precreated_backup": response["backup_path"], "expected_target": target}
    prepare.assert_called_once_with(expected_target=target)
    assert writer._prepared_backup is None
    assert response["delivery_status"] == "applied" and len(stored(b)) == 1
    rows = assert_receipts(b, response["action_ids"], "direct_priority", [
        ("ABCD1234", "tag_changes", {"add_tags": ["label:must_read"], "remove_tags": ["label:could_read"]}),
    ], statuses=["applied"])
    assert rows[0]["audit"][0]["requested"] == {"priority": "must_read"}
    assert backups(b) == [response["backup_path"]] and native(response["backup_path"]) == before
    assert sql_rows(response["backup_path"], "PRAGMA integrity_check") == [("ok",)]
    after = native(b.db)
    expected = list(before["tags"])
    expected.remove(("ABCD1234", 1, "label:could_read", 0, 0))
    assert after["tags"] == sorted(expected + [("ABCD1234", 1, "label:must_read", 0, 0)])
    assert after["tag_records"] == sorted(before["tag_records"] + [("label:must_read", 0)])
    for field in ("memberships", "notes", "fields", "deleted"):
        assert after[field] == before[field]
    assert [row for row in after["clocks"] if row[0] != "ABCD1234"] == [
        row for row in before["clocks"] if row[0] != "ABCD1234"]
    b.refresh.assert_awaited_once_with(["ABCD1234"])


def test_zotero_set_item_priority_refreshes_corpus(direct_boundary, monkeypatch):
    b = direct_boundary
    item_id = add_library_item(b.db, item_key="ABCD1234", title="Example Paper")
    for tag in ("zs:could_read", "topic:ml"):
        add_tag_to_item(b.db, item_id=item_id, tag_name=tag)
    before = native(b.db)
    assert stored(b) == [] and backups(b) == []
    captured: dict[str, object] = {}
    events: list[str] = []
    dispatch = Mock(wraps=b.writer.apply_changes)
    monkeypatch.setattr(b.writer, "apply_changes", dispatch)

    async def _fake_refresh(item_keys):
        rows = stored(b)
        assert len(rows) == 1 and rows[0]["status"] == "applied" and rows[0]["applied_at"]
        assert [event["event"] for event in json.loads(rows[0]["audit_json"])] == [
            "direct_requested", "direct_applied"]
        assert ("ABCD1234", 1, "label:must_read", 0, 0) in native(b.db)["tags"]
        events.append("native_applied")
        captured["item_keys"] = list(item_keys)
        return (1, 0, 0)

    monkeypatch.setattr(zotero_service, "refresh_corpus_items_by_keys", _fake_refresh)

    response = asyncio.run(zotero_service.zotero_set_item_priority(
        "ABCD1234", ZoteroItemPriorityUpdateRequest(priority="must_read", force=False),
    ))

    assert response["updated"] == 1
    assert captured["item_keys"] == ["ABCD1234"]
    assert events == ["native_applied"]
    dispatch.assert_called_once()
    assert response["delivery_status"] == "applied" and len(stored(b)) == 1
    assert_receipts(b, response["action_ids"], "direct_priority", [
        ("ABCD1234", "tag_changes", {"add_tags": ["label:must_read"], "remove_tags": []}),
    ], statuses=["applied"])
    assert native(b.db)["tags"] == sorted(before["tags"] + [("ABCD1234", 1, "label:must_read", 0, 0)])
    assert backups(b) == [response["backup_path"]] and native(response["backup_path"]) == before


def test_update_pending_change_normalizes_tag_payload(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        pending_service.triage_db,
        "get_pending_changes_by_ids",
        lambda *_args, **_kwargs: [{"id": 7, "change_type": "tag_changes", "audit": []}],
    )

    def _capture(change_id: int, payload: dict[str, object]) -> bool:
        captured["change_id"] = change_id
        captured["payload"] = payload
        return True

    monkeypatch.setattr(pending_service.triage_db, "update_pending_change_payload", _capture)

    async def _run() -> None:
        response = await pending_service.update_pending_change(
            7,
            PendingChangeUpdateRequest(
                payload={
                    "add_tags": [" topic:a ", "topic:a", ""],
                    "remove_tags": "topic:a, topic:b",
                }
            ),
        )
        assert response["updated"] == 1

    asyncio.run(_run())

    assert captured["change_id"] == 7
    assert captured["payload"] == {
        "add_tags": ["topic:a"],
        "remove_tags": ["topic:b"],
    }


def test_update_pending_change_normalizes_collection_payload(monkeypatch):
    monkeypatch.setattr(
        pending_service.triage_db,
        "get_pending_changes_by_ids",
        lambda *_args, **_kwargs: [{"id": 9, "change_type": "add_to_collection", "audit": []}],
    )
    captured: dict[str, object] = {}

    def _capture(change_id: int, payload: dict[str, object]) -> bool:
        captured["change_id"] = change_id
        captured["payload"] = payload
        return True

    monkeypatch.setattr(pending_service.triage_db, "update_pending_change_payload", _capture)

    async def _run() -> None:
        response = await pending_service.update_pending_change(
            9,
            PendingChangeUpdateRequest(payload={"collection_path": " Speech > Verification "}),
        )
        assert response["updated"] == 1

    asyncio.run(_run())

    assert captured["change_id"] == 9
    assert captured["payload"] == {"collection_path": "Speech > Verification"}


def test_update_pending_change_normalizes_note_payload(monkeypatch):
    monkeypatch.setattr(
        pending_service.triage_db,
        "get_pending_changes_by_ids",
        lambda *_args, **_kwargs: [{"id": 11, "change_type": "add_note", "audit": []}],
    )
    captured: dict[str, object] = {}

    def _capture(change_id: int, payload: dict[str, object]) -> bool:
        captured["change_id"] = change_id
        captured["payload"] = payload
        return True

    monkeypatch.setattr(pending_service.triage_db, "update_pending_change_payload", _capture)

    async def _run() -> None:
        response = await pending_service.update_pending_change(
            11,
            PendingChangeUpdateRequest(payload={"note_title": "  Custom  ", "note_html": "  <p>Updated</p>  "}),
        )
        assert response["updated"] == 1

    asyncio.run(_run())

    assert captured["change_id"] == 11
    assert captured["payload"] == {"note_title": "Custom", "note_html": "<p>Updated</p>"}


def test_update_pending_change_rejects_unsupported_payload_type(monkeypatch):
    monkeypatch.setattr(
        pending_service.triage_db,
        "get_pending_changes_by_ids",
        lambda *_args, **_kwargs: [{"id": 13, "change_type": "unsupported_change", "audit": []}],
    )

    async def _run() -> None:
        with pytest.raises(APIError) as exc_info:
            await pending_service.update_pending_change(
                13,
                PendingChangeUpdateRequest(payload={"foo": "bar"}),
            )
        assert exc_info.value.status_code == 422

    asyncio.run(_run())


def test_zotero_update_item_tags_normalizes_overlap_before_apply(direct_boundary, monkeypatch):
    b = direct_boundary
    item_id = add_library_item(b.db, item_key="ABCD1234", title="Tag Paper")
    add_tag_to_item(b.db, item_id=item_id, tag_name="topic:old")
    writer = b.writer
    dispatch, prepare = Mock(wraps=writer.apply_changes), Mock(wraps=writer.backup_database)
    monkeypatch.setattr(writer, "apply_changes", dispatch)
    monkeypatch.setattr(writer, "backup_database", prepare)
    before, target = native(b.db), writer.target_identity()
    assert stored(b) == [] and backups(b) == []

    response = asyncio.run(zotero_service.zotero_update_item_tags(
        "ABCD1234", ZoteroItemTagUpdateRequest(add_tags=["topic:a"], remove_tags=["topic:a", "topic:b"]),
    ))

    assert response["updated"] == 1
    dispatch.assert_called_once()
    changes, create_backup = dispatch.call_args.args
    assert len(changes) == 1 and changes[0]["id"] == response["action_ids"][0]
    payload = json.loads(changes[0]["payload_json"])
    assert payload == {
        "add_tags": ["topic:a"],
        "remove_tags": [],
    }
    assert create_backup is False
    assert dispatch.call_args.kwargs == {"precreated_backup": response["backup_path"], "expected_target": target}
    prepare.assert_called_once_with(expected_target=target)
    assert writer._prepared_backup is None
    assert response["delivery_status"] == "applied" and len(stored(b)) == 1
    rows = assert_receipts(b, response["action_ids"], "direct_tags", [
        ("ABCD1234", "tag_changes", {"add_tags": ["topic:a"], "remove_tags": []}),
    ], statuses=["applied"])
    assert rows[0]["audit"][0]["requested"] == {"add_tags": ["topic:a"], "remove_tags": ["topic:a", "topic:b"]}
    assert backups(b) == [response["backup_path"]] and native(response["backup_path"]) == before
    assert sql_rows(response["backup_path"], "PRAGMA integrity_check") == [("ok",)]
    after = native(b.db)
    assert after["tags"] == sorted(before["tags"] + [("ABCD1234", 1, "topic:a", 0, 0)])
    assert after["tag_records"] == sorted(before["tag_records"] + [("topic:a", 0)])
    for field in ("memberships", "notes", "fields", "deleted"):
        assert after[field] == before[field]
    assert [row for row in after["clocks"] if row[0] != "ABCD1234"] == [
        row for row in before["clocks"] if row[0] != "ABCD1234"]
    b.refresh.assert_awaited_once_with(["ABCD1234"])


def test_zotero_update_item_tags_returns_noop_when_overlap_cancels_delta(direct_boundary, monkeypatch):
    b = direct_boundary
    item_id = add_library_item(b.db, item_key="ABCD1234", title="Noop Paper")
    for tag in ("topic:existing", "topic:a"):
        add_tag_to_item(b.db, item_id=item_id, tag_name=tag)
    writer = b.writer
    dispatch, prepare = Mock(wraps=writer.apply_changes), Mock(wraps=writer.backup_database)
    monkeypatch.setattr(writer, "apply_changes", dispatch)
    monkeypatch.setattr(writer, "backup_database", prepare)
    before, saved, paths = native(b.db), stored(b), backups(b)
    assert saved == [] and paths == []

    response = asyncio.run(zotero_service.zotero_update_item_tags(
        "ABCD1234", ZoteroItemTagUpdateRequest(add_tags=["topic:a"], remove_tags=["topic:a"]),
    ))

    assert response["updated"] == 0
    assert response["message"] == "Tag update has no net changes"
    dispatch.assert_not_called()
    prepare.assert_not_called()
    b.refresh.assert_not_awaited()
    assert native(b.db) == before and stored(b) == saved and backups(b) == paths
    assert writer._prepared_backup is None
    assert not ({"action_ids", "backup_path", "delivery_status"} & response.keys())
