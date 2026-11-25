"""Triage SQLite path, connection setup, and shared query helpers."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK
from zotero_summarizer.settings import default_project_root

# The triage DB path. Resolved cheaply at import (no .env read) and overwritten
# once by ``lifecycle.startup`` with the real Settings value;
# tests monkeypatch it. It is set once at startup and only read concurrently
# thereafter, so plain reads from the feed daemon / API threads are safe.
DB_PATH = default_project_root() / "data" / "triage_history.db"

# Per-context path override. Used by ``with_db_path`` to point the no-arg
# ``_get_conn`` at a different DB *without* mutating the module global. A
# ``ContextVar`` is isolated per thread and per asyncio task, so the feed daemon,
# API worker threads, and triage tasks never clobber each other's override (the
# old save/restore-on-a-global approach was racy).
_DB_PATH_OVERRIDE: ContextVar[Path | None] = ContextVar("triage_db_path_override", default=None)
_WAL_LOCK = threading.Lock()
_WAL_PATHS: set[Path] = set()


def _resolve_db_path() -> Path:
    return _DB_PATH_OVERRIDE.get() or DB_PATH


resolve_db_path = _resolve_db_path


def set_db_path(path: Path) -> None:
    """Set the process default triage DB path at startup."""
    global DB_PATH
    DB_PATH = path


@contextlib.contextmanager
def with_db_path(db_path: Path) -> Iterator[None]:
    """Scope all no-arg connections in this thread/task to *db_path*."""
    token = _DB_PATH_OVERRIDE.set(db_path)
    try:
        yield
    finally:
        _DB_PATH_OVERRIDE.reset(token)


def _connect_to(db_path: Path) -> sqlite3.Connection:
    """Open a hardened connection to *db_path* (perms + busy_timeout + WAL).

    The single opener for every triage-history DB connection; ``_get_conn``
    delegates here for the no-arg (context-var-resolved) path.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        db_path.touch(mode=0o600)
    else:
        os.chmod(db_path, 0o600)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    # ponytail: process-local first-open lock; add an interprocess lock if
    # concurrent first startup is ever supported.
    with _WAL_LOCK:
        if db_path not in _WAL_PATHS:
            conn.execute("PRAGMA journal_mode=WAL")
            _WAL_PATHS.add(db_path)
    return conn


def _get_conn() -> sqlite3.Connection:
    return _connect_to(_resolve_db_path())


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


_ALLOWED_SORT = {
    "composite_score",
    "relevance_score",
    "normalized_score",
    "percentile",
    "rank",
    "confidence",
    "created_at",
    "title",
    "batch_created_at",
    "forced_priority",
}


def _normalize_order(order: str) -> str:
    normalized = order.lower()
    return normalized if normalized in {"asc", "desc"} else "desc"


def _normalize_sort(sort_by: str) -> str:
    return sort_by if sort_by in _ALLOWED_SORT else "composite_score"


def _sort_expression(sort_by: str) -> str:
    if sort_by == "forced_priority":
        priority_case = " ".join(
            f"WHEN '{priority}' THEN {rank}"
            for priority, rank in READING_PRIORITY_SORT_RANK.items()
        )
        return f"CASE forced_priority {priority_case} ELSE 0 END"
    return sort_by


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _json_to_list(raw_value: Any) -> list[Any]:
    if isinstance(raw_value, list):
        return raw_value
    if not isinstance(raw_value, str):
        return []
    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if isinstance(decoded, list):
        return decoded
    return []


_VALID_VERDICTS = ("worth", "waste", "unknown")
_VALID_AB_WINNERS = ("roles", "pure_score", "tied")
_AB_DECISION_THRESHOLD = 8     # weeks needed before decision rule fires
_AB_WINNING_MARGIN = 6         # >=6/8 locks the decision

_VALID_LABEL_PRIORITIES = (
    "must_read", "should_read", "could_read", "dont_read",
)
