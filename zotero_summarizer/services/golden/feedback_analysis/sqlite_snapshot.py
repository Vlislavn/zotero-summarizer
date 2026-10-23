"""WAL-aware read-only backup and metadata from the retained database only."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from .provenance import canonical_bytes

# Admission is deliberately independent of installed migration versions.
_REQUIRED_COLUMNS = {
    "triage_results": {"id", "item_id", "reading_priority", "response_json", "created_at"},
    "user_feedback": {"id", "item_id", "signal", "original_priority", "created_at"},
    "label_verdicts": {"id", "item_key", "user_priority", "original_derived_priority", "comment", "source", "created_at"},
}


def _require_columns(conn, table, required):
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if not required <= columns:
        raise ValueError(f"triage snapshot has incompatible {table} schema")


def inspect_snapshot(path: Path, guard) -> dict:
    guard.check()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(lambda: int(guard.expired()), 1000)
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise ValueError("retained triage snapshot failed integrity_check")
        schema = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        tables = {row[1] for row in schema if row[0] == "table"}
        for table, columns in _REQUIRED_COLUMNS.items():
            if table not in tables:
                raise ValueError(f"triage snapshot is missing {table}")
            _require_columns(conn, table, columns)
        versions = {"status": "unavailable", "values": None}
        if "schema_migrations" in tables:
            _require_columns(conn, "schema_migrations", {"namespace", "version"})
            rows = conn.execute("SELECT namespace, version FROM schema_migrations ORDER BY namespace").fetchall()
            if any(not isinstance(name, str) or type(version) is not int or version < 0 for name, version in rows):
                raise ValueError("invalid numbered schema version")
            if len({name for name, _version in rows}) != len(rows):
                raise ValueError("duplicate numbered schema namespace")
            versions = {"status": "available", "values": dict(rows)}
        revisions = {"status": "unavailable", "min_revision": None, "max_revision": None}
        if "sync_changes" in tables:
            _require_columns(conn, "sync_changes", {"revision", "item_key", "field", "value", "changed_at"})
            lower, upper = conn.execute("SELECT MIN(revision), MAX(revision) FROM sync_changes").fetchone()
            if any(value is not None and (type(value) is not int or value < 1) for value in (lower, upper)):
                raise ValueError("invalid retained sync revision")
            revisions = {"status": "available", "min_revision": lower, "max_revision": upper}
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        guard.snapshot(pages, page_size)
        metadata = {
            "integrity_check": "ok", "schema_sha256": hashlib.sha256(canonical_bytes(schema)).hexdigest(),
            "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "page_count": pages, "page_size": page_size,
            "numbered_schema_versions": versions, "sync_changes": revisions,
        }
    guard.check()
    return metadata


def _source_size(path: Path) -> int:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("triage source must be an existing regular file, not a symlink")
    size = info.st_size
    wal = path.with_name(path.name + "-wal")
    try:
        wal_info = wal.lstat()
    except FileNotFoundError:
        return size
    if not stat.S_ISREG(wal_info.st_mode):
        raise ValueError("triage WAL must be a regular file")
    return size + wal_info.st_size


def capture_sqlite(source: Path, target: Path, guard) -> dict:
    guard.source_bytes(_source_size(source))
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=0)) as live:
        live.execute("PRAGMA query_only=ON")
        live.set_progress_handler(lambda: int(guard.expired()), 1000)
        guard.snapshot(live.execute("PRAGMA page_count").fetchone()[0], live.execute("PRAGMA page_size").fetchone()[0])
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        callbacks = 0

        def progress(_status, _remaining, total):
            nonlocal callbacks
            callbacks += 1
            guard.backup_progress(callbacks, total)

        with closing(sqlite3.connect(target, timeout=0)) as retained:
            live.backup(retained, pages=guard.budgets.backup_pages_per_step, progress=progress, sleep=0)
    guard.source_bytes(_source_size(source))
    return inspect_snapshot(target, guard)
