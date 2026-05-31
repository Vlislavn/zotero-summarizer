"""ZoteroReader item reads: annotation exclusion (the reassurance fix — PDF
annotations are not papers and must never appear as library items) and
``get_all_items`` whole-library pagination past the per-call 500 clamp."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
from zotero_summarizer.integrations._zotero_read_items import ZoteroItemsMixin
from zotero_summarizer.integrations.zotero_read import ZoteroReader


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


def test_get_items_excludes_annotations(zotero_dir: Path):
    """A standalone annotation row must NOT appear as a library item — only the
    real paper does. (Without the fix, ~900 PDF annotations leaked in as 'items'.)"""
    db = zotero_dir / "zotero.sqlite"
    add_library_item(db, item_key="PAPER1", title="A real paper", abstract="abs", item_type="journalArticle")
    add_library_item(db, item_key="ANNO1", title="a highlight", item_type="annotation")

    items = ZoteroReader(zotero_dir).get_items(limit=500)["items"]
    keys = {it["item_key"] for it in items}
    assert "PAPER1" in keys
    assert "ANNO1" not in keys  # annotation excluded


def test_get_all_items_excludes_annotations(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    add_library_item(db, item_key="PAPER1", title="A real paper", abstract="abs")
    add_library_item(db, item_key="ANNO1", title="a highlight", item_type="annotation")

    out = ZoteroReader(zotero_dir).get_all_items()
    keys = {it["item_key"] for it in out["items"]}
    assert keys == {"PAPER1"}
    assert out["total"] == 1


# --- get_all_items pagination (uses the real loop with a fake get_items) ------

class _PagingReader(ZoteroItemsMixin):
    """Exercises the real ``get_all_items`` loop against a fake paged source."""

    def __init__(self, total_items: int):
        self._all = [{"item_key": f"K{i}", "title": f"t{i}"} for i in range(total_items)]
        self.offsets: list[int] = []

    def get_items(self, *, collection_key=None, search=None, tag=None, limit=100, offset=0):
        self.offsets.append(offset)
        chunk = self._all[offset:offset + limit]
        return {"items": chunk, "total": len(self._all), "limit": limit, "offset": offset}


def test_get_all_items_paginates_until_short_page():
    reader = _PagingReader(total_items=5)
    out = reader.get_all_items(page_size=2)
    assert [it["item_key"] for it in out["items"]] == ["K0", "K1", "K2", "K3", "K4"]
    assert out["total"] == 5
    assert reader.offsets == [0, 2, 4]  # third page is short (1 row) → stop


def test_get_all_items_stops_on_exact_multiple_without_extra_fetch():
    reader = _PagingReader(total_items=4)
    out = reader.get_all_items(page_size=2)
    assert out["total"] == 4
    assert reader.offsets == [0, 2]  # offset >= total stops it; no wasted empty page


def test_get_all_items_empty_library():
    reader = _PagingReader(total_items=0)
    out = reader.get_all_items(page_size=2)
    assert out == {"items": [], "total": 0}
    assert reader.offsets == [0]
