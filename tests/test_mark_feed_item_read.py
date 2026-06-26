"""Phase 1.5: ZoteroWriter writes feedItems.readTime in Zotero-compatible format."""
from __future__ import annotations

import re
import sqlite3
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
