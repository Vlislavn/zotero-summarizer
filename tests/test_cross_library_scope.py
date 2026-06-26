"""Cross-library scoping invariant: feed-library items never leak into the
user library's reads or writes.

Zotero stores the user's personal library (type='user') alongside ~dozens of
RSS feed libraries (type='feed') in one ``items`` table. The triage app treats
ONLY the user library as "the library" — so every whole-library read must
exclude feed items, and every write that targets an item by key must refuse a
feed item's key. A regression here previously grafted user-library PDFs onto
feed parents (cross-library 403 attachments) via the bulk full-text path.

These tests pin the invariant at the reader/writer level (not only through the
one full-text trace that surfaced it), and a control user-library item proves
the legitimate path still works.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, add_tag_to_item, build_zotero_db
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter

USER_KEY = "USERITEM1"
FEED_KEY = "FEEDITEM1"
USER_LIBRARY_ID = 1
FEED_LIBRARY_ID = 2  # type='feed' in the fixture


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    """A user-library item and a feed-library item, each with url + a tag."""
    db_path = build_zotero_db(tmp_path / "zotero")
    user_id = add_library_item(
        db_path, item_key=USER_KEY, title="User paper",
        url="https://arxiv.org/abs/2401.00001", library_id=USER_LIBRARY_ID,
    )
    feed_id = add_library_item(
        db_path, item_key=FEED_KEY, title="Feed paper",
        url="https://arxiv.org/abs/2402.00002", library_id=FEED_LIBRARY_ID,
    )
    add_tag_to_item(db_path, item_id=user_id, tag_name="user-tag")
    add_tag_to_item(db_path, item_id=feed_id, tag_name="feed-only-tag")
    return db_path.parent


@pytest.fixture
def reader(seeded: Path) -> ZoteroReader:
    return ZoteroReader(seeded)


@pytest.fixture
def writer(seeded: Path) -> ZoteroWriter:
    return ZoteroWriter(seeded)


# --- reads: feed item must never appear in whole-library reads ----------------

def test_get_items_excludes_feed_library_item(reader: ZoteroReader):
    keys = {it["item_key"] for it in reader.get_items(limit=500)["items"]}
    assert USER_KEY in keys
    assert FEED_KEY not in keys


def test_get_all_items_excludes_feed_library_item(reader: ZoteroReader):
    keys = {it["item_key"] for it in reader.get_all_items()["items"]}
    assert keys == {USER_KEY}


def test_get_field_values_excludes_feed_library_item(reader: ZoteroReader):
    urls = reader.get_field_values("url")
    assert USER_KEY in urls
    assert FEED_KEY not in urls


def test_get_item_detail_returns_none_for_feed_item(reader: ZoteroReader):
    assert reader.get_item_detail(USER_KEY) is not None
    assert reader.get_item_detail(FEED_KEY) is None


def test_get_item_membership_feed_item_reads_as_absent(reader: ZoteroReader):
    assert reader.get_item_membership(USER_KEY)["exists"] is True
    assert reader.get_item_membership(FEED_KEY)["exists"] is False


def test_get_library_stats_counts_only_user_library(reader: ZoteroReader):
    assert reader.get_library_stats()["total_items"] == 1


def test_get_tags_excludes_feed_item_tags(reader: ZoteroReader):
    tags = {t["tag"] for t in reader.get_tags()}
    assert "user-tag" in tags
    assert "feed-only-tag" not in tags


# --- writes: every key->item write must refuse a feed item's key --------------

def _apply_one(writer: ZoteroWriter, change_type: str, item_key: str, payload: dict) -> dict:
    return writer.apply_changes(
        [{"id": 1, "change_type": change_type, "item_key": item_key, "payload_json": payload}],
        create_backup=False,
    )


WRITE_CASES = [
    ("tag_changes", {"add_tags": ["zs:must_read"], "remove_tags": []}),
    ("set_field", {"field": "callNumber", "value": "zr-0001"}),
    ("add_note", {"note_title": "Triage", "note_html": "<h2>verdict</h2>"}),
    ("add_to_collection", {"collection_path": "Inbox"}),
]


@pytest.mark.parametrize("change_type,payload", WRITE_CASES)
def test_write_rejects_feed_item(writer: ZoteroWriter, change_type: str, payload: dict):
    result = _apply_one(writer, change_type, FEED_KEY, payload)
    assert result["applied_ids"] == []
    assert len(result["failed"]) == 1
    assert "user-library" in result["failed"][0]["error"]


@pytest.mark.parametrize("change_type,payload", WRITE_CASES)
def test_write_accepts_user_item(writer: ZoteroWriter, change_type: str, payload: dict):
    result = _apply_one(writer, change_type, USER_KEY, payload)
    assert result["failed"] == [], result["failed"]
    assert result["applied_ids"] == [1]


def test_add_attachment_rejects_feed_parent_but_accepts_user(writer: ZoteroWriter, tmp_path: Path):
    """Regression for the original trace: the bulk full-text path attached a
    user-library PDF to a feed parent. The guard must reject the feed parent
    while still attaching to a real user-library item."""
    pdf = tmp_path / "fulltext.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal\n")
    payload = {"source_path": str(pdf), "filename": "fulltext.pdf",
               "source_url": "https://arxiv.org/pdf/2402.00002", "title": "Full Text PDF"}

    feed_result = _apply_one(writer, "add_attachment", FEED_KEY, payload)
    assert feed_result["applied_ids"] == []
    assert "user-library" in feed_result["failed"][0]["error"]

    user_result = _apply_one(writer, "add_attachment", USER_KEY, payload)
    assert user_result["failed"] == [], user_result["failed"]
    assert user_result["applied_ids"] == [1]
