"""Tests for ZoteroWriter._apply_create_item_from_feed — full feed item -> Zotero item round trip."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


@pytest.fixture
def writer(zotero_dir: Path) -> ZoteroWriter:
    return ZoteroWriter(zotero_dir)


def _query(writer: ZoteroWriter, sql: str, params=()):
    import sqlite3 as _s
    conn = _s.connect(str(writer.db_path))
    conn.row_factory = _s.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_create_item_basic_round_trip(writer: ZoteroWriter):
    changes = [
        {
            "id": 1,
            "change_type": "create_item_from_feed",
            "item_key": "NEWPAPER1",
            "payload_json": {
                "title": "Test paper",
                "abstract": "We propose ...",
                "url": "https://arxiv.org/abs/2411.05930",
                "doi": "10.48550/arXiv.2411.05930",
                "item_type": "preprint",
                "publication_date": "2024-11-08",
                "authors": ["John Smith", "Doe, Jane"],
            },
        }
    ]
    result = writer.apply_changes(changes, create_backup=False)
    assert result["failed"] == [], result["failed"]
    assert 1 in result["applied_ids"]

    rows = _query(writer, "SELECT itemID, itemTypeID, libraryID, key FROM items WHERE key=?", ("NEWPAPER1",))
    assert len(rows) == 1
    item_id = int(rows[0]["itemID"])
    assert int(rows[0]["libraryID"]) == 1  # user library
    # itemTypeID for preprint = 31 per fixture
    assert int(rows[0]["itemTypeID"]) == 31

    # Verify itemData rows for title/abstract/url/DOI/date
    data_rows = _query(
        writer,
        """
        SELECT f.fieldName, v.value
        FROM itemData d JOIN fields f ON f.fieldID=d.fieldID
        JOIN itemDataValues v ON v.valueID=d.valueID
        WHERE d.itemID=?
        """,
        (item_id,),
    )
    fields = {r["fieldName"]: r["value"] for r in data_rows}
    assert fields["title"] == "Test paper"
    assert fields["abstractNote"] == "We propose ..."
    assert fields["url"] == "https://arxiv.org/abs/2411.05930"
    assert fields["DOI"] == "10.48550/arXiv.2411.05930"
    assert fields["date"] == "2024-11-08"


def test_create_item_idempotent_when_key_exists(writer: ZoteroWriter):
    changes = [
        {
            "id": 1,
            "change_type": "create_item_from_feed",
            "item_key": "DUPKEY01",
            "payload_json": {"title": "First", "item_type": "journalArticle"},
        }
    ]
    writer.apply_changes(changes, create_backup=False)
    # Apply again with the same key — should silently no-op
    writer.apply_changes(changes, create_backup=False)
    rows = _query(writer, "SELECT COUNT(*) AS n FROM items WHERE key=?", ("DUPKEY01",))
    assert int(rows[0]["n"]) == 1


def test_create_item_rejects_missing_title(writer: ZoteroWriter):
    changes = [
        {
            "id": 1,
            "change_type": "create_item_from_feed",
            "item_key": "NOTITLE1",
            "payload_json": {"item_type": "journalArticle"},
        }
    ]
    result = writer.apply_changes(changes, create_backup=False)
    assert result["applied_ids"] == []
    assert any("title" in (f.get("error") or "").lower() for f in result["failed"])


def test_create_item_falls_back_to_journalArticle_for_unknown_type(writer: ZoteroWriter):
    changes = [
        {
            "id": 1,
            "change_type": "create_item_from_feed",
            "item_key": "UNKTYPE1",
            "payload_json": {"title": "x", "item_type": "thesis"},  # not in allowed set
        }
    ]
    writer.apply_changes(changes, create_backup=False)
    rows = _query(writer, "SELECT itemTypeID FROM items WHERE key=?", ("UNKTYPE1",))
    # 22 = journalArticle per fixture
    assert int(rows[0]["itemTypeID"]) == 22


def test_create_then_collection_then_note_composes(writer: ZoteroWriter):
    """The full Phase 1 sequence: create -> add_to_collection -> add_note must work."""
    changes = [
        {
            "id": 1,
            "change_type": "create_item_from_feed",
            "item_key": "FULL0001",
            "payload_json": {"title": "Full flow paper", "item_type": "preprint"},
        },
        {
            "id": 2,
            "change_type": "add_to_collection",
            "item_key": "FULL0001",
            "payload_json": {"collection_path": "Inbox"},
        },
        {
            "id": 3,
            "change_type": "add_note",
            "item_key": "FULL0001",
            "payload_json": {"note_title": "Triage", "note_html": "<h2>verdict</h2>"},
        },
    ]
    result = writer.apply_changes(changes, create_backup=False)
    assert result["failed"] == [], result["failed"]

    item_rows = _query(writer, "SELECT itemID FROM items WHERE key=?", ("FULL0001",))
    assert len(item_rows) == 1
    item_id = int(item_rows[0]["itemID"])

    coll_rows = _query(
        writer,
        "SELECT collectionID FROM collectionItems WHERE itemID=?",
        (item_id,),
    )
    assert len(coll_rows) == 1
    assert int(coll_rows[0]["collectionID"]) == 90  # Inbox per fixture

    note_rows = _query(
        writer,
        "SELECT note FROM itemNotes WHERE parentItemID=?",
        (item_id,),
    )
    assert len(note_rows) == 1
    assert "verdict" in note_rows[0]["note"]


def test_apply_changes_rejects_unsupported_type(writer: ZoteroWriter):
    changes = [
        {
            "id": 1,
            "change_type": "definitely_not_a_change_type",
            "item_key": "X",
            "payload_json": {},
        }
    ]
    result = writer.apply_changes(changes, create_backup=False)
    assert result["applied_ids"] == []
    assert len(result["failed"]) == 1
