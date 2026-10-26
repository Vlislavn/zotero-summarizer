"""Zotero read/write regressions on isolated databases (A014–A023)."""
import asyncio
import json
import sqlite3

import pytest

from tests._zotero_fixtures import add_feed_item, add_library_item, add_tag_to_item, build_zotero_db
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from tests.test_generated_write_review import boundary
from tests._generated_write_fixtures import config, paper_io, published_review
from tests.test_review_cache import _isolate_cache


def _change(kind, payload, change_id=1):
    return {"id": change_id, "item_key": "PARENT", "change_type": kind, "payload_json": payload}


def test_reader_observes_wal_commits_without_writing(tmp_path):
    db = build_zotero_db(tmp_path / "Zotero #1")
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        reader = ZoteroReader(db.parent)
        add_library_item(db, item_key="PARENT", title="WAL paper")
        assert reader.get_library_stats()["total_items"] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read = reader._connect()
            try:
                read.execute("DELETE FROM items")
            finally:
                read.close()


def test_stats_scope_collections_and_tags_to_live_user_items(tmp_path):
    db = build_zotero_db(tmp_path / "zotero")
    user = add_library_item(db, item_key="PARENT", title="User")
    feed = add_feed_item(db, feed_library_id=2, guid="feed", title="Feed")
    trashed = add_library_item(db, item_key="TRASHED", title="Trashed")
    for item, tag in [(user, "user-tag"), (feed, "feed-tag"), (trashed, "trash-tag")]:
        add_tag_to_item(db, item_id=item, tag_name=tag)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO deletedItems(itemID) VALUES (?)", (trashed,))
        conn.execute("INSERT INTO collections(collectionID, collectionName, libraryID, key) VALUES (1, 'Foreign', 2, 'FOREIGN')")
    assert ZoteroReader(db.parent).get_library_stats() == {
        "total_items": 1, "total_collections": 2, "total_tags": 1, "items_with_pdf": 0,
    }


def test_deleted_children_are_hidden_from_every_read_surface(tmp_path):
    db = build_zotero_db(tmp_path / "zotero")
    parent = add_library_item(db, item_key="PARENT", title="Parent")
    note = add_library_item(db, item_key="NOTE", title="Note", item_type="note")
    pdf = add_library_item(db, item_key="PDF", title="PDF", item_type="attachment")
    ann = add_library_item(db, item_key="ANN", title="Annotation", item_type="annotation")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO itemNotes(itemID, parentItemID, note) VALUES (?, ?, 'visible')", (note, parent))
        conn.execute("INSERT INTO itemAttachments(itemID, parentItemID, path, contentType) VALUES (?, ?, 'storage:paper.pdf', 'application/pdf')", (pdf, parent))
        conn.execute("INSERT INTO itemAnnotations(itemID, parentItemID, type, text) VALUES (?, ?, 1, 'quote')", (ann, pdf))
    reader = ZoteroReader(db.parent)
    assert reader.get_item_detail("PARENT")["has_pdf"]
    assert len(reader.get_item_notes("PARENT")) == 1
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO deletedItems(itemID) VALUES (?)", [(note,), (ann,)])
    detail = reader.get_item_detail("PARENT")
    assert detail["notes"] == reader.get_item_notes("PARENT") == []
    assert detail["annotations"] == []
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM deletedItems WHERE itemID = ?", (ann,))
        conn.execute("INSERT INTO deletedItems(itemID) VALUES (?)", (pdf,))
    detail = reader.get_item_detail("PARENT")
    assert detail["annotations"] == detail["attachments"] == []
    assert not detail["has_pdf"]
    assert not reader.get_items()["items"][0]["has_pdf"]
    assert not reader.get_all_items()["items"][0]["has_pdf"]
    assert reader.get_library_stats()["items_with_pdf"] == 0


def test_collection_paths_terminate_on_cycles():
    reader = object.__new__(ZoteroReader)
    assert reader._collection_path(1, {1: {"name": "One", "parent": 2}, 2: {"name": "Two", "parent": 1}}) == "Two > One"


@pytest.mark.parametrize("payload,expected", [
    ({"collection_key": "FOREIGN"}, None),
    ({"collection_path": "Foreign"}, None),
    ({"collection_key": "FOREIGN", "collection_path": "Inbox"}, None),
    ({"collection_path": "Inbox"}, 90),
    ({"collection_path": "Research > Child"}, 100),
])
def test_collection_writes_never_resolve_foreign_collections(tmp_path, payload, expected):
    db = build_zotero_db(tmp_path / "zotero")
    parent = add_library_item(db, item_key="PARENT", title="Parent")
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO collections(collectionID, collectionName, libraryID, key, parentCollectionID) VALUES (?, ?, ?, ?, ?)", [
            (1, "Foreign", 2, "FOREIGN", None), (2, "Inbox", 2, "FEEDBOX", None),
            (3, "Research", 2, "FEEDROOT", None), (4, "Child", 2, "FEEDCHILD", 3),
            (100, "Child", 1, "USERCHILD", 91),
        ])
    result = ZoteroWriter(db.parent).apply_changes([_change("add_to_collection", payload)])
    assert bool(result["failed"]) == (expected is None)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT collectionID FROM collectionItems WHERE itemID = ?", (parent,)).fetchall() == ([] if expected is None else [(expected,)])


def test_collection_creation_and_batch_removal_use_the_user_library(tmp_path):
    db = build_zotero_db(tmp_path / "zotero")
    parent = add_library_item(db, item_key="PARENT", title="Parent")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE collections SET libraryID=2 WHERE collectionID=90")
        conn.execute("INSERT INTO collectionItems VALUES (?, 90)", (parent,))
        conn.row_factory = sqlite3.Row
        writer = ZoteroWriter(db.parent)
        user_box = writer._ensure_collection(conn, "Inbox", writer._table_columns(conn, "collections"))
        assert user_box != 90
        conn.execute("INSERT INTO collectionItems VALUES (?, ?)", (parent, user_box))
    assert writer.remove_items_from_collection(["PARENT"], "Inbox") == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT collectionID FROM collectionItems").fetchall() == [(90,)]


@pytest.mark.parametrize("library,item", [(3, "existing"), (2, "missing"), (1, "existing")])
def test_queued_feed_read_rejects_wrong_or_missing_identity(tmp_path, library, item):
    db = build_zotero_db(tmp_path / "zotero")
    feed_id = add_feed_item(db, feed_library_id=2, guid="feed", title="Feed")
    payload = {"feed_library_id": library, "feed_item_id": feed_id if item == "existing" else feed_id + 100}
    result = ZoteroWriter(db.parent).apply_changes([_change("mark_feed_item_read", payload)])
    assert result["applied_ids"] == [] and len(result["failed"]) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT readTime FROM feedItems").fetchone()[0] is None


def test_upsert_note_replaces_a_trashed_note_and_matches_marker_literally(tmp_path):
    db = build_zotero_db(tmp_path / "zotero")
    parent = add_library_item(db, item_key="PARENT", title="Parent")
    writer = ZoteroWriter(db.parent)
    distractor = _change("add_note", {"note_html": "<p>zsXabcYnote</p>"})
    assert writer.apply_changes([distractor])["applied_ids"] == [1]
    change = _change("upsert_note", {"marker": "zs_%_note", "note_html": "<p>zs_%_note</p>"})
    assert writer.apply_changes([change])["applied_ids"] == [1]
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO deletedItems(itemID) SELECT itemID FROM itemNotes WHERE instr(note, 'zs_%_note') > 0")
    assert writer.apply_changes([change])["applied_ids"] == [1]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM itemNotes WHERE parentItemID = ?", (parent,)).fetchone()[0] == 3
    assert len(ZoteroReader(db.parent).get_item_notes("PARENT")) == 2


def test_dispatch_lock_retries_the_whole_batch_without_partial_commits(tmp_path, monkeypatch):
    db = build_zotero_db(tmp_path / "zotero")
    add_library_item(db, item_key="PARENT", title="Parent")
    writer = ZoteroWriter(db.parent)
    dispatch = writer._dispatch_change
    attempts = []

    def lock_once(conn, change, cols):
        attempts.append(change["id"])
        if attempts == [1, 2]:
            raise sqlite3.OperationalError("database is locked")
        dispatch(conn, change, cols)

    monkeypatch.setattr(writer, "_dispatch_change", lock_once)
    monkeypatch.setattr("zotero_summarizer.integrations.zotero_write.time.sleep", lambda _: None)
    result = writer.apply_changes([
        _change("add_note", {"note_html": "<p>note</p>"}),
        _change("tag_changes", {"add_tags": ["tag"]}, 2),
    ])
    assert result["applied_ids"] == [1, 2] and result["failed"] == []
    assert attempts == [1, 2, 1, 2]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM itemNotes").fetchone()[0] == 1


def test_pending_note_retry_after_status_failure_does_not_duplicate(tmp_path, monkeypatch):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.models import PendingChangeApplyRequest
    from zotero_summarizer.services import corpus
    from zotero_summarizer.services.zotero import pending, pending_confirmation, zotero
    from zotero_summarizer.storage import repositories

    db = build_zotero_db(tmp_path / "zotero")
    add_library_item(db, item_key="PARENT", title="Parent")
    writer = ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)

    async def refresh(_keys):
        return None

    def fail_status(*_args):
        raise sqlite3.OperationalError("local status write failed")

    monkeypatch.setattr(corpus, "refresh_corpus_items_by_keys", refresh)
    with repositories.with_db_path(tmp_path / "triage.db"):
        repositories.init_db()
        repositories.insert_pending_changes("PARENT", "Parent", [{"change_type": "add_note", "payload": {"note_html": "<p>Deliver once</p>"}}])
        row, = asyncio.run(pending.list_pending_changes()).items
        req = PendingChangeApplyRequest(change_ids=[row["id"]], previews={
            row["id"]: row["approval_preview"]["preview_sha256"],
        })
        dispatched = []
        dispatch = writer._dispatch_change

        def record(conn, change, cols):
            dispatched.append(change["id"])
            dispatch(conn, change, cols)

        monkeypatch.setattr(writer, "_dispatch_change", record)
        with monkeypatch.context() as patch:
            patch.setattr(pending_confirmation, "_finalize", fail_status)
            with pytest.raises(sqlite3.OperationalError, match="status write failed"):
                asyncio.run(pending.apply_pending_changes(req))
        assert repositories.get_pending_changes_by_ids([row["id"]])[0]["status"] == "applying"
        with pytest.raises(APIError) as error:
            asyncio.run(pending.apply_pending_changes(req))
        assert error.value.status_code == 409
        assert dispatched == [row["id"]]
        assert len(list(db.parent.glob(writer._BACKUP_GLOB))) == 1
    assert len(ZoteroReader(db.parent).get_item_notes("PARENT")) == 1


@pytest.mark.parametrize("operation,args", [
    ("mirror_verdict", ("must_read", "Comment")),
    ("mirror_review_note", ("My note",)),
    ("mirror_verdict", ("must_read", "")),
    ("stage_current_digest", ()),
])
def test_explicit_mirrors_and_confirmed_digest_require_backup_before_dispatch(tmp_path, monkeypatch, operation, args, request):
    from zotero_summarizer.services.zotero import generated_notes, mirror_notes, zotero
    from zotero_summarizer.storage import repositories

    published = request.getfixturevalue("boundary") if operation == "stage_current_digest" else None
    db = build_zotero_db(tmp_path / "Zotero #1")
    add_library_item(db, item_key="PARENT", title="Parent")
    writer = ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", lambda: ZoteroReader(db.parent))
    dispatch = writer._dispatch_change
    dispatched = []
    app_path = tmp_path / "triage.db"

    def assert_backup(conn, change, cols):
        dispatched.append(change["item_key"])
        backups = list(db.parent.glob(writer._BACKUP_GLOB))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("SELECT COUNT(*) FROM itemNotes").fetchone()[0] == 0
            assert backup.execute("SELECT COUNT(*) FROM itemTags").fetchone()[0] == 0
        if operation in {"mirror_review_note", "mirror_verdict"}:
            with sqlite3.connect(app_path) as app:
                claims = app.execute("SELECT id,status,audit_json FROM pending_changes").fetchall()
            claim, = [row for row in claims if row[0] == change["id"]]
            assert type(claim[0]) is int and claim[0] > 0
            assert claim[1] == "applying"
            assert [event["event"] for event in json.loads(claim[2])] == ["mirror_requested"]
            assert json.loads(claim[2])[0]["source"] == operation.replace("mirror_review_note", "mirror_user_note")
        dispatch(conn, change, cols)

    monkeypatch.setattr(writer, "_dispatch_change", assert_backup)
    if operation == "mirror_review_note":
        with repositories.with_db_path(app_path):
            repositories.init_db()
            repositories.upsert_review_note(app_path, "PARENT", args[0])
            result = mirror_notes.mirror_review_note(app_path, "PARENT")
        assert result["note_written"] is True and result["note_pending"] is False
        assert result["note_error"] is None and result["target_key"] == "PARENT"
        assert dispatched == ["PARENT"]
        with sqlite3.connect(db) as native:
            assert native.execute("SELECT note FROM itemNotes").fetchall() == [(
                '<div class="zotero-note znv1"><!-- zs:note_type=user_note;version=1 -->'
                "<h2>📝 My notes</h2><p>My note</p>"
                "<p><em>zs:note_type=user_note;version=1</em></p></div>",
            )]
        with sqlite3.connect(app_path) as app:
            status, audit = app.execute("SELECT status,audit_json FROM pending_changes").fetchone()
        assert status == "applied"
        assert [event["event"] for event in json.loads(audit)] == [
            "mirror_requested", "mirror_applied", "mirror_acknowledged",
        ]
    elif operation == "mirror_verdict":
        from zotero_summarizer.services.golden.verdict_effects import mirror_current_verdict
        with repositories.with_db_path(app_path):
            repositories.init_db()
            repositories.insert_or_update_label_verdict(app_path, item_key="PARENT",
                original_derived_priority="unknown", user_priority=args[0], comment=args[1])
            result = mirror_current_verdict(app_path, "PARENT")
        assert result == {"label_written": True, "note_written": bool(args[1])}
        assert dispatched == ["PARENT"] * (2 if args[1] else 1)
    else:
        from zotero_summarizer.services.library import _review_cache
        from zotero_summarizer.services.zotero import pending
        from zotero_summarizer.models import PendingChangeApplyRequest

        _review_cache._write_one("PARENT", published.entry)
        generated_notes.stage_current_digest("PARENT")
        assert dispatched == [] and not list(db.parent.glob(writer._BACKUP_GLOB))
        proposal, = asyncio.run(pending.list_pending_changes(item_key="PARENT")).items
        result = asyncio.run(pending.apply_pending_changes(PendingChangeApplyRequest(
            change_ids=[proposal["id"]], previews={proposal["id"]: proposal["approval_preview"]["preview_sha256"]},
        )))
        assert result["applied"] == 1 and dispatched == ["PARENT"]


def test_partial_local_finalization_rolls_back_all_claims_without_native_redelivery(boundary, monkeypatch):
    from zotero_summarizer.api.errors import APIError
    from zotero_summarizer.models import PendingChangeApplyRequest
    from zotero_summarizer.services.zotero import pending
    from zotero_summarizer.storage import pending_actions, repositories

    b = boundary
    repositories.insert_pending_changes(b.key, "Owned paper", [{
        "change_type": "tag_changes", "payload": {"add_tags": ["finalize-once"]},
    }])
    rows = sorted(asyncio.run(pending.list_pending_changes()).items, key=lambda row: row["id"])
    req = PendingChangeApplyRequest(change_ids=[row["id"] for row in rows], previews={
        row["id"]: row["approval_preview"]["preview_sha256"] for row in rows})
    original, completed = pending_actions.record, []

    def fail_second(conn, change_id, event, **kwargs):
        if event["event"] == "applied":
            completed.append(change_id)
            if len(completed) == 2:
                assert conn.execute("SELECT status FROM pending_changes WHERE id=?", (completed[0],)).fetchone()[0] == "applied"
                raise sqlite3.OperationalError("Owned second finalization failure")
        original(conn, change_id, event, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pending_actions, "record", fail_second)
        with pytest.raises(sqlite3.OperationalError, match="second finalization"):
            asyncio.run(pending.apply_pending_changes(req))
    assert completed == req.change_ids
    saved = repositories.get_pending_changes_by_ids(req.change_ids)
    assert all(row["status"] == "applying" and row["audit"][-1]["event"] == "approved" for row in saved)
    with pytest.raises(APIError) as error:
        asyncio.run(pending.apply_pending_changes(req))
    assert error.value.status_code == 409
    assert len(ZoteroReader(b.db.parent).get_item_notes(b.key)) == 1
    assert len(list(b.db.parent.glob(b.writer._BACKUP_GLOB))) == 1
    with sqlite3.connect(b.db) as conn:
        assert conn.execute("SELECT name FROM tags").fetchall() == [("finalize-once",)]


def test_unexpected_dispatch_error_rolls_back_and_propagates(tmp_path, monkeypatch):
    db = build_zotero_db(tmp_path / "zotero")
    add_library_item(db, item_key="PARENT", title="Parent")
    writer = ZoteroWriter(db.parent)
    dispatch = writer._dispatch_change

    def fail(conn, change, cols):
        if change["id"] == 2:
            raise RuntimeError("dispatch bug")
        dispatch(conn, change, cols)

    monkeypatch.setattr(writer, "_dispatch_change", fail)
    with pytest.raises(RuntimeError, match="dispatch bug"):
        writer.apply_changes([_change("add_note", {"note_html": "note"}), _change("tag_changes", {}, 2)])
    assert ZoteroReader(db.parent).get_item_notes("PARENT") == []


@pytest.mark.parametrize("operation,args,kwargs", [
    ("apply_changes", ([_change("add_note", {"note_html": "note"})],), {}),
    ("mark_feed_items_read", ([1],), {}),
    ("remove_items_from_collection", (["PARENT"], "Inbox"), {}),
    ("apply_feed_materialization", (), dict(new_item_key="NEW", feed_payload={"title": "New"}, inbox_collection_name="Inbox", tags=[], note_title="", note_html="")),
])
def test_journal_configuration_failure_is_not_swallowed(tmp_path, monkeypatch, operation, args, kwargs):
    db = build_zotero_db(tmp_path / "zotero")
    add_library_item(db, item_key="PARENT", title="Parent")
    writer = ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(writer, "backup_database", lambda: None)
    connect = sqlite3.connect

    def deny_journal(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_authorizer(lambda action, arg, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA and arg == "journal_mode" else sqlite3.SQLITE_OK)
        return conn

    monkeypatch.setattr(sqlite3, "connect", deny_journal)
    with pytest.raises((sqlite3.DatabaseError, ZoteroWriteError), match="not authorized"):
        getattr(writer, operation)(*args, **kwargs)
    assert ZoteroReader(db.parent).get_item_notes("PARENT") == []
