from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import repositories


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


def _migration_offline_sync(conn: sqlite3.Connection) -> None:
    from zotero_summarizer.storage._repo_sync import apply_sync_schema

    apply_sync_schema(conn)


def _migration_reconcile_triage_schema(conn: sqlite3.Connection) -> None:
    """Converge databases whose old v1 marker predates later baseline columns."""
    repositories.apply_schema(conn)


def _migration_corpus_encoder_identity(conn: sqlite3.Connection) -> None:
    # NULL deliberately marks legacy/fallback vectors as unverified, not current.
    conn.execute("ALTER TABLE corpus_embeddings ADD COLUMN encoder_id TEXT")
    conn.execute("ALTER TABLE goal_embeddings ADD COLUMN encoder_id TEXT")


def _migration_corpus_paper_identity(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE corpus_embeddings ADD COLUMN doi TEXT NOT NULL DEFAULT ''")


def _migration_label_mirror_receipts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE label_mirror_receipts ("
        "revision INTEGER PRIMARY KEY REFERENCES sync_changes(revision))"
    )
    conn.execute("DROP TRIGGER sync_label_update")
    conn.execute("""
        CREATE TRIGGER sync_label_update AFTER UPDATE ON label_verdicts
        WHEN OLD.user_priority IS NOT NEW.user_priority
          OR OLD.comment IS NOT NEW.comment OR OLD.source IS NOT NEW.source
          OR OLD.original_derived_priority IS NOT NEW.original_derived_priority BEGIN
          INSERT INTO sync_changes(item_key, field, value, comment, source)
          VALUES (NEW.item_key, 'verdict', NEW.user_priority, NEW.comment, NEW.source);
        END
    """)


def _migration_review_training_sample(conn: sqlite3.Connection) -> None:
    if "training_sample_json" not in repositories.table_columns(conn, "label_verdicts"):
        conn.execute("ALTER TABLE label_verdicts ADD COLUMN training_sample_json TEXT")


# Append-only, version-ordered. To change the schema, add a new Migration with
# the next version number — never edit a shipped one or add inline ALTERs.
TRIAGE_MIGRATIONS: list[Migration] = [
    Migration(1, "baseline_schema", _migration_baseline_triage),
    Migration(2, "offline_mutation_sync", _migration_offline_sync),
    Migration(3, "reconcile_triage_schema", _migration_reconcile_triage_schema),
    Migration(4, "corpus_encoder_alignment", _migration_baseline_corpus),
    Migration(5, "label_mirror_receipts", _migration_label_mirror_receipts),
    Migration(6, "corpus_paper_identity_alignment", _migration_baseline_corpus),
    Migration(7, "review_training_sample", _migration_review_training_sample),
]
CORPUS_MIGRATIONS: list[Migration] = [
    Migration(1, "baseline_embedding_cache", _migration_baseline_corpus),
    Migration(2, "sync_version_alignment", _migration_baseline_corpus),
    Migration(3, "triage_reconcile_alignment", _migration_baseline_corpus),
    Migration(4, "corpus_encoder_identity", _migration_corpus_encoder_identity),
    Migration(5, "label_mirror_alignment", _migration_baseline_corpus),
    Migration(6, "corpus_paper_identity", _migration_corpus_paper_identity),
    Migration(7, "review_training_alignment", _migration_baseline_corpus),
]

# Both namespaces advance in lockstep so one reported target stays meaningful;
# a store unaffected by a schema step receives a named no-op version marker.
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
            conn.execute("BEGIN IMMEDIATE")
            applied = _current_version(conn, namespace)
            if migration.version > applied:
                migration.apply(conn)
                _record_version(conn, namespace, migration.version)
                applied = migration.version
            conn.commit()
        return applied
    finally:
        conn.close()


def migrate_existing(settings: Settings | None = None) -> MigrationResult:
    """Initialize or upgrade local SQLite stores in place.

    Existing ``triage_history.db`` and ``corpus_cache.db`` files are reused.
    Migrations are additive and version-gated via the ``schema_migrations``
    table, so re-running is a no-op once the DB is at ``SCHEMA_VERSION``.
    """
    from zotero_summarizer.storage.corpus import EmbeddingCache

    effective_settings = settings or Settings.load()
    effective_settings.data_dir.mkdir(parents=True, exist_ok=True)

    run_migrations(effective_settings.triage_db_path, "triage", TRIAGE_MIGRATIONS)

    # Construction initializes and migrates corpus tables without re-embedding.
    EmbeddingCache(
        effective_settings.corpus_db_path, "sentence-transformers/all-MiniLM-L6-v2"
    )

    return MigrationResult(
        triage_db_path=effective_settings.triage_db_path,
        corpus_db_path=effective_settings.corpus_db_path,
        schema_version=SCHEMA_VERSION,
    )
