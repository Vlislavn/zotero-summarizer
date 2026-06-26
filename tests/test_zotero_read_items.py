"""ZoteroReader item reads: annotation exclusion (the reassurance fix — PDF
annotations are not papers and must never appear as library items) and
``get_all_items`` whole-library pagination past the per-call 500 clamp."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import add_library_item, build_zotero_db
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


# --- get_all_items: ONE un-paged query (perf-regression contract for fix A) ---


def test_get_all_items_runs_a_single_execute_read(zotero_dir: Path, monkeypatch):
    """A whole-library pass must be ONE connection — so under Zotero-open
    contention it copies the 176 MB DB snapshot at most ONCE, not once per
    500-item page (the regression this fix removes). Counts ``_execute_read``."""
    db = zotero_dir / "zotero.sqlite"
    for i in range(3):
        add_library_item(db, item_key=f"P{i}", title=f"paper {i}", abstract="a")
    reader = ZoteroReader(zotero_dir)
    calls = {"n": 0}
    original = reader._execute_read

    def _counting(fn):
        calls["n"] += 1
        return original(fn)

    monkeypatch.setattr(reader, "_execute_read", _counting)
    out = reader.get_all_items()
    assert {it["item_key"] for it in out["items"]} == {"P0", "P1", "P2"}
    assert calls["n"] == 1  # single read for the whole library, regardless of size


def test_get_all_items_returns_more_than_one_get_items_page(zotero_dir: Path):
    """``get_items`` clamps to 500/call; ``get_all_items`` must NOT inherit that
    cap — the single query returns the whole library past the 500 boundary."""
    db = zotero_dir / "zotero.sqlite"
    for i in range(501):
        add_library_item(db, item_key=f"K{i:04d}", title=f"t{i}", abstract="a")
    out = ZoteroReader(zotero_dir).get_all_items()
    assert out["total"] == 501
    assert len({it["item_key"] for it in out["items"]}) == 501  # none truncated/duped


def test_get_all_items_empty_library(zotero_dir: Path):
    out = ZoteroReader(zotero_dir).get_all_items()
    assert out == {"items": [], "total": 0}
