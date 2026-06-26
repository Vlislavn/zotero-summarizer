"""Phase 1.5: provenance tag `/zs/feeds-v3` lands with itemTags.type=1 (auto-tag)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


def test_slash_prefixed_tag_creates_tag_with_type_1(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    item_id = add_library_item(db, item_key="K1", title="t")
    writer = ZoteroWriter(zotero_dir)
    writer.apply_changes(
        [
            {
                "id": 1,
                "item_key": "K1",
                "change_type": "tag_changes",
                "payload_json": {"add_tags": ["/zs/feeds-v3"], "remove_tags": []},
            }
        ],
        create_backup=False,
    )

    conn = sqlite3.connect(str(db))
    try:
        # tags.type=1 marks the tag itself as system-managed
        row = conn.execute("SELECT type FROM tags WHERE name=?", ("/zs/feeds-v3",)).fetchone()
        assert row is not None
        assert int(row[0]) == 1, "Slash-prefixed tag should be created with tags.type=1"
        # itemTags.type=1 marks the LINK as auto-assigned (subtler chip in Zotero UI)
        link = conn.execute(
            "SELECT type FROM itemTags WHERE itemID=? AND tagID=(SELECT tagID FROM tags WHERE name=?)",
            (item_id, "/zs/feeds-v3"),
        ).fetchone()
        assert link is not None
        assert int(link[0]) == 1, "Slash-prefixed itemTag should be created with itemTags.type=1"
    finally:
        conn.close()


def test_regular_tag_stays_type_0(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    item_id = add_library_item(db, item_key="K2", title="t")
    writer = ZoteroWriter(zotero_dir)
    writer.apply_changes(
        [
            {
                "id": 1,
                "item_key": "K2",
                "change_type": "tag_changes",
                "payload_json": {"add_tags": ["agent-autonomy"], "remove_tags": []},
            }
        ],
        create_backup=False,
    )

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT type FROM tags WHERE name=?", ("agent-autonomy",)).fetchone()
        assert int(row[0]) == 0, "User tags should stay tags.type=0"
        link = conn.execute(
            "SELECT type FROM itemTags WHERE itemID=? AND tagID=(SELECT tagID FROM tags WHERE name=?)",
            (item_id, "agent-autonomy"),
        ).fetchone()
        assert int(link[0]) == 0
    finally:
        conn.close()


def test_apply_feed_materialization_adds_provenance_tag(zotero_dir: Path):
    """End-to-end: materialize a feed item via the daemon-direct write path."""
    writer = ZoteroWriter(zotero_dir)
    result = writer.apply_feed_materialization(
        new_item_key="K3",
        feed_payload={
            "title": "Test paper",
            "abstract": "A short abstract.",
            "doi": "10.0/x",
            "url": "https://arxiv.org/abs/2511.99999",
            "item_type": "preprint",
        },
        inbox_collection_name="Inbox",
        matched_collections=["Research"],
        tags=["zs:should_read", "agent-autonomy"],
        note_title="Triage: Test paper",
        note_html="<h2>Why this paper</h2><p>x</p>",
        provenance_tag="/zs/feeds-v3",
        create_backup=False,
    )
    assert result["item_key"] == "K3"
    assert "create_item" in result["applied_steps"]
    assert "add_to_inbox" in result["applied_steps"]
    assert "add_to_collection:Research" in result["applied_steps"]

    db = zotero_dir / "zotero.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        tags = [r[0] for r in conn.execute(
            "SELECT t.name FROM tags t JOIN itemTags it ON it.tagID=t.tagID "
            "JOIN items i ON i.itemID=it.itemID WHERE i.key='K3'"
        ).fetchall()]
        assert "/zs/feeds-v3" in tags
        assert "zs:should_read" in tags
        assert "agent-autonomy" in tags

        # And the provenance tag was created with type=1.
        prov_type = conn.execute("SELECT type FROM tags WHERE name=?", ("/zs/feeds-v3",)).fetchone()[0]
        assert int(prov_type) == 1
    finally:
        conn.close()


def test_apply_feed_materialization_auto_creates_inbox(zotero_dir: Path):
    """If Inbox doesn't exist (fresh library), daemon auto-creates it."""
    # Delete the pre-built Inbox to simulate a fresh library.
    db = zotero_dir / "zotero.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM collections WHERE collectionName='Inbox'")
    conn.commit()
    conn.close()

    writer = ZoteroWriter(zotero_dir)
    result = writer.apply_feed_materialization(
        new_item_key="K4",
        feed_payload={"title": "Auto inbox test", "abstract": "x"},
        inbox_collection_name="Inbox",
        matched_collections=[],
        tags=["zs:could_read"],
        note_title="Triage: x",
        note_html="<h2>x</h2><p>x</p>",
        provenance_tag="/zs/feeds-v3",
        create_backup=False,
    )
    # Either "add_to_inbox" (existing) or "add_to_inbox_after_autocreate" is fine.
    assert any("add_to_inbox" in step for step in result["applied_steps"])

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT collectionID FROM collections WHERE collectionName='Inbox'").fetchone()
        assert row is not None, "Inbox should have been auto-created"
    finally:
        conn.close()
