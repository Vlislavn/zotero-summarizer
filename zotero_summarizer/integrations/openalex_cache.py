"""SQLite-backed cache for OpenAlex / Unpaywall responses.

A single table in `corpus_cache.db` stores arbitrary JSON payloads keyed by a
short string (e.g. ``doi:10.1234/...``, ``author:A123``, ``unpaywall:10.1234/...``).
Entries older than the configured TTL are treated as missing.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


_CREATE_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS openalex_cache (
    key         TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    fetched_at  INTEGER NOT NULL
);
"""


class OpenAlexCache:
    """Thread-safe (per-connection) SQLite key-value cache."""

    def __init__(self, db_path: Path, *, ttl_seconds: int = 30 * 86400) -> None:
        self.db_path = Path(db_path)
        self.ttl_seconds = int(ttl_seconds)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(_CREATE_CACHE_TABLE)
            conn.commit()
        finally:
            conn.close()

    def get(self, key: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT payload_json, fetched_at FROM openalex_cache WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        payload_json, fetched_at = row
        if int(time.time()) - int(fetched_at) > self.ttl_seconds:
            return None
        try:
            return json.loads(payload_json)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT INTO openalex_cache (key, payload_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    fetched_at   = excluded.fetched_at
                """,
                (key, json.dumps(payload), int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()
