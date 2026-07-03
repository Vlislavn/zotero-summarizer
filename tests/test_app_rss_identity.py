from __future__ import annotations

import sqlite3

import httpx
import pytest

from zotero_summarizer.integrations import app_rss
from zotero_summarizer.integrations.app_rss import AppRssReader, RssUrlRejected, validate_rss_url
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
from zotero_summarizer.storage import feeds as fs
from zotero_summarizer.storage import repositories as repo
from zotero_summarizer.storage import rss as rss_storage
from zotero_summarizer.storage.feed_identity import (
    is_legacy_feed_key,
    is_stable_feed_key,
    stable_feed_key_from_item,
)


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    fs.init_feeds_schema(conn)
    return conn


def _item(feed_library_id: int, item_id: int, guid: str, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "feed_library_id": feed_library_id,
        "item_id": item_id,
        "guid": guid,
        "title": f"Paper {item_id}",
        "doi": "",
        "arxiv_id": "",
        "feed_name": "Example",
    }
    base.update(extra)
    return base


def test_zotero_and_app_rss_items_with_same_guid_share_stable_key() -> None:
    zotero_item = _item(10, 111, "https://example.com/papers/1?utm_source=zotero")
    app_item = _item(
        2,
        222,
        "https://EXAMPLE.com/papers/1?utm_source=rss",
        source_type="app_rss",
    )

    zotero_key = stable_feed_key_from_item(zotero_item)
    app_key = stable_feed_key_from_item(app_item)

    assert zotero_key == app_key
    assert is_stable_feed_key(zotero_key)
    assert not is_legacy_feed_key(zotero_key)


def test_historical_processed_row_suppresses_matching_app_rss_arrival() -> None:
    conn = _open()
    fs.record_decision(
        conn,
        run_id="r1",
        feed_item=_item(10, 111, "shared-guid"),
        decision=fs.DECISION_SELECTED,
    )
    conn.commit()

    app_copy = _item(2, 222, "shared-guid", source_type="app_rss")
    unprocessed, skipped = fs.filter_unprocessed(conn, [app_copy])

    assert unprocessed == []
    assert skipped == 1
    assert fs.select_stale_unread_to_mark(conn, [app_copy]) == [(2, 222)]


def test_legacy_feed_alias_copies_labels_and_resolves_lookup(tmp_path) -> None:
    db_path = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        repo.apply_schema(conn)
        fs.record_decision(
            conn,
            run_id="r1",
            feed_item=_item(10, 4242, "legacy-shared-guid"),
            decision=fs.DECISION_SELECTED,
        )
        conn.commit()
    finally:
        conn.close()

    repo.insert_or_update_label_verdict(
        db_path,
        item_key="feed:4242",
        original_derived_priority="could_read",
        user_priority="must_read",
        comment="legacy",
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        fs.init_feeds_schema(conn)
        conn.commit()
        row = conn.execute(
            "SELECT stable_feed_key FROM feed_key_aliases WHERE old_key = 'feed:4242'"
        ).fetchone()
        assert row is not None
        stable_key = str(row["stable_feed_key"])
        copied = conn.execute(
            "SELECT user_priority FROM label_verdicts WHERE item_key = ?",
            (stable_key,),
        ).fetchone()
        assert copied is not None
        assert copied["user_priority"] == "must_read"
    finally:
        conn.close()

    verdict = repo.get_label_verdict(db_path, "feed:4242")
    assert verdict is not None
    assert verdict["item_key"] == stable_key
    assert verdict["user_priority"] == "must_read"


def test_ambiguous_legacy_alias_is_reported_not_resolved() -> None:
    conn = _open()
    fs.record_decision(
        conn,
        run_id="r1",
        feed_item=_item(1, 42, "guid-one"),
        decision=fs.DECISION_SELECTED,
    )
    fs.record_decision(
        conn,
        run_id="r1",
        feed_item=_item(2, 42, "guid-two"),
        decision=fs.DECISION_SELECTED,
    )
    conn.commit()

    fs.init_feeds_schema(conn)

    report = fs.feed_key_alias_validation_report(conn)
    assert report["ambiguous_count"] == 1
    assert report["ambiguous"][0]["old_key"] == "feed:42"
    assert fs.get_processed_feed_item_by_id(conn, 42) is None


def test_app_rss_reader_refresh_stores_items_without_scoring(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        fs.init_feeds_schema(conn)
        rss_storage.upsert_rss_feed(
            conn,
            name="Example Feed",
            url="https://example.com/rss",
            enabled=True,
        )
        conn.commit()
    finally:
        conn.close()

    rss_xml = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>Stored from RSS</title>
      <guid>shared-rss-guid</guid>
      <link>https://example.com/paper</link>
      <description>Abstract text</description>
    </item>
  </channel>
</rss>
"""
    monkeypatch.setattr(
        app_rss,
        "_fetch_public_url",
        lambda url, *, timeout: (rss_xml, httpx.Headers({"etag": "abc"})),
    )

    reader = AppRssReader(db_path)
    result = reader.refresh_feeds(max_feeds=1, max_new_items_per_feed=5)

    assert result["inserted"] == 1
    assert result["errors"] == []
    items = reader.get_feed_items(unread_only=True)
    assert len(items) == 1
    assert items[0]["source_type"] == "app_rss"
    assert items[0]["title"] == "Stored from RSS"
    assert is_stable_feed_key(items[0]["stable_feed_key"])


def test_app_library_reader_lists_kept_rss_rows(tmp_path) -> None:
    db_path = tmp_path / "triage.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        fs.init_feeds_schema(conn)
        feed_id = rss_storage.upsert_rss_feed(
            conn,
            name="Example Feed",
            url="https://example.com/rss",
            enabled=True,
        )
        rss_storage.upsert_rss_item(
            conn,
            rss_feed_id=feed_id,
            item={
                "guid": "kept-guid",
                "title": "Kept RSS Paper",
                "abstract": "Useful abstract",
                "url": "https://example.com/paper",
                "doi": "10.1234/example",
                "authors": "Ada Lovelace; Grace Hopper",
            },
        )
        fs.record_decision(
            conn,
            run_id="r",
            feed_item={
                "feed_library_id": feed_id,
                "item_id": 7,
                "source_type": "app_rss",
                "guid": "kept-guid",
                "title": "Kept RSS Paper",
                "doi": "10.1234/example",
            },
            decision=fs.DECISION_USER_APPROVED,
            reading_priority="should_read",
        )
        conn.commit()
    finally:
        conn.close()

    reader = AppLibraryReader(db_path)
    page = reader.get_all_items(search="useful")

    assert page["total"] == 1
    item = page["items"][0]
    assert is_stable_feed_key(item["item_key"])
    assert item["title"] == "Kept RSS Paper"
    assert item["abstract"] == "Useful abstract"
    assert item["has_pdf"] is False

    detail = reader.get_item_detail(item["item_key"])
    assert detail is not None
    assert detail["url"] == "https://example.com/paper"
    assert detail["doi"] == "10.1234/example"
    assert detail["authors"] == ["Ada Lovelace", "Grace Hopper"]
    assert detail["has_pdf"] is False


def test_delete_rss_feed_removes_owned_items() -> None:
    conn = _open()
    feed_id = rss_storage.upsert_rss_feed(
        conn,
        name="Example",
        url="https://example.com/rss",
    )
    item_id, _inserted = rss_storage.upsert_rss_item(
        conn,
        rss_feed_id=feed_id,
        item={"guid": "owned-item", "title": "Owned Item"},
    )
    conn.commit()

    assert rss_storage.delete_rss_feed(conn, feed_id) is True
    conn.commit()

    assert conn.execute("SELECT 1 FROM rss_feeds WHERE id = ?", (feed_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM rss_items WHERE id = ?", (item_id,)).fetchone() is None


def test_validate_rss_url_rejects_local_and_private_urls(monkeypatch) -> None:
    for url in ("file:///tmp/feed.xml", "http://127.0.0.1/rss", "http://[::1]/rss"):
        with pytest.raises(RssUrlRejected):
            validate_rss_url(url)

    def fake_getaddrinfo(*args, **kwargs):
        return [(None, None, None, None, ("10.0.0.8", 443))]

    monkeypatch.setattr(app_rss.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(RssUrlRejected):
        validate_rss_url("https://example.com/rss")
