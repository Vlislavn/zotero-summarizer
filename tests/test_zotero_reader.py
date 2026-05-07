from __future__ import annotations

import asyncio
import sqlite3

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


def test_zotero_reader_uses_snapshot_fallback_when_live_db_is_busy(monkeypatch, tmp_path):
    zotero_dir = tmp_path / "Zotero"
    zotero_dir.mkdir()
    (zotero_dir / "zotero.sqlite").touch()
    (zotero_dir / "storage").mkdir()
    reader = ZoteroReader(zotero_dir)

    attempts = {"count": 0}

    def fake_connect():
        attempts["count"] += 1
        raise sqlite3.OperationalError("database is locked")

    def fake_snapshot_read(fn):
        return {"source": "snapshot"}

    monkeypatch.setattr(reader, "_connect", fake_connect)
    monkeypatch.setattr(reader, "_execute_snapshot_read", fake_snapshot_read)

    result = reader._execute_read(lambda conn: {"source": "live"})

    assert result == {"source": "snapshot"}
    assert attempts["count"] == 2


def test_zotero_reader_busy_error_when_snapshot_fallback_fails(monkeypatch, tmp_path):
    zotero_dir = tmp_path / "Zotero"
    zotero_dir.mkdir()
    (zotero_dir / "zotero.sqlite").touch()
    (zotero_dir / "storage").mkdir()
    reader = ZoteroReader(zotero_dir)

    def fake_connect():
        raise sqlite3.OperationalError("database is locked")

    def fake_snapshot_read(fn):
        raise ZoteroReadError("snapshot failed")

    monkeypatch.setattr(reader, "_connect", fake_connect)
    monkeypatch.setattr(reader, "_execute_snapshot_read", fake_snapshot_read)

    try:
        reader._execute_read(lambda conn: None)
        raise AssertionError("Expected ZoteroReadError")
    except ZoteroReadError as exc:
        assert "busy" in str(exc).lower()


def test_zotero_error_handlers_return_503():
    read_response = asyncio.run(errors.zotero_read_error_handler(None, ZoteroReadError("database is locked")))
    write_response = asyncio.run(errors.zotero_write_error_handler(None, ZoteroWriteError("write failed")))

    assert isinstance(read_response, JSONResponse)
    assert isinstance(write_response, JSONResponse)
    assert read_response.status_code == 503
    assert write_response.status_code == 503
