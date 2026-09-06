from __future__ import annotations

import asyncio
import sqlite3
import pytest

from fastapi.responses import JSONResponse

from zotero_summarizer.api import errors
from zotero_summarizer.integrations.zotero_read import ZoteroReadError, ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError


def test_zotero_reader_construction_rejects_missing_db(tmp_path):
    missing_dir = tmp_path / "missing-zotero"

    try:
        ZoteroReader(missing_dir)
        raise AssertionError("Expected ZoteroReadError")
    except ZoteroReadError as exc:
        assert "data directory not found" in str(exc).lower()


def test_zotero_reader_retries_then_reports_a_persistent_lock(monkeypatch, tmp_path):
    zotero_dir = tmp_path / "Zotero"
    zotero_dir.mkdir()
    (zotero_dir / "zotero.sqlite").touch()
    (zotero_dir / "storage").mkdir()
    reader = ZoteroReader(zotero_dir)

    attempts = {"count": 0}

    def fake_connect():
        attempts["count"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(reader, "_connect", fake_connect)
    with pytest.raises(ZoteroReadError, match="busy"):
        reader._execute_read(lambda conn: {"source": "live"})
    assert attempts["count"] == 2


def test_zotero_reader_recovers_from_a_transient_lock(monkeypatch, tmp_path):
    zotero_dir = tmp_path / "Zotero"
    zotero_dir.mkdir()
    (zotero_dir / "zotero.sqlite").touch()
    (zotero_dir / "storage").mkdir()
    reader = ZoteroReader(zotero_dir)

    connect = reader._connect
    calls = []

    def once_locked():
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return connect()

    monkeypatch.setattr(reader, "_connect", once_locked)
    assert reader._execute_read(lambda conn: conn.execute("SELECT 42").fetchone()[0]) == 42
    assert len(calls) == 2


def test_zotero_error_handlers_return_503():
    read_response = asyncio.run(errors.zotero_read_error_handler(None, ZoteroReadError("database is locked")))
    write_response = asyncio.run(errors.zotero_write_error_handler(None, ZoteroWriteError("write failed")))

    assert isinstance(read_response, JSONResponse)
    assert isinstance(write_response, JSONResponse)
    assert read_response.status_code == 503
    assert write_response.status_code == 503


def test_sort_collection_nodes_pins_inbox_then_readnext():
    """Inbox lands first, the read-next queue second, then everything else
    alphabetical — the two workflow collections are no longer buried."""
    nodes = [
        {"name": "Zebra"},
        {"name": "Read Next"},
        {"name": "Apple"},
        {"name": "Inbox"},
        {"name": "Methods"},
    ]
    ZoteroReader._sort_collection_nodes(nodes)
    assert [n["name"] for n in nodes] == ["Inbox", "Read Next", "Apple", "Methods", "Zebra"]


def test_sort_collection_nodes_pins_recursively_and_matches_readnext_variants():
    """The pin applies at every depth and the read-next matcher is tolerant of
    spacing/casing variants (mirrors the frontend READ_NEXT_RE)."""
    nodes = [
        {"name": "Topics", "children": [{"name": "later stuff"}, {"name": "ReadNext"}, {"name": "AAA"}]},
        {"name": "Inbox"},
    ]
    ZoteroReader._sort_collection_nodes(nodes)
    assert [n["name"] for n in nodes] == ["Inbox", "Topics"]
    assert [c["name"] for c in nodes[1]["children"]] == ["ReadNext", "AAA", "later stuff"]
