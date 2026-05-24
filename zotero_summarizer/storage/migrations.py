from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories
from zotero_summarizer.storage.corpus import EmbeddingCache


@dataclass(frozen=True)
class Migration:
    """One ordered, gated schema step. ``apply`` runs inside a transaction the
    runner commits; it must not commit/close the connection itself."""

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _migration_baseline_triage(conn: sqlite3.Connection) -> None:
    repositories.apply_schema(conn)


def _migration_baseline_corpus(_conn: sqlite3.Connection) -> None:
    # The corpus schema is owned by EmbeddingCache's constructor (run before the
    # migration runner), so this baseline step only records the version. Future
    # corpus schema changes append a new numbered step here.
    return None


# Append-only, version-ordered. To change the schema, add a new Migration with
# the next version number — never edit a shipped one or add inline ALTERs.
TRIAGE_MIGRATIONS: list[Migration] = [
    Migration(1, "baseline_schema", _migration_baseline_triage),
]
CORPUS_MIGRATIONS: list[Migration] = [
    Migration(1, "baseline_embedding_cache", _migration_baseline_corpus),
]

# The current target version = the highest defined across namespaces. Both
# namespaces share the v1 baseline today, so this is 1; it advances as steps
# are appended.
SCHEMA_VERSION = max(m.version for m in (*TRIAGE_MIGRATIONS, *CORPUS_MIGRATIONS))


@dataclass(frozen=True)
class MigrationResult:
    triage_db_path: Path
    corpus_db_path: Path
    schema_version: int


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            namespace   TEXT PRIMARY KEY,
            version     INTEGER NOT NULL,
            applied_at  TEXT DEFAULT (datetime('now'))
        )
        """
    )


def _current_version(conn: sqlite3.Connection, namespace: str) -> int:
    row = conn.execute(
        "SELECT version FROM schema_migrations WHERE namespace = ?",
        (namespace,),
    ).fetchone()
    return int(row[0]) if row else 0


def _record_version(conn: sqlite3.Connection, namespace: str, version: int) -> None:
    conn.execute(
        """
        INSERT INTO schema_migrations (namespace, version)
        VALUES (?, ?)
        ON CONFLICT(namespace) DO UPDATE SET
            version = excluded.version,
            applied_at = datetime('now')
        """,
        (namespace, version),
    )


def run_migrations(db_path: Path, namespace: str, migrations: list[Migration]) -> int:
    """Apply every migration whose version exceeds the recorded one, in order.

    Each step + its version bump commit together, so an interrupted run leaves
    the DB at the last fully-applied version (never half-migrated).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = repositories._connect_to(db_path)
    try:
        _ensure_migrations_table(conn)
        conn.commit()
        applied = _current_version(conn, namespace)
        for migration in sorted(migrations, key=lambda m: m.version):
            if migration.version <= applied:
                continue
            migration.apply(conn)
            _record_version(conn, namespace, migration.version)
            conn.commit()
            applied = migration.version
        return applied
    finally:
        conn.close()


def migrate_existing(settings: Settings | None = None) -> MigrationResult:
    """Initialize or upgrade local SQLite stores in place.

    Existing ``triage_history.db`` and ``corpus_cache.db`` files are reused.
    Migrations are additive and version-gated via the ``schema_migrations``
    table, so re-running is a no-op once the DB is at ``SCHEMA_VERSION``.
    """
    effective_settings = settings or Settings.load()
    effective_settings.data_dir.mkdir(parents=True, exist_ok=True)

    run_migrations(effective_settings.triage_db_path, "triage", TRIAGE_MIGRATIONS)

    # Constructing the cache initializes corpus tables without re-embedding; the
    # corpus migration then records the version against the same DB.
    EmbeddingCache(effective_settings.corpus_db_path, "sentence-transformers/all-MiniLM-L6-v2")
    run_migrations(effective_settings.corpus_db_path, "corpus", CORPUS_MIGRATIONS)

    return MigrationResult(
        triage_db_path=effective_settings.triage_db_path,
        corpus_db_path=effective_settings.corpus_db_path,
        schema_version=SCHEMA_VERSION,
    )
