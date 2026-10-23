"""Ordinary Pending approval interleaving; owned queue and native SQLite only."""
import asyncio
import json
import socket
import sqlite3
from types import SimpleNamespace

import pytest


KEY = "ORD186AA"
PAYLOAD_A = {"add_tags": ["pending-186-A"], "remove_tags": []}
PAYLOAD_B = {"add_tags": ["pending-186-B"], "remove_tags": []}


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    import dotenv
    import keyring

    def forbidden_io(*_args, **_kwargs):
        raise AssertionError("No network, private dotenv or keyring access in this test")

    monkeypatch.setenv("ZOTERO_SUMMARIZER_HOME", str(tmp_path))
    monkeypatch.setattr(socket.socket, "connect", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_io)
    monkeypatch.setattr(dotenv, "load_dotenv", forbidden_io)
    monkeypatch.setattr(keyring, "get_password", forbidden_io)

    from tests._generated_write_fixtures import install_generated_writer
    from tests._zotero_fixtures import add_library_item
    from zotero_summarizer.services import corpus
    from zotero_summarizer.services.zotero import pending
    from zotero_summarizer.storage import repositories

    async def refresh(_keys):
        return None

    monkeypatch.setattr(corpus, "refresh_corpus_items_by_keys", refresh)
    with repositories.with_db_path(tmp_path / "triage.db"):
        db, writer = install_generated_writer(tmp_path, monkeypatch)
        add_library_item(db, item_key=KEY, title="Owned ordinary Pending paper")
        dispatched = []
        dispatch = writer._dispatch_change

        def record_dispatch(conn, change, cols):
            dispatched.append({name: change[name] for name in (
                "id", "item_key", "change_type", "payload_json",
            )})
            dispatch(conn, change, cols)

        monkeypatch.setattr(writer, "_dispatch_change", record_dispatch)
        yield SimpleNamespace(db=db, pending=pending, queue=repositories,
                              dispatched=dispatched)


def _present(boundary):
    assert boundary.queue.insert_pending_changes(KEY, "Owned ordinary Pending paper", [{
        "change_type": "tag_changes", "payload": PAYLOAD_A,
    }]) == 1
    dto = asyncio.run(boundary.pending.list_pending_changes()).model_dump(mode="json")
    row, = dto["items"]
    assert (row["item_key"], row["change_type"], row["status"]) == (KEY, "tag_changes", "pending")
    assert "generated_preview" not in row
    assert json.loads(row["payload_json"]) == PAYLOAD_A
    return row


def _ui_apply_request(presented):
    from zotero_summarizer.models import PendingChangeApplyRequest

    # Pending Apply transports the common snapshot that was actually presented.
    body = {"change_ids": [presented["id"]], "force": False, "retry": False,
            "previews": {presented["id"]: presented["approval_preview"]["preview_sha256"]}}
    request = PendingChangeApplyRequest.model_validate(body)
    assert request.model_dump(exclude_unset=True) == body
    return request


def _native_tags(db):
    with sqlite3.connect(db) as conn:
        return [row[0] for row in conn.execute(
            "SELECT t.name FROM tags t JOIN itemTags it ON it.tagID=t.tagID "
            "JOIN items i ON i.itemID=it.itemID WHERE i.key=? ORDER BY t.name", (KEY,),
        )]


def test_presented_ordinary_payload_cannot_authorize_same_id_replacement(boundary, record_property):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.models import PendingChangeUpdateRequest

    b = boundary
    presented = _present(b)
    captured = json.dumps(presented, sort_keys=True)
    request = _ui_apply_request(presented)
    edited = asyncio.run(b.pending.update_pending_change(
        presented["id"], PendingChangeUpdateRequest(payload=PAYLOAD_B),
    ))
    assert edited["updated"] == 1 and edited["payload"] == PAYLOAD_B
    durable, = b.queue.get_pending_changes_by_ids([presented["id"]])
    assert durable["id"] == presented["id"] and durable["status"] == "pending"
    assert json.loads(durable["payload_json"]) == PAYLOAD_B
    assert json.dumps(presented, sort_keys=True) == captured
    assert json.loads(presented["payload_json"]) == PAYLOAD_A
    assert b.dispatched == [] and _native_tags(b.db) == []

    try:
        outcome = asyncio.run(b.pending.apply_pending_changes(request))
    except APIError as exc:
        assert exc.status_code == 409
        outcome = {"error": exc.error, "status_code": exc.status_code}

    receipt = {"presented": presented, "durable_before_apply": durable,
               "request": request.model_dump(exclude_unset=True), "outcome": outcome,
               "native_dispatch": b.dispatched, "native_tags": _native_tags(b.db)}
    record_property("ordinary_pending_receipt", json.dumps(receipt, sort_keys=True))
    assert b.dispatched == [], json.dumps(receipt, sort_keys=True)
    assert receipt["native_tags"] == []


def test_unchanged_presented_ordinary_payload_still_reaches_native_writer(boundary, record_property):
    b = boundary
    presented = _present(b)
    request = _ui_apply_request(presented)

    result = asyncio.run(b.pending.apply_pending_changes(request))

    record_property("ordinary_pending_receipt", json.dumps({
        "presented": presented, "request": request.model_dump(exclude_unset=True),
        "outcome": result, "native_dispatch": b.dispatched, "native_tags": _native_tags(b.db),
    }, sort_keys=True))
    assert result["applied"] == 1 and result["failed"] == 0
    assert b.dispatched == [{"id": presented["id"], "item_key": KEY,
                             "change_type": "tag_changes", "payload_json": presented["payload_json"]}]
    assert _native_tags(b.db) == PAYLOAD_A["add_tags"]
    applied, = b.queue.get_pending_changes_by_ids([presented["id"]])
    assert applied["status"] == "applied"
    with sqlite3.connect(result["backup_path"]) as backup:
        assert backup.execute("SELECT COUNT(*) FROM itemTags").fetchone()[0] == 0
