"""SQLite schema definitions for ``processed_feed_items``.

Pure data module split out of ``storage/feeds.py`` for file-size compliance
(<500 LOC each) and single-responsibility: this file holds the table DDL,
index DDL, and the migration-column list — no I/O, no Python control flow.

The function that consumes these constants (``init_feeds_schema``) stays
in ``feeds.py`` so any error-handling decisions about migration races
remain visible alongside the rest of the storage CRUD code.
"""
from __future__ import annotations


CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS processed_feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_library_id INTEGER NOT NULL,
    feed_item_id INTEGER NOT NULL,
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

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_run ON processed_feed_items(run_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_guid ON processed_feed_items(guid)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_decision ON processed_feed_items(decision, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_zotero_key ON processed_feed_items(materialized_zotero_key)",
    "CREATE INDEX IF NOT EXISTS idx_processed_feed_outcome_due ON processed_feed_items(outcome_eligible_at, outcome_detected_at)",
)

# Phase 1.5 migration: add new columns to pre-existing Phase 1 databases.
# SQLite ALTER TABLE does NOT support non-constant defaults like
# ``datetime('now')``, so ``updated_at`` is added without a default. The
# CREATE TABLE path (fresh DB) does include the default — both code paths
# converge. Existing Phase 1 rows get NULL for ``updated_at`` until their
# next update.
MIGRATION_COLUMNS = (
    ("materialized_zotero_key", "TEXT"),
    ("outcome_eligible_at", "TEXT"),
    ("outcome_detected_at", "TEXT"),
    ("final_outcome", "TEXT"),
    ("outcome_signal_weight", "REAL"),
    ("read_time_marked_at", "TEXT"),
    ("updated_at", "TEXT"),
    ("shap_contribs_json", "TEXT"),   # Phase 1.14
    ("quality_review_json", "TEXT"),  # full-text quality review
)
