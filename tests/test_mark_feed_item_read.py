"""Phase 1.5: ZoteroWriter writes feedItems.readTime in Zotero-compatible format."""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests._zotero_fixtures import add_feed_item, build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


def _read_time_for(db: Path, item_id: int) -> str | None:
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT readTime FROM feedItems WHERE itemID=?", (item_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _assert_backup_prestate(backup, feed_rows, clocks):
    assert backup.is_file()
    with closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("SELECT itemID,readTime FROM feedItems ORDER BY itemID").fetchall() == feed_rows
        assert conn.execute(
            "SELECT itemID,dateModified,clientDateModified,version,synced FROM items ORDER BY itemID"
        ).fetchall() == clocks


def test_mark_feed_items_read_writes_timestamp(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="g1", title="A")
    b = add_feed_item(db, feed_library_id=2, guid="g2", title="B")

    writer = ZoteroWriter(zotero_dir)
    rows = writer.mark_feed_items_read([a, b])
    assert rows == 2
    assert _read_time_for(db, a) is not None
    assert _read_time_for(db, b) is not None


def test_mark_feed_items_read_format_matches_zotero_convention(zotero_dir: Path):
    """readTime must be `YYYY-MM-DD HH:MM:SS` UTC (matches what Zotero's client writes)."""
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="g1", title="A")
    writer = ZoteroWriter(zotero_dir)
    writer.mark_feed_items_read([a])
    ts = _read_time_for(db, a)
    assert ts is not None
    # 19-char `YYYY-MM-DD HH:MM:SS` (no Z suffix, no fractional seconds).
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", ts), f"format mismatch: {ts!r}"


def test_mark_feed_items_read_idempotent_does_not_clobber(zotero_dir: Path):
    """Already-read items should NOT have their readTime overwritten."""
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="g1", title="A")
    # Set existing readTime via fixture helper.
    from tests._zotero_fixtures import set_feed_item_read

    set_feed_item_read(db, feed_item_id=a, read_time="2020-01-01 12:00:00")
    pre = _read_time_for(db, a)

    writer = ZoteroWriter(zotero_dir)
    rows_updated = writer.mark_feed_items_read([a])
    assert rows_updated == 0  # WHERE readTime IS NULL skipped this row
    assert _read_time_for(db, a) == pre


def test_mark_feed_items_read_empty_list_is_noop(zotero_dir: Path):
    writer = ZoteroWriter(zotero_dir)
    assert writer.mark_feed_items_read([]) == 0


def test_mark_feed_item_read_change_type_via_apply_changes(zotero_dir: Path):
    """The change-type dispatcher must also support mark_feed_item_read."""
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="g1", title="A")
    writer = ZoteroWriter(zotero_dir)
    result = writer.apply_changes(
        [
            {
                "id": 1,
                "item_key": "__feed_marker__",  # placeholder; not used for this type
                "change_type": "mark_feed_item_read",
                "payload_json": {"feed_library_id": 2, "feed_item_id": a},
            }
        ],
        create_backup=False,
    )
    assert result["applied_ids"] == [1]
    assert _read_time_for(db, a) is not None


def test_mark_feed_item_read_change_type_rejects_invalid_payload(zotero_dir: Path):
    writer = ZoteroWriter(zotero_dir)
    result = writer.apply_changes(
        [
            {
                "id": 2,
                "item_key": "ignored",
                "change_type": "mark_feed_item_read",
                "payload_json": {"feed_library_id": 0, "feed_item_id": 0},
            }
        ],
        create_backup=False,
    )
    assert result["applied_ids"] == []
    assert len(result["failed"]) == 1
    assert "feed_library_id" in result["failed"][0]["error"]


@pytest.mark.parametrize("mode", ["raw", "typed"])
def test_feed_read_count_contracts_on_same_mixed_inputs(zotero_dir: Path, monkeypatch, mode):
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="new-read", title="New")
    b = add_feed_item(db, feed_library_id=2, guid="already-read", title="Already")
    existing_time = "2020-01-01 12:00:00"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE feedItems SET readTime=? WHERE itemID=?", (existing_time, b))
        conn.execute("UPDATE items SET dateModified='2001-02-03 04:05:06', "
                     "clientDateModified='2001-02-03 04:05:06', version=7, synced=1")
        conn.commit()
        before = conn.execute(
            "SELECT itemID,dateModified,clientDateModified,version,synced FROM items ORDER BY itemID"
        ).fetchall()
    writer = ZoteroWriter(zotero_dir)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    missing = max(a, b) + 1

    if mode == "raw":
        result = writer.mark_feed_items_read([a, b, missing])
    else:
        result = writer.apply_changes([
            {"id": row_id, "item_key": "adapter-only", "change_type": "mark_feed_item_read",
             "payload_json": {"feed_library_id": 2, "feed_item_id": item_id}}
            for row_id, item_id in enumerate([a, b, missing], start=1)
        ])

    with closing(sqlite3.connect(db)) as conn:
        feed_rows = conn.execute("SELECT itemID,readTime FROM feedItems ORDER BY itemID").fetchall()
        assert [row[0] for row in feed_rows] == [a, b]
        assert feed_rows[0][1] is not None
        assert feed_rows[1] == (b, existing_time)
        assert conn.execute(
            "SELECT itemID,dateModified,clientDateModified,version,synced FROM items ORDER BY itemID"
        ).fetchall() == before
    if mode == "raw":
        assert result == 1
        backup, = zotero_dir.glob("zotero.sqlite.backup_*")
        _assert_backup_prestate(backup, [(a, None), (b, existing_time)], before)
    else:
        assert result["applied_ids"] == [1, 2]
        assert result["failed"] == [{
            "id": 3, "error": "Feed item does not belong to the supplied feed library", "expected_io": False,
        }]
        assert result["failed"][0]["expected_io"] is False
        backup = Path(result["backup_path"])
        assert backup.is_file() and backup.is_relative_to(zotero_dir)
        _assert_backup_prestate(backup, [(a, None), (b, existing_time)], before)


@pytest.mark.parametrize("mode", ["raw", "typed"])
def test_feed_read_trigger_abort_has_distinct_batch_atomicity(zotero_dir: Path, monkeypatch, mode):
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="accepted-row", title="Accepted")
    b = add_feed_item(db, feed_library_id=2, guid="aborted-row", title="Aborted")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "CREATE TRIGGER abort_second_read BEFORE UPDATE OF readTime ON feedItems "
            f"WHEN NEW.itemID={b} BEGIN SELECT RAISE(ABORT,'synthetic feed read abort'); END"
        )
        conn.execute("UPDATE items SET dateModified='2001-02-03 04:05:06', "
                     "clientDateModified='2001-02-03 04:05:06', version=7, synced=1")
        conn.commit()
        before = conn.execute(
            "SELECT itemID,dateModified,clientDateModified,version,synced FROM items ORDER BY itemID"
        ).fetchall()
    writer = ZoteroWriter(zotero_dir)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)

    if mode == "raw":
        with pytest.raises(ZoteroWriteError, match="synthetic feed read abort") as caught:
            writer.mark_feed_items_read([a, b])
    else:
        result = writer.apply_changes([
            {"id": row_id, "item_key": "adapter-only", "change_type": "mark_feed_item_read",
             "payload_json": {"feed_library_id": 2, "feed_item_id": item_id}}
            for row_id, item_id in enumerate([a, b], start=1)
        ])

    with closing(sqlite3.connect(db)) as conn:
        feed_rows = conn.execute("SELECT itemID,readTime FROM feedItems ORDER BY itemID").fetchall()
        assert conn.execute(
            "SELECT itemID,dateModified,clientDateModified,version,synced FROM items ORDER BY itemID"
        ).fetchall() == before
    if mode == "raw":
        assert caught.value.expected_io is False
        assert feed_rows == [(a, None), (b, None)]
        backup, = zotero_dir.glob("zotero.sqlite.backup_*")
        _assert_backup_prestate(backup, [(a, None), (b, None)], before)
    else:
        assert [row[0] for row in feed_rows] == [a, b]
        assert feed_rows[0][1] is not None
        assert feed_rows[1] == (b, None)
        assert result["applied_ids"] == [1]
        assert result["failed"] == [{"id": 2, "error": "synthetic feed read abort", "expected_io": False}]
        assert result["failed"][0]["expected_io"] is False
        backup = Path(result["backup_path"])
        assert backup.is_file() and backup.is_relative_to(zotero_dir)
        _assert_backup_prestate(backup, [(a, None), (b, None)], before)


def test_empty_raw_feed_read_precedes_connector_and_database(zotero_dir: Path, monkeypatch):
    writer = ZoteroWriter(zotero_dir)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Empty raw batch attempted connector or database I/O")

    monkeypatch.setattr(writer, "is_connector_running", forbidden)
    monkeypatch.setattr("zotero_summarizer.integrations._zotero_write_feed.sqlite3.connect", forbidden)

    assert writer.mark_feed_items_read([]) == 0
    assert not list(zotero_dir.glob("zotero.sqlite.backup_*"))
