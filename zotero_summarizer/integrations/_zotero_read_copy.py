"""Refreshed read-only COPY of zotero.sqlite for the reader (leaf module).

The live file is opened only as the ``mode=ro`` source of SQLite's backup API (a
consistent snapshot including WAL pages); every query runs on the copy. Never writes
to the live file or its directory.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from zotero_summarizer.integrations._zotero_read_common import ZoteroReadError, is_expected_sqlite_io

DEFAULT_REFRESH_INTERVAL_SECONDS = 0.0  # 0 = copy on every source change; >0 opt-in throttle (max staleness)
_SOURCE_TIMEOUT_SECONDS = 2.0
_LOCK = threading.Lock()  # one refresh at a time in this process


def _signature(source: Path) -> list[list[int] | None]:
    """(mtime_ns, size) of zotero.sqlite and its -wal; ``None`` for a missing file."""
    out: list[list[int] | None] = []
    for path in (source, source.with_name(source.name + "-wal")):
        try:
            st = path.stat()
            out.append([st.st_mtime_ns, st.st_size])
        except OSError:
            out.append(None)
    return out


def _give_up_when_locked(started: float):
    """``backup(progress=)`` hook. CPython retries BUSY/LOCKED steps forever, so a source
    held locked by a running Zotero would hang the copy (and every reader) with no end;
    raise once the source stayed locked past ``_SOURCE_TIMEOUT_SECONDS``."""
    def progress(status: int, remaining: int, total: int) -> None:
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() - started > _SOURCE_TIMEOUT_SECONDS:
            raise sqlite3.OperationalError("database is locked")
    return progress


class ReadCopy:
    def __init__(self, source: Path, copy_path: Path, *,
                 refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS) -> None:
        self.source = source
        self.copy_path = copy_path
        self.refresh_interval_seconds = refresh_interval_seconds
        self._stamp_path = copy_path.with_name(copy_path.name + ".stamp.json")

    def _load_stamp(self) -> dict:
        try:
            stamp = json.loads(self._stamp_path.read_text())
        except (OSError, ValueError):
            return {}
        return stamp if isinstance(stamp, dict) and stamp.get("source") == str(self.source) else {}

    def _save_stamp(self, stamp: dict) -> None:
        tmp = self._stamp_path.with_name(f"{self._stamp_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({**stamp, "source": str(self.source)}))
        os.replace(tmp, self._stamp_path)

    @property
    def stale_since(self) -> str | None:
        """UTC ISO time the source first changed without the copy catching up; else ``None``."""
        return self._load_stamp().get("stale_since") if self.copy_path.exists() else None

    def refresh(self) -> None:
        """Re-copy when the source changed and the throttle allows; else keep the copy."""
        with _LOCK:
            stamp = self._load_stamp()
            have_copy = self.copy_path.exists() and bool(stamp)
            sig = _signature(self.source)
            if have_copy and (sig == stamp.get("sig")
                              or time.time() - stamp.get("attempted_at", 0.0) < self.refresh_interval_seconds):
                return
            now = time.time()
            try:
                self._backup()
            except (sqlite3.Error, OSError) as exc:
                if not have_copy:
                    raise ZoteroReadError(
                        f"No readable Zotero copy yet and the source is unavailable: {exc}",
                        expected_io=not isinstance(exc, sqlite3.Error) or is_expected_sqlite_io(exc),
                    ) from exc
                stale = stamp.get("stale_since") or datetime.now(timezone.utc).isoformat(timespec="seconds")
                self._save_stamp({**stamp, "attempted_at": now, "stale_since": stale})
                return
            self._save_stamp({"sig": sig, "attempted_at": now, "copied_at": now, "stale_since": None})

    def _backup(self) -> None:
        self.copy_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.copy_path.with_name(f"{self.copy_path.name}.{os.getpid()}.tmp")
        try:
            src = sqlite3.connect(f"{self.source.as_uri()}?mode=ro", uri=True, timeout=_SOURCE_TIMEOUT_SECONDS)
            try:
                dst = sqlite3.connect(str(tmp))
                try:
                    src.backup(dst, progress=_give_up_when_locked(time.monotonic()))
                finally:
                    dst.close()
            finally:
                src.close()
            os.replace(tmp, self.copy_path)
        finally:
            tmp.unlink(missing_ok=True)
