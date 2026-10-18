"""Real temporary native SQL; no live Zotero, connector or historical launch controller."""
import sqlite3

import pytest

from tests._zotero_fixtures import add_feed_item, build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError


@pytest.fixture
def owned(tmp_path, monkeypatch):
    db = build_zotero_db(tmp_path / "owned-native")
    ids = [add_feed_item(db, feed_library_id=2, guid=str(i), title=str(i)) for i in range(3)]
    writer = ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    return writer, ids


def states(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT itemID,readTime FROM feedItems ORDER BY itemID").fetchall()


def test_backup_is_complete_and_contains_exact_selected_and_unselected_prestate(owned):
    writer, ids = owned
    before = states(writer.db_path)

    assert writer.mark_feed_items_read(ids[:2]) == 2

    backups = list(writer.data_dir.glob(writer._BACKUP_GLOB))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert states(backups[0]) == before
    after = dict(states(writer.db_path))
    assert after[ids[0]] is not None and after[ids[1]] is not None
    assert after[ids[2]] is None


@pytest.mark.parametrize("error", [OSError, sqlite3.DatabaseError, sqlite3.OperationalError])
def test_backup_failure_prevents_raw_retry_and_all_native_effects(owned, monkeypatch, error):
    writer, ids = owned
    before = states(writer.db_path)
    calls = []

    def backup_refusal():
        calls.append("backup")
        raise error("synthetic backup refusal")

    monkeypatch.setattr(writer, "backup_database", backup_refusal)
    monkeypatch.setattr(writer, "_retry_on_lock", lambda *a, **k: calls.append("retry"))
    monkeypatch.setattr(writer, "_prune_backups", lambda: calls.append("prune"))

    with pytest.raises(OSError if error is OSError else ZoteroWriteError):
        writer.mark_feed_items_read(ids)

    assert calls == ["backup"]
    assert states(writer.db_path) == before


def test_backup_precedes_retry_owner_and_pruning_follows_native_commit(owned, monkeypatch):
    writer, ids = owned
    before = states(writer.db_path)
    events = []
    original_backup = writer.backup_database
    original_retry = writer._retry_on_lock
    original_prune = writer._prune_backups

    def backup():
        path = original_backup()
        assert states(path) == before
        events.append("backup_complete")
        return path

    def retry(fn, **kwargs):
        if kwargs["ctx"] == "mark_feed_items_read":
            assert events == ["backup_complete"]
            assert states(writer.db_path) == before
            events.append("native_retry_owner")
        return original_retry(fn, **kwargs)

    def prune():
        assert all(value is not None for _, value in states(writer.db_path))
        events.append("prune_after_commit")
        original_prune()

    monkeypatch.setattr(writer, "backup_database", backup)
    monkeypatch.setattr(writer, "_retry_on_lock", retry)
    monkeypatch.setattr(writer, "_prune_backups", prune)

    assert writer.mark_feed_items_read(ids) == len(ids)
    assert events == ["backup_complete", "native_retry_owner", "prune_after_commit"]


def test_sql_failure_preserves_prestate_and_completed_backup_without_pruning(owned, monkeypatch):
    writer, ids = owned
    with sqlite3.connect(writer.db_path) as conn:
        conn.execute("CREATE TRIGGER refuse_read BEFORE UPDATE ON feedItems "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic native refusal'); END")
    before = states(writer.db_path)
    pruned = []
    monkeypatch.setattr(writer, "_prune_backups", lambda: pruned.append(True))

    with pytest.raises(ZoteroWriteError):
        writer.mark_feed_items_read(ids)

    assert states(writer.db_path) == before
    backups = list(writer.data_dir.glob(writer._BACKUP_GLOB))
    assert len(backups) == 1 and states(backups[0]) == before
    assert pruned == []


def test_pruning_error_does_not_replay_a_committed_update(owned, monkeypatch):
    writer, ids = owned
    calls = []
    original_retry = writer._retry_on_lock

    def retry(fn, **kwargs):
        calls.append(kwargs["ctx"])
        return original_retry(fn, **kwargs)

    def fail_prune():
        raise OSError("synthetic retention failure")

    monkeypatch.setattr(writer, "_retry_on_lock", retry)
    monkeypatch.setattr(writer, "_prune_backups", fail_prune)

    with pytest.raises(OSError, match="synthetic retention failure"):
        writer.mark_feed_items_read(ids)

    assert all(value is not None for _, value in states(writer.db_path))
    assert calls == ["backup", "mark_feed_items_read"]
    assert len(list(writer.data_dir.glob(writer._BACKUP_GLOB))) == 1


def test_empty_selection_does_not_probe_backup_or_retry(owned, monkeypatch):
    writer, _ = owned
    before = states(writer.db_path)
    calls = []
    for name in ("is_connector_running", "backup_database", "_retry_on_lock", "_prune_backups"):
        monkeypatch.setattr(writer, name, lambda *args, **kwargs: calls.append(True))

    assert writer.mark_feed_items_read([]) == 0
    assert calls == []
    assert states(writer.db_path) == before


def test_backup_includes_committed_wal_resident_prestate(owned):
    writer, ids = owned
    keeper = sqlite3.connect(writer.db_path)
    try:
        assert keeper.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("UPDATE feedItems SET readTime='2020-01-01 12:00:00' WHERE itemID=?", (ids[0],))
        keeper.commit()
        before = states(writer.db_path)
        assert writer.db_path.with_name("zotero.sqlite-wal").stat().st_size > 0
        immutable = sqlite3.connect(writer.db_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            assert immutable.execute("SELECT readTime FROM feedItems WHERE itemID=?", (ids[0],)).fetchone() == (None,)
        finally:
            immutable.close()

        assert writer.mark_feed_items_read(ids) == len(ids) - 1

        backups = list(writer.data_dir.glob(writer._BACKUP_GLOB))
        assert len(backups) == 1
        assert states(backups[0]) == before
        assert dict(states(writer.db_path))[ids[0]] == "2020-01-01 12:00:00"
    finally:
        keeper.close()
