from __future__ import annotations

import asyncio
import sqlite3
import pytest

from fastapi.responses import JSONResponse

from zotero_summarizer.api import errors
from zotero_summarizer.integrations._zotero_read_copy import ReadCopy
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


# --- S0: the reader queries a refreshed COPY of zotero.sqlite -------------------------------


def _source(tmp_path, name="Zotero", rows=(1,)):
    zotero_dir = tmp_path / name
    zotero_dir.mkdir()
    conn = sqlite3.connect(zotero_dir / "zotero.sqlite")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE t(a)")
    conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    conn.commit()
    return zotero_dir, conn


def _count(reader):
    return reader._execute_read(lambda c: c.execute("SELECT COUNT(*) FROM t").fetchone()[0])


def _backups(monkeypatch):
    calls = []
    real = ReadCopy._backup
    monkeypatch.setattr(ReadCopy, "_backup", lambda self: (calls.append(1), real(self))[1])
    return calls


def test_read_copy_refreshes_only_when_the_source_changed(monkeypatch, tmp_path):
    zotero_dir, live = _source(tmp_path)
    calls = _backups(monkeypatch)
    reader = ZoteroReader(zotero_dir, tmp_path / "copy" / "zotero.sqlite", refresh_interval_seconds=0)

    assert _count(reader) == 1 and _count(reader) == 1
    assert len(calls) == 1  # unchanged source -> no second copy
    live.execute("INSERT INTO t VALUES (2)")
    live.commit()
    assert _count(reader) == 2  # changed source (WAL) -> new copy
    assert len(calls) == 2 and reader.stale_since is None


def test_read_copy_refresh_is_throttled_but_a_missing_copy_always_copies(monkeypatch, tmp_path):
    zotero_dir, live = _source(tmp_path)
    calls = _backups(monkeypatch)
    reader = ZoteroReader(zotero_dir, tmp_path / "copy" / "zotero.sqlite", refresh_interval_seconds=3600)

    assert _count(reader) == 1
    live.execute("INSERT INTO t VALUES (2)")
    live.commit()
    assert _count(reader) == 1 and len(calls) == 1  # changed, but inside the interval
    (tmp_path / "copy" / "zotero.sqlite").unlink()
    assert _count(reader) == 2 and len(calls) == 2  # no copy -> refresh regardless of throttle


def test_read_copy_never_opens_the_live_file_except_as_backup_source(monkeypatch, tmp_path):
    zotero_dir, live = _source(tmp_path)
    live_path = str(zotero_dir / "zotero.sqlite")
    opened = []
    real_connect = sqlite3.connect

    def spy(target, *args, **kwargs):
        opened.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    reader = ZoteroReader(zotero_dir, tmp_path / "copy" / "zotero.sqlite", refresh_interval_seconds=0)
    assert _count(reader) == 1
    live.execute("INSERT INTO t VALUES (2)")
    live.commit()
    assert _count(reader) == 2

    live_opens = [target for target in opened if live_path in target]
    assert len(live_opens) == 2 and all(t.endswith("?mode=ro") for t in live_opens)  # one backup source per refresh
    assert opened.count(str(tmp_path / "copy" / "zotero.sqlite")) == 0  # queries use the mode=ro URI of the copy


def test_read_copy_serves_the_old_copy_flagged_stale_when_the_source_is_locked(monkeypatch, tmp_path):
    zotero_dir, live = _source(tmp_path)
    reader = ZoteroReader(zotero_dir, tmp_path / "copy" / "zotero.sqlite", refresh_interval_seconds=0)
    assert _count(reader) == 1 and reader.stale_since is None

    live.execute("INSERT INTO t VALUES (2)")
    live.commit()
    real = ReadCopy._backup

    def locked(self):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ReadCopy, "_backup", locked)
    assert _count(reader) == 1  # old copy, not silent: flagged
    first = reader.stale_since
    assert first is not None
    assert _count(reader) == 1 and reader.stale_since == first  # first failure time is kept

    monkeypatch.setattr(ReadCopy, "_backup", real)
    assert _count(reader) == 2 and reader.stale_since is None  # recovered


def test_read_copy_missing_and_source_unreadable_raises_a_clear_error(tmp_path):
    zotero_dir = tmp_path / "Zotero"
    zotero_dir.mkdir()
    (zotero_dir / "zotero.sqlite").write_bytes(b"this is not a sqlite database" * 200)
    reader = ZoteroReader(zotero_dir, tmp_path / "copy" / "zotero.sqlite", refresh_interval_seconds=0)

    with pytest.raises(ZoteroReadError, match="No readable Zotero copy"):
        _count(reader)
    assert not (tmp_path / "copy" / "zotero.sqlite").exists()


def test_read_copy_is_not_reused_for_a_different_source(tmp_path):
    first_dir, _first = _source(tmp_path, "ZoteroA", rows=(1,))
    second_dir, _second = _source(tmp_path, "ZoteroB", rows=(1, 2, 3))
    shared_copy = tmp_path / "copy" / "zotero.sqlite"

    assert _count(ZoteroReader(first_dir, shared_copy, refresh_interval_seconds=3600)) == 1
    assert _count(ZoteroReader(second_dir, shared_copy, refresh_interval_seconds=3600)) == 3


# --- ReadCopy: a source locked by a running Zotero must not hang the reader ---------

def _plain_db(tmp_path):
    source = tmp_path / "zotero.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    return source


def _hold_lock(source):
    """Hold the file lock from another connection, like a running Zotero; the source 'changed'."""
    import os
    holder = sqlite3.connect(source, isolation_level=None)
    holder.execute("PRAGMA locking_mode=EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("INSERT INTO t VALUES (2)")
    os.utime(source, ns=(1, 1))  # signature differs from any stamp
    return holder


def _refresh_within(copy: ReadCopy, seconds: float):
    """Run ``refresh`` in a thread; return (finished, error, elapsed)."""
    import threading
    import time
    outcome: dict = {}

    def run():
        try:
            copy.refresh()
        except Exception as exc:  # noqa: BLE001 - the test inspects the type
            outcome["error"] = exc

    started = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    return not worker.is_alive(), outcome.get("error"), time.monotonic() - started


def test_read_copy_keeps_old_copy_and_marks_stale_when_source_is_locked(tmp_path):
    source = _plain_db(tmp_path)
    copy = ReadCopy(source, tmp_path / "data" / "copy.sqlite")
    copy.refresh()
    assert copy.stale_since is None
    holder = _hold_lock(source)
    try:
        finished, error, elapsed = _refresh_within(copy, 6.0)
    finally:
        holder.close()
    assert finished, "refresh hung on a locked source"
    assert error is None and elapsed < 3.5
    assert copy.stale_since is not None
    old = sqlite3.connect(copy.copy_path)
    try:
        assert old.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        old.close()


def test_read_copy_without_a_copy_reports_a_locked_source_quickly(tmp_path):
    source = _plain_db(tmp_path)
    holder = _hold_lock(source)
    try:
        finished, error, elapsed = _refresh_within(ReadCopy(source, tmp_path / "data" / "copy.sqlite"), 6.0)
    finally:
        holder.close()
    assert finished, "refresh hung on a locked source"
    assert isinstance(error, ZoteroReadError) and elapsed < 3.5
