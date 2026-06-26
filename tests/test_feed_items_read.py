"""Tests for ZoteroReader feed methods (get_feed_groups, get_feed_items, find_by_external_id)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._zotero_fixtures import add_feed_item, add_library_item, build_zotero_db
from zotero_summarizer.integrations.zotero_read import ZoteroReader


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


def test_get_user_library_id_returns_1(zotero_dir: Path):
    r = ZoteroReader(zotero_dir)
    assert r.get_user_library_id() == 1


def test_get_feed_groups_returns_both_feeds_sorted(zotero_dir: Path):
    r = ZoteroReader(zotero_dir)
    groups = r.get_feed_groups()
    assert len(groups) == 2
    names = [g["name"] for g in groups]
    assert names == sorted(names, key=str.lower)
    g = groups[0]
    assert {"library_id", "name", "url", "last_update", "last_check", "refresh_interval_minutes"}.issubset(g.keys())


def test_get_feed_items_filters_by_feed_library(zotero_dir: Path):
    add_feed_item(zotero_dir / "zotero.sqlite", feed_library_id=2, guid="g1", title="From A 1")
    add_feed_item(zotero_dir / "zotero.sqlite", feed_library_id=2, guid="g2", title="From A 2")
    add_feed_item(zotero_dir / "zotero.sqlite", feed_library_id=3, guid="g3", title="From B 1")

    r = ZoteroReader(zotero_dir)
    a_items = r.get_feed_items(feed_library_id=2)
    b_items = r.get_feed_items(feed_library_id=3)
    all_items = r.get_feed_items()

    assert len(a_items) == 2
    assert len(b_items) == 1
    assert len(all_items) == 3
    assert all(i["feed_library_id"] == 2 for i in a_items)
    assert all(i["feed_library_id"] == 3 for i in b_items)


def test_get_feed_items_populates_metadata(zotero_dir: Path):
    add_feed_item(
        zotero_dir / "zotero.sqlite",
        feed_library_id=2,
        guid="g-arxiv",
        title="BERTrend: trend detection",
        abstract="We propose BERTrend ...",
        url="https://arxiv.org/abs/2411.05930",
        doi="10.48550/arXiv.2411.05930",
    )
    r = ZoteroReader(zotero_dir)
    items = r.get_feed_items(feed_library_id=2)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "BERTrend: trend detection"
    assert item["abstract"] == "We propose BERTrend ..."
    assert item["url"] == "https://arxiv.org/abs/2411.05930"
    assert item["doi"] == "10.48550/arXiv.2411.05930"
    # arXiv ID should be auto-extracted from URL OR DOI
    assert item["arxiv_id"] == "2411.05930"
    assert item["feed_name"] == "Test Feed A"


def test_get_feed_items_sanitizes_injection_chars(zotero_dir: Path):
    """Control + Unicode tag chars must be stripped from feed-supplied strings."""
    add_feed_item(
        zotero_dir / "zotero.sqlite",
        feed_library_id=2,
        guid="g-evil",
        title="Title\x00with\x01control\U000e0001tag chars",
        abstract="Abstract\nwith\ttabs (kept)",
    )
    r = ZoteroReader(zotero_dir)
    items = r.get_feed_items(feed_library_id=2)
    assert len(items) == 1
    assert items[0]["title"] == "Titlewithcontroltag chars"
    # Tab and newline are preserved
    assert "\n" in items[0]["abstract"]
    assert "\t" in items[0]["abstract"]


def test_find_by_external_id_finds_by_doi(zotero_dir: Path):
    add_library_item(
        zotero_dir / "zotero.sqlite",
        item_key="ABCD1234",
        title="Existing paper",
        doi="10.1234/example",
    )
    r = ZoteroReader(zotero_dir)
    assert r.find_by_external_id(doi="10.1234/example") == "ABCD1234"
    # Case-insensitive
    assert r.find_by_external_id(doi="10.1234/EXAMPLE") == "ABCD1234"
    # Missing
    assert r.find_by_external_id(doi="10.9999/nope") is None


def test_find_by_external_id_finds_by_arxiv_via_url(zotero_dir: Path):
    add_library_item(
        zotero_dir / "zotero.sqlite",
        item_key="ARXV0001",
        title="Stored paper",
        url="https://arxiv.org/abs/2411.05930v2",
    )
    r = ZoteroReader(zotero_dir)
    assert r.find_by_external_id(arxiv_id="2411.05930") == "ARXV0001"
    assert r.find_by_external_id(arxiv_id="2099.99999") is None


def test_find_by_external_id_none_when_no_input(zotero_dir: Path):
    r = ZoteroReader(zotero_dir)
    assert r.find_by_external_id() is None
    assert r.find_by_external_id(doi="", arxiv_id="") is None


def test_find_by_external_id_only_user_library(zotero_dir: Path):
    """Should not match items in feed libraries — those aren't 'in my library'."""
    add_library_item(
        zotero_dir / "zotero.sqlite",
        item_key="FEED0001",
        title="Feed-paper",
        doi="10.5555/feed",
        library_id=2,  # feed library
    )
    r = ZoteroReader(zotero_dir)
    assert r.find_by_external_id(doi="10.5555/feed") is None
