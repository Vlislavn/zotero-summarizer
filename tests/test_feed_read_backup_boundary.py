"""Proposed backup-only service invariant; neither automation nor history policy."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import stat

import pytest

from tests._direct_write_fixtures import native, sql_rows
from tests._zotero_fixtures import add_feed_item, build_zotero_db, set_feed_item_read
from tests.test_review_questions import _database
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.triage.feeds._zotero_readsync import sync_zotero_read_state
from zotero_summarizer.storage import feeds, repositories, rss

OLD_READ = "2000-01-02 03:04:05"
OLD_CLOCK = "2001-02-03 04:05:06"
PRESTATE = [(2201, None), (2202, None), (2203, None), (2204, OLD_READ)]


def _native_state(path):
    return {
        "feed_rows": sql_rows(path, "SELECT itemID,readTime FROM feedItems ORDER BY itemID"),
        "feed_identity": sql_rows(path,
            "SELECT f.itemID,f.guid,f.translatedTime,i.key,i.libraryID,l.type FROM feedItems f "
            "JOIN items i ON i.itemID=f.itemID JOIN libraries l ON l.libraryID=i.libraryID ORDER BY f.itemID"),
        "items": sql_rows(path, "SELECT itemID,itemTypeID,libraryID,dateAdded,dateModified,"
                          "clientDateModified,version,synced FROM items ORDER BY itemID"),
        "libraries": sql_rows(path, "SELECT * FROM libraries ORDER BY libraryID"),
        "other": native(path),
    }


def _app_state():
    path = repositories._resolve_db_path()
    return {
        "path": str(path),
        "rss_feeds": sql_rows(path, "SELECT * FROM rss_feeds ORDER BY id"),
        "rss_items": sql_rows(path, "SELECT * FROM rss_items ORDER BY id"),
        "history": sql_rows(path, "SELECT * FROM pending_changes ORDER BY id"),
    }


def _seed_rss(path):
    with feeds.open_triage_conn(path) as conn:
        feed_id = rss.upsert_rss_feed(conn, name="Owned read state", url="https://example.invalid/feed220")
        for name in ("matched", "app-unread", "already-read", "app-only"):
            item_id, _ = rss.upsert_rss_item(conn, rss_feed_id=feed_id, item={
                "guid": f"urn:zs:220:{name}", "stable_feed_key": f"feed:220:{name}", "title": name,
            })
            if name != "app-unread":
                rss.mark_rss_items_read(conn, [item_id])
        conn.commit()


@pytest.fixture
def read_boundary(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    db = build_zotero_db(tmp_path / "owned-native220")
    for item_id, (name, library) in enumerate((
        ("matched", 2), ("app-unread", 2), ("native-only", 3), ("already-read", 2),
    ), start=2201):
        add_feed_item(db, feed_library_id=library, item_id=item_id, guid=f"urn:zs:220:{name}", title=name)
    set_feed_item_read(db, feed_item_id=2204, read_time=OLD_READ)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE items SET dateModified=?,clientDateModified=?,version=7,synced=1",
                     (OLD_CLOCK, OLD_CLOCK))
        conn.commit()
    settings = replace(settings, zotero_data_dir=db.parent)
    context = get_context()
    monkeypatch.setattr(context, "settings", settings)
    reader, writer = ZoteroReader(db.parent), ZoteroWriter(db.parent)
    monkeypatch.setattr(context.state, "zotero_reader", reader)
    monkeypatch.setattr(context.state, "zotero_writer", writer)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    # The ContextVar override stays active through the entire test/call/observations.
    with repositories.with_db_path(settings.triage_db_path):
        _seed_rss(settings.triage_db_path)
        yield settings, db, reader, writer


def _file_identity(path):
    value = Path(path).stat(follow_symlinks=False)
    return value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns


def _backup_view(path):
    return {"path": str(path), "file": _file_identity(path), "state": _native_state(path),
            "integrity": sql_rows(path, "PRAGMA integrity_check")}


def _observe(writer, monkeypatch):
    """Observe completed real backups and each real retry callback; never preflight-assert."""
    real_backup, real_retry = writer.backup_database, writer._retry_on_lock
    backups, attempts = [], []

    def backup(*args, **kwargs):
        source_before = writer.target_identity().model_dump()
        reference = real_backup(*args, **kwargs)
        backups.append({"source_before": source_before, "source_after": writer.target_identity().model_dump(),
                        "completed": _backup_view(reference)})
        return reference

    def retry(fn, **kwargs):
        def attempt():
            entry = {"ctx": kwargs.get("ctx"), "target": writer.target_identity().model_dump(),
                     "before": _native_state(writer.db_path), "app": _app_state(),
                     "backups": [(record, _backup_view(record["completed"]["path"])) for record in backups]}
            attempts.append(entry)
            try:
                return fn()
            finally:
                entry["after"] = _native_state(writer.db_path)
        return real_retry(attempt, **kwargs)

    monkeypatch.setattr(writer, "backup_database", backup)
    monkeypatch.setattr(writer, "_retry_on_lock", retry)
    return backups, attempts


def _require_backup_for_mutating_attempts(db, attempts, diagnostic):
    mutating = [entry for entry in attempts if entry["before"] != entry["after"]]
    assert mutating, f"Native changed without an observed mutating DB attempt: {diagnostic!r}"
    for entry in mutating:
        matching = [record for record, at_entry in entry["backups"]
                    if record["source_before"] == record["source_after"] == entry["target"]
                    and entry["target"]["database_path"] == str(db.resolve(strict=True))
                    and at_entry == record["completed"] and at_entry["state"] == entry["before"]
                    and at_entry["integrity"] == [("ok",)]
                    and Path(at_entry["path"]).parent == db.parent
                    and stat.S_ISREG(at_entry["file"][2])]
        assert matching, f"Native changed before any completed matching prestate backup: {diagnostic!r}"
        for record in matching:
            assert _backup_view(record["completed"]["path"]) == record["completed"], diagnostic


def test_sync_native_effect_has_matching_prestate_backup(read_boundary, monkeypatch, record_property):
    settings, db, reader, writer = read_boundary
    before, app_before = _native_state(db), _app_state()
    assert before["feed_rows"] == PRESTATE
    assert reader.db_path.resolve(strict=True) == writer.db_path.resolve(strict=True) == db.resolve(strict=True)
    assert reader.get_unread_feed_guid_map() == {
        "urn:zs:220:matched": 2201, "urn:zs:220:app-unread": 2202, "urn:zs:220:native-only": 2203,
    }
    assert sql_rows(settings.triage_db_path, "SELECT guid,read_at IS NOT NULL FROM rss_items ORDER BY id") == [
        ("urn:zs:220:matched", 1), ("urn:zs:220:app-unread", 0),
        ("urn:zs:220:already-read", 1), ("urn:zs:220:app-only", 1),
    ]
    backups, attempts = _observe(writer, monkeypatch)
    result = sync_zotero_read_state(zotero_reader=reader, writer=writer, tick_id="owned220-backup")
    after, app_after = _native_state(db), _app_state()
    diagnostic = {"returned": result, "before": before["feed_rows"], "after": after["feed_rows"],
                  "backup_count": len(backups), "backups": backups, "attempts": attempts,
                  "app_before": app_before, "app_after": app_after}
    record_property("feed_read_backup_boundary", repr(diagnostic))
    assert app_after["path"] == app_before["path"] == str(settings.triage_db_path)
    assert (app_after["rss_feeds"], app_after["rss_items"]) == (app_before["rss_feeds"], app_before["rss_items"])
    assert [row[0] for row in after["feed_rows"]] == [2201, 2202, 2203, 2204], diagnostic
    assert after["feed_rows"][1:] == PRESTATE[1:], diagnostic
    assert [row[-4:] for row in before["items"]] == [(OLD_CLOCK, OLD_CLOCK, 7, 1)] * 4, diagnostic
    assert {key: value for key, value in after.items() if key != "feed_rows"} == {
        key: value for key, value in before.items() if key != "feed_rows"}, diagnostic
    if after != before:
        _require_backup_for_mutating_attempts(db, attempts, diagnostic)


def test_sync_without_writer_has_no_native_effect(read_boundary, monkeypatch, record_property):
    settings, db, reader, writer = read_boundary
    before, app_before = _native_state(db), _app_state()
    assert before["feed_rows"] == PRESTATE
    backups, attempts = _observe(writer, monkeypatch)
    result = sync_zotero_read_state(zotero_reader=reader, writer=None, tick_id="owned220-no-writer")
    after, app_after = _native_state(db), _app_state()
    record_property("feed_read_without_writer", repr({"returned": result, "before": before, "after": after,
                    "backups": backups, "attempts": attempts, "app_before": app_before, "app_after": app_after}))
    assert result == 0
    assert after == before and after["feed_rows"] == PRESTATE
    assert [row[-4:] for row in after["items"]] == [(OLD_CLOCK, OLD_CLOCK, 7, 1)] * 4
    assert app_after == app_before and app_after["path"] == str(settings.triage_db_path)
    assert backups == attempts == []
    assert list(db.parent.glob("zotero.sqlite.backup_*")) == []
