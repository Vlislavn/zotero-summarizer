"""Regression tests for the Zotero read-sync reconciler + feed-refresh rotation.

Both guard against the 2026-07 app-RSS-migration outage: (a) app-read items
never cleared Zotero's unread badge (`sync_zotero_read_state` did not exist —
`zotero_ids` was structurally empty in `mark_processed_read`); (b) the RSS
refresh sliced the alphabetically-first ``max_feeds`` forever, starving 31 of
41 enabled feeds.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._zotero_fixtures import add_feed, add_feed_item, build_zotero_db
from zotero_summarizer.integrations.app_rss import AppRssReader
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import rss as rss_storage
from zotero_summarizer.services.triage.feeds import _common
from zotero_summarizer.services.triage.feeds._zotero_readsync import sync_zotero_read_state


def _app_db_with_read_items(tmp_path: Path, guids_read: list[str], guids_unread: list[str]) -> Path:
    db_path = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    feeds_storage.init_feeds_schema(conn)
    feed_id = rss_storage.upsert_rss_feed(conn, name="F", url="http://example.com/f.xml")
    for i, guid in enumerate([*guids_read, *guids_unread]):
        item_id, _ = rss_storage.upsert_rss_item(
            conn,
            rss_feed_id=feed_id,
            item={"guid": guid, "title": f"t{i}", "stable_feed_key": f"k{i}", "url": guid},
        )
        if guid in guids_read:
            rss_storage.mark_rss_items_read(conn, [item_id])
    conn.commit()
    conn.close()
    return db_path


class _RecordingWriter:
    def __init__(self, fail: bool = False):
        self.calls: list[list[int]] = []
        self.fail = fail

    def mark_feed_items_read(self, ids: list[int]) -> int:
        if self.fail:
            raise ZoteroWriteError("database is locked")
        self.calls.append(list(ids))
        return len(ids)


@pytest.fixture
def patched_settings(tmp_path: Path, monkeypatch):
    db_path = _app_db_with_read_items(
        tmp_path,
        guids_read=["http://arxiv.org/abs/1", "http://arxiv.org/abs/2"],
        guids_unread=["http://arxiv.org/abs/3"],
    )
    monkeypatch.setattr(_common, "get_settings", lambda: SimpleNamespace(triage_db_path=db_path))
    return db_path


def _zotero_with_unread(tmp_path: Path, guids: list[str]) -> ZoteroReader:
    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=2, name="ZF")
    for i, guid in enumerate(guids):
        add_feed_item(db, feed_library_id=2, item_id=100 + i, guid=guid)
    return ZoteroReader(db.parent)


def test_sync_marks_only_app_read_matches(tmp_path: Path, patched_settings):
    """Zotero-unread ∩ app-read is marked; app-unread and Zotero-only guids are not."""
    reader = _zotero_with_unread(
        tmp_path,
        ["http://arxiv.org/abs/1", "http://arxiv.org/abs/3", "http://arxiv.org/abs/zotero-only"],
    )
    writer = _RecordingWriter()
    marked = sync_zotero_read_state(zotero_reader=reader, writer=writer, tick_id="t")
    assert marked == 1
    assert writer.calls == [[100]]  # itemID of abs/1 (the only app-READ match)


def test_sync_skips_when_zotero_absent(patched_settings):
    writer = _RecordingWriter()
    assert sync_zotero_read_state(zotero_reader=None, writer=writer, tick_id="t") == 0
    assert sync_zotero_read_state(zotero_reader=object(), writer=None, tick_id="t") == 0
    assert writer.calls == []


def test_sync_lock_failure_returns_zero_not_raise(tmp_path: Path, patched_settings):
    """DB-locked posture mirrors mark_processed_read: warn + retry next tick."""
    reader = _zotero_with_unread(tmp_path, ["http://arxiv.org/abs/1"])
    assert sync_zotero_read_state(zotero_reader=reader, writer=_RecordingWriter(fail=True), tick_id="t") == 0


def test_unread_guid_map_excludes_read(tmp_path: Path):
    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=2, name="ZF")
    add_feed_item(db, feed_library_id=2, item_id=100, guid="g-unread")
    add_feed_item(db, feed_library_id=2, item_id=101, guid="g-read")
    from tests._zotero_fixtures import set_feed_item_read

    set_feed_item_read(db, feed_item_id=101)
    got = ZoteroReader(db.parent).get_unread_feed_guid_map()
    assert got == {"g-unread": 100}


def test_refresh_rotates_least_recently_fetched_first(tmp_path: Path, monkeypatch):
    """A bounded pass must pick the never/oldest-fetched feed, not the alphabetical head."""
    db_path = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    feeds_storage.init_feeds_schema(conn)
    a = rss_storage.upsert_rss_feed(conn, name="AAA fetched recently", url="http://example.com/a.xml")
    b = rss_storage.upsert_rss_feed(conn, name="ZZZ never fetched", url="http://example.com/z.xml")
    conn.execute("UPDATE rss_feeds SET last_fetched_at = datetime('now') WHERE id = ?", (a,))
    conn.commit()
    conn.close()

    fetched_urls: list[str] = []

    def _fake_fetch(url: str, *, timeout: float):
        fetched_urls.append(url)
        return "<rss><channel><title>t</title></channel></rss>", {}

    monkeypatch.setattr("zotero_summarizer.integrations.app_rss._fetch_public_url", _fake_fetch)
    AppRssReader(db_path).refresh_feeds(max_feeds=1)
    assert fetched_urls == ["http://example.com/z.xml"], (
        f"expected never-fetched feed first, got {fetched_urls} (id a={a}, b={b})"
    )
