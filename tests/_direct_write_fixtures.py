"""Owned direct-editor setup/oracles; seeded direct rows are prospective fixtures only."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._zotero_fixtures import (
    add_collection_link, add_library_item, add_tag_to_item, build_zotero_db,
)
from zotero_summarizer.api.errors import install_error_handlers
from zotero_summarizer.api.routes.pending import router as pending_router
from zotero_summarizer.api.routes.zotero import router as zotero_router
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import (
    PendingChangeApplyRequest, ZoteroItemCollectionUpdateRequest,
    ZoteroItemPriorityUpdateRequest, ZoteroItemTagUpdateRequest,
)
from zotero_summarizer.services import corpus
from zotero_summarizer.services.zotero import pending, zotero
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import pending_actions, repositories

KEY = "DIR197AA"
OTHER = "DIR197BB"
WEEKLY = {"add_tags": ["ri:weekly", "ri:yes"], "remove_tags": []}


@pytest.fixture
def direct_boundary(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    with repositories.with_db_path(settings.triage_db_path):
        repositories.init_db()
        db = build_zotero_db(tmp_path / "owned-zotero")
        parent = add_library_item(db, item_key=KEY, title="Owned direct paper")
        other = add_library_item(db, item_key=OTHER, title="Unrelated paper")
        for name, kind in [("topic:old", 1), ("Topic:Old", 0),
                           ("topic:keep", 0), ("label:could_read", 0)]:
            add_tag_to_item(db, item_id=parent, tag_name=name, tag_type=kind)
        add_tag_to_item(db, item_id=other, tag_name="topic:old", tag_type=1)
        add_collection_link(db, item_id=parent, collection_id=90)
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO collections(collectionID,collectionName,libraryID,key) "
                         "VALUES (92,'Foreign',2,'FOREIGN')")
        writer, reader = ZoteroWriter(db.parent), ZoteroReader(db.parent)
        monkeypatch.setattr(writer, "is_connector_running", lambda: False)
        monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
        monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", lambda: reader)
        refresh = AsyncMock(return_value=(0, 0, 0))
        monkeypatch.setattr(zotero, "refresh_corpus_items_by_keys", refresh)
        monkeypatch.setattr(corpus, "refresh_corpus_items_by_keys", refresh)
        app = FastAPI()
        app.include_router(zotero_router)
        app.include_router(pending_router)
        install_error_handlers(app)
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, queue_db=settings.triage_db_path,
                                  settings=settings, writer=writer, reader=reader,
                                  client=client, refresh=refresh)


def edit(kind="tags", *, key=KEY, **values):
    if kind == "tags":
        call = zotero.zotero_update_item_tags(key, ZoteroItemTagUpdateRequest(**values))
    elif kind == "priority":
        call = zotero.zotero_set_item_priority(key, ZoteroItemPriorityUpdateRequest(**values))
    elif kind == "collections":
        call = zotero.zotero_update_item_collections(key, ZoteroItemCollectionUpdateRequest(**values))
    else:
        raise ValueError("Unknown test editor")
    return asyncio.run(call)


def sql_rows(path, statement, params=()):
    with closing(sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True)) as conn:
        return conn.execute(statement, params).fetchall()


def native(path):
    return {
        "tags": sql_rows(path,
            "SELECT i.key,i.libraryID,t.name,t.type,it.type FROM itemTags it "
            "JOIN items i ON i.itemID=it.itemID JOIN tags t ON t.tagID=it.tagID "
            "ORDER BY i.key,t.name"),
        "memberships": sql_rows(path,
            "SELECT i.key,i.libraryID,c.key,c.libraryID FROM collectionItems ci "
            "JOIN items i ON i.itemID=ci.itemID JOIN collections c ON c.collectionID=ci.collectionID "
            "ORDER BY i.key,c.key"),
        "tag_records": sql_rows(path, "SELECT name,type FROM tags ORDER BY name"),
        "clocks": sql_rows(path,
            "SELECT key,version,synced,dateModified,clientDateModified FROM items ORDER BY key"),
        "notes": sql_rows(path, "SELECT itemID,parentItemID,title,note FROM itemNotes ORDER BY itemID"),
        "fields": sql_rows(path,
            "SELECT i.key,i.libraryID,f.fieldName,v.value FROM itemData d "
            "JOIN items i ON i.itemID=d.itemID JOIN fields f ON f.fieldID=d.fieldID "
            "JOIN itemDataValues v ON v.valueID=d.valueID ORDER BY i.key,f.fieldName"),
        "deleted": sql_rows(path, "SELECT itemID,dateDeleted FROM deletedItems ORDER BY itemID"),
    }


def stored(b):
    with closing(sqlite3.connect(f"{b.queue_db.as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM pending_changes ORDER BY id")]


def backups(b):
    return sorted(str(path) for path in b.db.parent.glob("zotero.sqlite.backup_*"))


def queue(payload, *, kind="tag_changes", key=KEY):
    repositories.insert_pending_changes(key, "Owned proposal", [{"change_type": kind, "payload": payload}])
    return max(repositories.get_pending_changes(status=None, item_key=key), key=lambda row: row["id"])


def presented(row):
    rows = asyncio.run(pending.list_pending_changes(status="all", item_key=row["item_key"])).items
    return next(value for value in rows if value["id"] == row["id"])


def apply_presented(rows, **flags):
    return asyncio.run(pending.apply_pending_changes(PendingChangeApplyRequest(
        change_ids=[row["id"] for row in rows], previews={
            row["id"]: row["approval_preview"]["preview_sha256"] for row in rows}, **flags,
    )))


def seed_direct(b, status="failed", *, payload=None, source="direct_tags", key=KEY):
    """Seed a proposed history record, NOT a receipt emitted by today's production code."""
    defaults = {"direct_tags": WEEKLY,
                "direct_priority": {"add_tags": ["label:must_read"], "remove_tags": ["label:could_read"]},
                "direct_collections": {"collection_key": "RESEARCH1"}}
    payload = payload if payload is not None else defaults[source]
    backup = b.writer.backup_database()
    requested = {"priority": "must_read"} if source == "direct_priority" else payload
    if source == "direct_collections":
        requested = {"add": [payload], "remove": []}
    kind = "add_to_collection" if source == "direct_collections" else "tag_changes"
    event = {"event": "direct_requested", "actor": "current_user", "source": source,
             "requested": requested, "target": b.writer.target_identity().model_dump(),
             "recovery": "backup_only", "backup_path": backup}
    with pending_actions.locked_queue() as conn:
        cursor = conn.execute("INSERT INTO pending_changes "
            "(item_key,item_title,change_type,payload_json,status) VALUES (?,?,?,?,?)",
            (key, "Prospective direct history", kind, json.dumps(payload, ensure_ascii=False), "applying"))
        change_id = int(cursor.lastrowid)
        pending_actions.record(conn, change_id, event, status="applying", backup_path=backup)
        if status != "applying":
            pending_actions.record(conn, change_id, {
                "event": "direct_applied" if status == "applied" else "direct_failed",
                "actor": "system", "source": source, "expected_io": False,
            }, status=status, error="Owned failed receipt" if status == "failed" else "")
    return repositories.get_pending_changes_by_ids([change_id])[0]


def assert_receipts(b, ids, source, actions, *, statuses, failed_io=None):
    """Bind returned IDs to literal request-order actions, never receipt-derived gold."""
    assert ids and len(ids) == len(set(ids)) and all(type(value) is int and value > 0 for value in ids)
    assert all(left < right for left, right in zip(ids, ids[1:]))
    found = {row["id"]: row for row in repositories.get_pending_changes_by_ids(ids)}
    assert set(found) == set(ids) and len(ids) == len(actions) == len(statuses)
    rows = [found[change_id] for change_id in ids]
    failed_io = failed_io or {}
    assert set(failed_io) == {index for index, status in enumerate(statuses) if status == "failed"}
    for index, (row, action, status) in enumerate(zip(rows, actions, statuses)):
        assert (row["item_key"], row["change_type"], json.loads(row["payload_json"])) == action
        assert row["status"] == status
        first = row["audit"][0]
        assert first["event"] == "direct_requested" and first["actor"] == "current_user"
        assert first["source"] == source
        assert isinstance(first["at"], str) and first["at"]
        assert first["target"] == b.writer.target_identity().model_dump()
        assert first["recovery"] == "backup_only"
        assert first["backup_path"] == row["backup_path"] and row["backup_path"] in backups(b)
        assert row.get("proposal_json") is None and "generated_preview" not in row
        assert "preview_sha256" not in first and "inverse" not in first
        assert presented(row)["approval_preview"] is None
        if status == "applying":
            assert row["applied_at"] is None
            assert row["error_message"] is None or isinstance(row["error_message"], str)
            for diagnostic in row["audit"][1:]:
                assert (diagnostic["event"], diagnostic["actor"], diagnostic["source"]) == (
                    "direct_uncertain", "system", source)
                assert diagnostic.get("status", "applying") == "applying"
                assert diagnostic.get("delivery_status", "uncertain") == "uncertain"
                assert "applied_ids" not in diagnostic and "failed_ids" not in diagnostic
            continue
        assert status in {"applied", "failed"}
        terminal_name = "direct_applied" if status == "applied" else "direct_failed"
        assert [event["event"] for event in row["audit"]] == ["direct_requested", terminal_name]
        terminal = row["audit"][-1]
        assert terminal["actor"] == "system" and terminal["source"] == source
        assert isinstance(terminal["at"], str) and terminal["at"]
        if status == "applied":
            assert isinstance(row["applied_at"], str) and row["applied_at"]
            assert row["error_message"] == ""
        else:
            assert type(failed_io[index]) is bool and terminal["expected_io"] is failed_io[index]
            assert row["applied_at"] is None
            assert isinstance(row["error_message"], str) and row["error_message"]
    return rows


class ObservedLock:
    """Delegate the actual RLock; record a contender's real nonblocking acquisition."""
    def __init__(self, actual, role, phase):
        self.actual, self.role, self.phase = actual, role, phase
        self.probe = []

    def __enter__(self):
        if self.role.get() == "B" and not self.probe:
            acquired = self.actual.acquire(blocking=False)
            self.probe.append(acquired)
            self.phase.set()
            if acquired:
                return self
        self.actual.acquire()
        return self

    def __exit__(self, *_args):
        self.actual.release()
