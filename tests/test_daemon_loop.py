"""Daemon round-robin selection remains independent of feed size/read status."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import add_feed_item, build_zotero_db, set_feed_item_read
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services.triage.feeds import _pick_unread_batch_round_robin


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


def test_round_robin_pulls_from_multiple_feeds(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    for i in range(5):
        add_feed_item(db, feed_library_id=2, guid=f"A{i}", title=f"A{i}")
    add_feed_item(db, feed_library_id=3, guid="B1", title="B1")
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=3, feed_library_ids=[2, 3])
    assert {it["feed_library_id"] for it in batch} == {2, 3}
    assert len(batch) == 3


def test_round_robin_handles_one_empty_feed(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    add_feed_item(db, feed_library_id=2, guid="A1", title="A1")
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=5, feed_library_ids=[2, 3])
    assert len(batch) == 1
    assert batch[0]["feed_library_id"] == 2


def test_round_robin_skips_already_read(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="A1", title="A1")
    b = add_feed_item(db, feed_library_id=2, guid="A2", title="A2")
    set_feed_item_read(db, feed_item_id=a)
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=5, feed_library_ids=[2])
    assert {it["item_id"] for it in batch} == {b}


def test_round_robin_respects_batch_size(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    for i in range(20):
        add_feed_item(db, feed_library_id=2, guid=f"A{i}", title=f"A{i}")
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=5, feed_library_ids=[2])
    assert len(batch) == 5
