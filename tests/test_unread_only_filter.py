"""Phase 1.5: ZoteroReader.get_feed_items(unread_only=True) filters by readTime."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import add_feed_item, build_zotero_db, set_feed_item_read
from zotero_summarizer.integrations.zotero_read import ZoteroReader


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


def test_unread_only_default_returns_all(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="g1", title="Unread paper")
    b = add_feed_item(db, feed_library_id=2, guid="g2", title="Read paper")
    set_feed_item_read(db, feed_item_id=b)

    r = ZoteroReader(zotero_dir)
    all_items = r.get_feed_items()
    assert {it["item_id"] for it in all_items} == {a, b}


def test_unread_only_skips_already_read(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    unread = add_feed_item(db, feed_library_id=2, guid="g1", title="Unread paper")
    read = add_feed_item(db, feed_library_id=2, guid="g2", title="Read paper")
    set_feed_item_read(db, feed_item_id=read)

    r = ZoteroReader(zotero_dir)
    items = r.get_feed_items(unread_only=True)
    assert len(items) == 1
    assert items[0]["item_id"] == unread
    assert items[0]["read_time"] is None


def test_unread_only_combines_with_feed_library_filter(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    a_unread = add_feed_item(db, feed_library_id=2, guid="g1", title="A unread")
    a_read = add_feed_item(db, feed_library_id=2, guid="g2", title="A read")
    b_unread = add_feed_item(db, feed_library_id=3, guid="g3", title="B unread")
    set_feed_item_read(db, feed_item_id=a_read)

    r = ZoteroReader(zotero_dir)
    a_items = r.get_feed_items(feed_library_id=2, unread_only=True)
    assert [it["item_id"] for it in a_items] == [a_unread]
    b_items = r.get_feed_items(feed_library_id=3, unread_only=True)
    assert [it["item_id"] for it in b_items] == [b_unread]


def test_order_oldest_first(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    older = add_feed_item(db, feed_library_id=2, guid="g1", title="Older", date_added="2026-05-01 10:00:00")
    newer = add_feed_item(db, feed_library_id=2, guid="g2", title="Newer", date_added="2026-05-12 10:00:00")

    r = ZoteroReader(zotero_dir)
    items_old = r.get_feed_items(feed_library_id=2, order="oldest_first")
    assert [it["item_id"] for it in items_old] == [older, newer]
    items_new = r.get_feed_items(feed_library_id=2, order="newest_first")
    assert [it["item_id"] for it in items_new] == [newer, older]


def test_unread_only_empty_when_all_read(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="g1", title="x")
    set_feed_item_read(db, feed_item_id=a)
    r = ZoteroReader(zotero_dir)
    assert r.get_feed_items(unread_only=True) == []
