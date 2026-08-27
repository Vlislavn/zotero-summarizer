"""Schema, migrations, and connections for the feed-processing tables."""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from zotero_summarizer.storage.feed_identity import legacy_feed_key, stable_feed_key_from_item
from zotero_summarizer.storage.feeds_lookup import _col


LOGGER = logging.getLogger("zotero_summarizer.storage.feeds")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS processed_feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_library_id INTEGER NOT NULL,
    feed_item_id INTEGER NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'zotero',
    stable_feed_key TEXT,
    guid TEXT NOT NULL,
    title TEXT NOT NULL,
    doi TEXT,
    arxiv_id TEXT,
    feed_name TEXT,
    decision TEXT NOT NULL,
    decision_reason TEXT NOT NULL DEFAULT '',
    composite_score REAL,
    surprise_score REAL,
    corpus_affinity REAL,
    reading_priority TEXT,
    is_black_swan INTEGER NOT NULL DEFAULT 0,
    model_version TEXT,
    run_id TEXT NOT NULL,
    planned_zotero_key TEXT,
    matched_collections_json TEXT,
    error TEXT,
    -- Phase 1.5 outcome-feedback columns
    materialized_zotero_key TEXT,
    zotero_sync_status TEXT,
    outcome_eligible_at TEXT,
    outcome_detected_at TEXT,
    final_outcome TEXT,
    outcome_signal_weight REAL,
    read_time_marked_at TEXT,
    -- Phase 1.14: SHAP contributions + OpenAlex author/venue raw context, JSON-encoded.
    shap_contribs_json TEXT,
    -- Full-text peer-review quality assessment (QualityReview), JSON-encoded.
    quality_review_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(feed_library_id, feed_item_id)
)
"""

CREATE_RSS_FEEDS_TABLE = """
CREATE TABLE IF NOT EXISTS rss_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'app',
    imported_zotero_library_id INTEGER,
    last_fetched_at TEXT,
    last_error TEXT,
    etag TEXT,
    modified TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(url)
)
"""

CREATE_RSS_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS rss_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rss_feed_id INTEGER NOT NULL,
    stable_feed_key TEXT NOT NULL,
    guid TEXT,
    entry_id TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    url TEXT,
    canonical_url TEXT,
    doi TEXT,
    arxiv_id TEXT,
    publication_date TEXT,
    publication_title TEXT,
    authors TEXT,
    item_type TEXT NOT NULL DEFAULT 'journalArticle',
    raw_json TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    read_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(rss_feed_id) REFERENCES rss_feeds(id) ON DELETE CASCADE,
    UNIQUE(stable_feed_key)
)
"""

CREATE_FEED_KEY_ALIASES_TABLE = """
CREATE TABLE IF NOT EXISTS feed_key_aliases (
    old_key TEXT PRIMARY KEY,
    stable_feed_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_FEED_KEY_ALIAS_AMBIGUITIES_TABLE = """
CREATE TABLE IF NOT EXISTS feed_key_alias_ambiguities (
    old_key TEXT PRIMARY KEY,
    stable_feed_keys_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_run ON processed_feed_items(run_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_guid ON processed_feed_items(guid)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_stable_key ON processed_feed_items(stable_feed_key)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_decision ON processed_feed_items(decision, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_zotero_key ON processed_feed_items(materialized_zotero_key)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_outcome_due ON processed_feed_items(outcome_eligible_at, outcome_detected_at)",
    "CREATE INDEX IF NOT EXISTS idx_rss_feeds_enabled ON rss_feeds(enabled, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_rss_items_feed_read ON rss_items(rss_feed_id, read_at, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_rss_items_stable_key ON rss_items(stable_feed_key)",
    "CREATE INDEX IF NOT EXISTS idx_feed_key_aliases_stable ON feed_key_aliases(stable_feed_key)",
)

# Phase 1.5 migration: add new columns to pre-existing Phase 1 databases.
# SQLite ALTER TABLE does NOT support non-constant defaults like
# ``datetime('now')``, so ``updated_at`` is added without a default. The
# CREATE TABLE path (fresh DB) does include the default — both code paths
# converge. Existing Phase 1 rows get NULL for ``updated_at`` until their
# next update.
MIGRATION_COLUMNS = (
    ("source_type", "TEXT NOT NULL DEFAULT 'zotero'"),
    ("stable_feed_key", "TEXT"),
    ("materialized_zotero_key", "TEXT"),
    ("zotero_sync_status", "TEXT"),
    ("outcome_eligible_at", "TEXT"),
    ("outcome_detected_at", "TEXT"),
    ("final_outcome", "TEXT"),
    ("outcome_signal_weight", "REAL"),
    ("read_time_marked_at", "TEXT"),
    ("updated_at", "TEXT"),
    ("shap_contribs_json", "TEXT"),   # Phase 1.14
    ("quality_review_json", "TEXT"),  # full-text quality review
    ("abstract", "TEXT"),             # feed item abstract (for Today card)
    ("pub_year", "INTEGER"),          # publication year parsed from publication_date
)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _backfill_stable_feed_keys(conn: sqlite3.Connection) -> int:
    """Populate ``processed_feed_items.stable_feed_key`` for existing rows."""
    rows = conn.execute(
        """
        SELECT id, guid, doi, arxiv_id
        FROM processed_feed_items
        WHERE stable_feed_key IS NULL OR stable_feed_key = ''
        """
    ).fetchall()
    updated = 0
    for row in rows:
        row_id = int(_col(row, 0, "id"))
        item = {
            "guid": _col(row, 1, "guid"),
            "doi": _col(row, 2, "doi"),
            "arxiv_id": _col(row, 3, "arxiv_id"),
        }
        stable = stable_feed_key_from_item(item)
        if not stable:
            continue
        conn.execute(
            "UPDATE processed_feed_items SET stable_feed_key = ? WHERE id = ?",
            (stable, row_id),
        )
        updated += 1
    return updated


def _backfill_feed_key_aliases(conn: sqlite3.Connection) -> dict[str, int]:
    """Map unambiguous legacy ``feed:<feed_item_id>`` keys to stable keys."""
    rows = conn.execute(
        """
        SELECT feed_item_id, stable_feed_key
        FROM processed_feed_items
        WHERE feed_item_id > 0
          AND stable_feed_key IS NOT NULL
          AND stable_feed_key != ''
        """
    ).fetchall()
    grouped: dict[str, set[str]] = {}
    row_counts: dict[str, int] = {}
    for row in rows:
        old_key = legacy_feed_key(_col(row, 0, "feed_item_id"))
        if not old_key:
            continue
        grouped.setdefault(old_key, set()).add(str(_col(row, 1, "stable_feed_key")))
        row_counts[old_key] = row_counts.get(old_key, 0) + 1

    inserted = 0
    ambiguous = 0
    for old_key, stable_keys in grouped.items():
        if len(stable_keys) == 1:
            stable = next(iter(stable_keys))
            conn.execute(
                """
                INSERT INTO feed_key_aliases (old_key, stable_feed_key)
                VALUES (?, ?)
                ON CONFLICT(old_key) DO UPDATE SET stable_feed_key = excluded.stable_feed_key
                """,
                (old_key, stable),
            )
            conn.execute("DELETE FROM feed_key_alias_ambiguities WHERE old_key = ?", (old_key,))
            inserted += 1
            continue
        ambiguous += 1
        conn.execute("DELETE FROM feed_key_aliases WHERE old_key = ?", (old_key,))
        conn.execute(
            """
            INSERT INTO feed_key_alias_ambiguities (old_key, stable_feed_keys_json, row_count)
            VALUES (?, ?, ?)
            ON CONFLICT(old_key) DO UPDATE SET
                stable_feed_keys_json = excluded.stable_feed_keys_json,
                row_count = excluded.row_count,
                created_at = datetime('now')
            """,
            (old_key, json.dumps(sorted(stable_keys)), row_counts[old_key]),
        )
    return {"aliases": inserted, "ambiguous": ambiguous}


def _copy_legacy_label_verdicts_to_stable_keys(conn: sqlite3.Connection) -> int:
    """Duplicate old ``feed:<id>`` verdict rows onto their stable keys."""
    if not _table_exists(conn, "label_verdicts"):
        return 0
    verdict_cols = {row[1] for row in conn.execute("PRAGMA table_info(label_verdicts)").fetchall()}
    if "source" not in verdict_cols:
        return 0
    cursor = conn.execute(
        """
        WITH candidates AS (
            SELECT
                a.stable_feed_key,
                lv.original_derived_priority,
                lv.user_priority,
                lv.comment,
                lv.created_at,
                lv.source
            FROM label_verdicts lv
            JOIN feed_key_aliases a ON a.old_key = lv.item_key
        ),
        copyable AS (
            SELECT stable_feed_key
            FROM candidates
            GROUP BY stable_feed_key
            HAVING COUNT(*) = 1
        )
        INSERT INTO label_verdicts (
            item_key, original_derived_priority, user_priority, comment, created_at, source
        )
        SELECT
            c.stable_feed_key,
            c.original_derived_priority,
            c.user_priority,
            c.comment,
            c.created_at,
            c.source
        FROM candidates c
        JOIN copyable cp ON cp.stable_feed_key = c.stable_feed_key
        WHERE NOT EXISTS (
            SELECT 1 FROM label_verdicts existing
            WHERE existing.item_key = c.stable_feed_key
        )
        """
    )
    return int(cursor.rowcount or 0)


def init_feeds_schema(conn: sqlite3.Connection) -> None:
    """Create the feed tables and migrate older databases in place."""
    conn.execute(CREATE_TABLE)
    conn.execute(CREATE_RSS_FEEDS_TABLE)
    conn.execute(CREATE_RSS_ITEMS_TABLE)
    conn.execute(CREATE_FEED_KEY_ALIASES_TABLE)
    conn.execute(CREATE_FEED_KEY_ALIAS_AMBIGUITIES_TABLE)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(processed_feed_items)").fetchall()}
    for col_name, col_def in MIGRATION_COLUMNS:
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE processed_feed_items ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError as exc:
                LOGGER.warning("Failed to add column %s: %s", col_name, exc)
    for stmt in INDEX_STATEMENTS:
        conn.execute(stmt)
    _backfill_stable_feed_keys(conn)
    _backfill_feed_key_aliases(conn)
    _copy_legacy_label_verdicts_to_stable_keys(conn)


@contextmanager
def open_triage_conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a schema-initialized, ``sqlite3.Row``-backed triage connection."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        init_feeds_schema(conn)
        yield conn
    finally:
        conn.close()
