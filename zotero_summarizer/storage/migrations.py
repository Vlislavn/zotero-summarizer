from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.corpus import EmbeddingCache


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MigrationResult:
    triage_db_path: Path
    corpus_db_path: Path
    schema_version: int


def _ensure_metadata_table(db_path: Path, namespace: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                namespace   TEXT PRIMARY KEY,
                version     INTEGER NOT NULL,
                applied_at  TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migrations (namespace, version)
            VALUES (?, ?)
            ON CONFLICT(namespace) DO UPDATE SET
                version = excluded.version,
                applied_at = datetime('now')
            """,
            (namespace, SCHEMA_VERSION),
        )
        conn.commit()
    finally:
        conn.close()


def migrate_existing(settings: Settings | None = None) -> MigrationResult:
    """Initialize or upgrade local SQLite stores in place.

    Existing `triage_history.db` and `corpus_cache.db` files are reused. This
    migration is intentionally additive for v1: it creates missing tables and a
    lightweight `schema_migrations` table without deleting user data.
    """
    effective_settings = settings or Settings.load()
    effective_settings.data_dir.mkdir(parents=True, exist_ok=True)

    previous_db_path = repositories.DB_PATH
    repositories.DB_PATH = effective_settings.triage_db_path
    try:
        repositories.init_db()
        _ensure_metadata_table(effective_settings.triage_db_path, "triage")
    finally:
        repositories.DB_PATH = previous_db_path

    # Constructing the cache initializes corpus tables without re-embedding.
    EmbeddingCache(effective_settings.corpus_db_path, "sentence-transformers/all-MiniLM-L6-v2")
    _ensure_metadata_table(effective_settings.corpus_db_path, "corpus")

    return MigrationResult(
        triage_db_path=effective_settings.triage_db_path,
        corpus_db_path=effective_settings.corpus_db_path,
        schema_version=SCHEMA_VERSION,
    )
