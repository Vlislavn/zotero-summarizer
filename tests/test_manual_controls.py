from __future__ import annotations

import asyncio

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import PendingChangeUpdateRequest, ZoteroItemPriorityUpdateRequest, ZoteroItemTagUpdateRequest
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services import pending as pending_service
from zotero_summarizer.services import zotero as zotero_service


def _state():
    return get_context().state


class _FakeReader:
    def __init__(self, detail: dict[str, object] | None = None):
        self._detail = detail or {
            "item_key": "ABCD1234",
            "title": "Example Paper",
            "tags": ["zs:could_read", "topic:ml"],
            "reading_priority": "could_read",
        }

    def get_item_detail(self, _item_key: str) -> dict[str, object] | None:
        return dict(self._detail)


class _FakeWriter:
    def __init__(self, connector_running: bool = False, failed: list[dict[str, object]] | None = None):
        self._connector_running = connector_running
        self._failed = failed or []
        self.last_changes: list[dict[str, object]] = []
        self.last_create_backup: bool | None = None

    def is_connector_running(self) -> bool:
        return self._connector_running

    def apply_changes(self, changes, create_backup: bool):
        self.last_changes = list(changes)
        self.last_create_backup = bool(create_backup)
        return {
            "applied_ids": [0],
            "failed": list(self._failed),
            "backup_path": None,
        }


def test_zotero_set_item_priority_requires_force_when_connector_running():
    _state().zotero_reader = _FakeReader()
    _state().zotero_writer = _FakeWriter(connector_running=True)
    _state().zotero_error = ""

    async def _run() -> None:
        response = await zotero_service.zotero_set_item_priority(
            "ABCD1234",
            ZoteroItemPriorityUpdateRequest(priority="must_read", force=False),
        )
        assert response["error"] == "zotero_running"
        assert response["requires_force"] is True

    asyncio.run(_run())


def test_zotero_set_item_priority_applies_tag_change():
    reader = _FakeReader(
        {
            "item_key": "ABCD1234",
            "title": "Priority Paper",
            "tags": ["zs:could_read", "topic:test"],
            "reading_priority": "could_read",
        }
    )
    writer = _FakeWriter(connector_running=False)
    _state().zotero_reader = reader
    _state().zotero_writer = writer
    _state().zotero_error = ""

    async def _run() -> None:
        response = await zotero_service.zotero_set_item_priority(
            "ABCD1234",
            ZoteroItemPriorityUpdateRequest(priority="must_read", force=False),
        )
        assert response["updated"] == 1
        assert writer.last_create_backup is True
        assert writer.last_changes
        payload = writer.last_changes[0]["payload_json"]
        assert payload["add_tags"] == ["zs:must_read"]
        assert payload["remove_tags"] == ["zs:could_read"]

    asyncio.run(_run())


def test_zotero_set_item_priority_refreshes_corpus(monkeypatch):
    _state().zotero_reader = _FakeReader()
    _state().zotero_writer = _FakeWriter(connector_running=False)
    _state().zotero_error = ""
    captured: dict[str, object] = {}

    async def _fake_refresh(item_keys):
        captured["item_keys"] = list(item_keys)
        return (1, 0, 0)

    monkeypatch.setattr(zotero_service, "refresh_corpus_items_by_keys", _fake_refresh)

    async def _run() -> None:
        response = await zotero_service.zotero_set_item_priority(
            "ABCD1234",
            ZoteroItemPriorityUpdateRequest(priority="must_read", force=False),
        )
        assert response["updated"] == 1

    asyncio.run(_run())

    assert captured["item_keys"] == ["ABCD1234"]


def test_update_pending_change_normalizes_tag_payload(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        pending_service.triage_db,
        "get_pending_changes_by_ids",
        lambda *_args, **_kwargs: [{"id": 7, "change_type": "tag_changes"}],
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
        lambda *_args, **_kwargs: [{"id": 9, "change_type": "add_to_collection"}],
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
        lambda *_args, **_kwargs: [{"id": 11, "change_type": "add_note"}],
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
        lambda *_args, **_kwargs: [{"id": 13, "change_type": "unsupported_change"}],
    )

    async def _run() -> None:
        with pytest.raises(APIError) as exc_info:
            await pending_service.update_pending_change(
                13,
                PendingChangeUpdateRequest(payload={"foo": "bar"}),
            )
        assert exc_info.value.status_code == 422

    asyncio.run(_run())


def test_zotero_update_item_tags_normalizes_overlap_before_apply():
    reader = _FakeReader(
        {
            "item_key": "ABCD1234",
            "title": "Tag Paper",
            "tags": ["topic:old"],
            "reading_priority": "could_read",
        }
    )
    writer = _FakeWriter(connector_running=False)
    _state().zotero_reader = reader
    _state().zotero_writer = writer
    _state().zotero_error = ""

    async def _run() -> None:
        response = await zotero_service.zotero_update_item_tags(
            "ABCD1234",
            ZoteroItemTagUpdateRequest(add_tags=["topic:a"], remove_tags=["topic:a", "topic:b"]),
        )
        assert response["updated"] == 1
        payload = writer.last_changes[0]["payload_json"]
        assert payload == {
            "add_tags": ["topic:a"],
            "remove_tags": [],
        }

    asyncio.run(_run())


def test_zotero_update_item_tags_returns_noop_when_overlap_cancels_delta():
    reader = _FakeReader(
        {
            "item_key": "ABCD1234",
            "title": "Noop Paper",
            "tags": ["topic:existing", "topic:a"],
            "reading_priority": "could_read",
        }
    )
    writer = _FakeWriter(connector_running=False)
    _state().zotero_reader = reader
    _state().zotero_writer = writer
    _state().zotero_error = ""

    async def _run() -> None:
        response = await zotero_service.zotero_update_item_tags(
            "ABCD1234",
            ZoteroItemTagUpdateRequest(add_tags=["topic:a"], remove_tags=["topic:a"]),
        )
        assert response["updated"] == 0
        assert response["message"] == "Tag update has no net changes"
        assert writer.last_changes == []

    asyncio.run(_run())
