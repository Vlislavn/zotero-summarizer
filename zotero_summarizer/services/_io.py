"""Atomic file/JSON writes, read-only SQLite and UTC timestamps (leaf; no runtime context)."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable


def atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    """Write via a temp file + ``os.replace`` so a crash/race can never leave a
    half-written or truncated file at ``path`` (POSIX rename is atomic). Used for
    irreplaceable artifacts like the golden CSV and the model joblib.
    """
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        write(tmp_path)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def connect_sqlite_ro(db_path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a SQLite DB read-only with a ``Row`` factory.

    Pre-checks existence so a missing file raises a clear ``FileNotFoundError``
    instead of an opaque ``sqlite3.OperationalError`` from URI mode.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"database not found at {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso_z() -> str:
    """UTC timestamp, second precision, ``Z`` suffix (e.g. ``2026-05-23T12:00:00Z``)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json_or_empty(path: Path) -> dict[str, Any]:
    """Read a small JSON state file; a MISSING file → ``{}`` (legitimate absence —
    first run, partially-migrated cache). A file that EXISTS but is unreadable or
    unparseable RAISES — corrupt state is a real signal, never papered over."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict[str, Any], *, indent: int | None = None) -> None:
    """Publish complete JSON via ``atomic_write``, creating parent directories.
    The caller owns the envelope (e.g. ``{"updated_at": now_iso_z(), ...}``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, lambda tmp: tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8"))
