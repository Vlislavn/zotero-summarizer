"""Shared DB fixtures/helpers for the daily-slate tests."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from zotero_summarizer.storage import repositories as repo


_CREATE_TABLE_SQL = """
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
    materialized_zotero_key TEXT,
    outcome_eligible_at TEXT,
    outcome_detected_at TEXT,
    final_outcome TEXT,
    outcome_signal_weight REAL,
    read_time_marked_at TEXT,
    shap_contribs_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(feed_library_id, feed_item_id)
)
"""


_DEFAULT_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_shap_json(
    *,
    affinity: float = 0.3,
    prestige: float | None = 4.2,
    goal_sims: dict[str, float] | None = None,
) -> str:
    payload: dict[str, object] = {
        "shap": [
            {"feature": "venue_h_index", "contribution": 0.12},
            {"feature": "year_recency", "contribution": -0.04},
            {"feature": "title_log_len", "contribution": 0.01},
        ],
        # goal_sims mirrors the gate aux pass: {goal text: cosine}, absent key
        # == signal unavailable (candidate.row_goal_sim returns None then).
        "aux_context": {"corpus_affinity": affinity, "goal_sims": goal_sims},
    }
    if prestige is not None:
        payload["summary"] = {"prestige_score": prestige}
    return json.dumps(payload)


def _create_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_CREATE_TABLE_SQL)
        # The slate now excludes papers the user has acted on, so the verdict
        # tables must exist (created by repositories.init_db in production).
        conn.execute(repo._CREATE_LABEL_VERDICTS_TABLE)
        conn.execute(repo._CREATE_ROLE_VALUE_VERDICTS_TABLE)
        conn.commit()
    finally:
        conn.close()


def _insert(
    db_path: Path,
    *,
    item_key: str,
    decision: str,
    composite_score: float,
    surprise_score: float = 0.0,
    corpus_affinity: float | None = None,
    created_at: datetime | None = None,
    shap_contribs_json: str | None = None,
    feed_item_id: int | None = None,
    title: str = "Test paper",
    feed_name: str = "",
    doi: str | None = None,
    arxiv_id: str | None = None,
    materialized_zotero_key: str | None = None,
    final_outcome: str | None = None,
    goal_sims: dict[str, float] | None = None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        ts = (created_at or _DEFAULT_NOW).astimezone(timezone.utc)
        created_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute("SELECT COALESCE(MAX(feed_item_id), 0) FROM processed_feed_items")
        next_id = int(cur.fetchone()[0]) + 1 if feed_item_id is None else int(feed_item_id)
        shap_json = shap_contribs_json
        if shap_json is None:
            shap_json = _make_shap_json(
                affinity=corpus_affinity if corpus_affinity is not None else 0.3,
                prestige=4.2,
                goal_sims=goal_sims,
            )
        conn.execute(
            """
            INSERT INTO processed_feed_items (
                feed_library_id, feed_item_id, guid, title, doi, arxiv_id, feed_name,
                decision, composite_score, surprise_score, corpus_affinity, run_id,
                shap_contribs_json, materialized_zotero_key, final_outcome,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                next_id,
                item_key,
                title,
                doi,
                arxiv_id,
                feed_name,
                decision,
                float(composite_score),
                float(surprise_score),
                None if corpus_affinity is None else float(corpus_affinity),
                "test-run",
                shap_json,
                materialized_zotero_key,
                final_outcome,
                created_str,
                created_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()
