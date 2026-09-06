"""Zotero read/write regressions on isolated databases (A014–A023)."""
import asyncio
import sqlite3

import pytest

from tests._zotero_fixtures import add_feed_item, add_library_item, add_tag_to_item, build_zotero_db
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.models import PaperDigest


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
    from zotero_summarizer.models import PendingChangeMutationRequest
    from zotero_summarizer.services import corpus
    from zotero_summarizer.services.zotero import pending, zotero
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
        row = repositories.get_pending_changes()[0]
        req = PendingChangeMutationRequest(change_ids=[row["id"]])
        with monkeypatch.context() as patch:
            patch.setattr(repositories, "set_pending_changes_status", fail_status)
            with pytest.raises(sqlite3.OperationalError, match="status write failed"):
                asyncio.run(pending.apply_pending_changes(req))
        assert repositories.get_pending_changes()[0]["status"] == "pending"
        assert asyncio.run(pending.apply_pending_changes(req))["applied"] == 1
        assert repositories.get_pending_changes("applied")[0]["id"] == row["id"]
    assert len(ZoteroReader(db.parent).get_item_notes("PARENT")) == 1


@pytest.mark.parametrize("operation,args", [
    ("zotero_upsert_verdict_note", ("must_read", "Comment")),
    ("zotero_upsert_user_note", ("My note",)),
    ("zotero_set_label_tag", ("must_read",)),
    ("zotero_upsert_digest_note", (PaperDigest(tldr="Digest"),)),
])
def test_every_direct_mirror_requires_a_backup_before_dispatch(tmp_path, monkeypatch, operation, args):
    from zotero_summarizer.services.zotero import zotero

    db = build_zotero_db(tmp_path / "Zotero #1")
    add_library_item(db, item_key="PARENT", title="Parent")
    writer = ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    monkeypatch.setattr(zotero, "get_zotero_reader_or_raise", lambda: ZoteroReader(db.parent))
    dispatch = writer._dispatch_change

    def assert_backup(conn, change, cols):
        backups = list(db.parent.glob(writer._BACKUP_GLOB))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("SELECT COUNT(*) FROM itemNotes").fetchone()[0] == 0
            assert backup.execute("SELECT COUNT(*) FROM itemTags").fetchone()[0] == 0
        dispatch(conn, change, cols)

    monkeypatch.setattr(writer, "_dispatch_change", assert_backup)
    getattr(zotero, operation)("PARENT", *args)


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
