"""Phase 1.5: ZoteroReader.get_item_membership distinguishes user-outcome states."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import (
    add_collection_link,
    add_library_item,
    add_tag_to_item,
    build_zotero_db,
    mark_trashed,
)
from zotero_summarizer.integrations.zotero_read import ZoteroReader


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


def test_membership_nonexistent_key(zotero_dir: Path):
    r = ZoteroReader(zotero_dir)
    m = r.get_item_membership("GHOSTKEY")
    assert m["exists"] is False
    assert m["is_trashed"] is False
    assert m["collection_keys"] == []
    assert m["is_in_inbox"] is False
    assert m["has_engagement_tag"] is False


def test_membership_kept_in_inbox_only(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    item_id = add_library_item(db, item_key="K1", title="t")
    add_collection_link(db, item_id=item_id, collection_id=90)  # 90 = Inbox

    r = ZoteroReader(zotero_dir)
    m = r.get_item_membership("K1")
    assert m["exists"] is True
    assert m["is_in_inbox"] is True
    assert len(m["collection_keys"]) == 1


def test_membership_moved_to_other_collection(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    item_id = add_library_item(db, item_key="K2", title="t")
    add_collection_link(db, item_id=item_id, collection_id=91)  # Research, not Inbox

    r = ZoteroReader(zotero_dir)
    m = r.get_item_membership("K2")
    assert m["exists"] is True
    assert m["is_in_inbox"] is False
    assert "Research" in m["collection_names"]


def test_membership_deleted_from_all_collections(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    item_id = add_library_item(db, item_key="K3", title="t")
    # No collectionItems link at all -> deleted from all
    r = ZoteroReader(zotero_dir)
    m = r.get_item_membership("K3")
    assert m["exists"] is True
    assert m["is_trashed"] is False
    assert m["collection_keys"] == []


def test_membership_trashed(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    item_id = add_library_item(db, item_key="K4", title="t")
    mark_trashed(db, item_id=item_id)
    r = ZoteroReader(zotero_dir)
    m = r.get_item_membership("K4")
    assert m["exists"] is True
    assert m["is_trashed"] is True


def test_membership_engagement_tag(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    item_id = add_library_item(db, item_key="K5", title="t")
    add_collection_link(db, item_id=item_id, collection_id=90)
    add_tag_to_item(db, item_id=item_id, tag_name="🧠 must-read")
    r = ZoteroReader(zotero_dir)
    m = r.get_item_membership("K5")
    assert m["has_engagement_tag"] is True
