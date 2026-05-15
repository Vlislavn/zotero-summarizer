"""Tests for Phase 1.17 Step 1 — :func:`assemble_daily_slate`.

Strategy: seed an in-memory-like sqlite DB (file-backed in ``tmp_path``)
directly with synthetic ``processed_feed_items`` rows. This avoids any
SPECTER2 / OpenAlex / LLM round-trips. The role-allocation logic, the
backlog cap, the surprise floor, and the day-stable RNG can all be exercised
with crafted rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pytest

from zotero_summarizer.services.daily_select import (
    DailySlate,
    SlatePaper,
    assemble_daily_slate,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


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


def _make_shap_json(*, affinity: float = 0.3, prestige: float | None = 4.2) -> str:
    payload: dict[str, object] = {
        "shap": [
            {"feature": "venue_h_index", "contribution": 0.12},
            {"feature": "year_recency", "contribution": -0.04},
            {"feature": "title_log_len", "contribution": 0.01},
        ],
        "aux_context": {"corpus_affinity": affinity},
    }
    if prestige is not None:
        payload["summary"] = {"prestige_score": prestige}
    return json.dumps(payload)


def _create_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_CREATE_TABLE_SQL)
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
            )
        conn.execute(
            """
            INSERT INTO processed_feed_items (
                feed_library_id, feed_item_id, guid, title, decision,
                composite_score, surprise_score, corpus_affinity, run_id,
                shap_contribs_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                next_id,
                item_key,
                title,
                decision,
                float(composite_score),
                float(surprise_score),
                None if corpus_affinity is None else float(corpus_affinity),
                "test-run",
                shap_json,
                created_str,
                created_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def triage_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage.db"
    _create_db(db)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_assemble_daily_slate_empty_pool_returns_empty(triage_db: Path) -> None:
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert isinstance(slate, DailySlate)
    assert slate.papers == []
    assert slate.pool_size == 0
    assert slate.capped_at == 0


def test_assemble_daily_slate_basic_K5_with_full_pool(triage_db: Path) -> None:
    # 20 awaiting_review rows with composite_score ramping 1.0..5.0.
    for i in range(20):
        composite = 1.0 + (4.0 * i / 19.0)
        # Put one strongly-surprising paper near the middle.
        surprise = 0.85 if i == 10 else 0.05
        # Provide one strongly-negative affinity for diversity to find.
        affinity = -0.4 if i == 3 else 0.3
        _insert(
            triage_db,
            item_key=f"K{i:02d}",
            decision="awaiting_review",
            composite_score=composite,
            surprise_score=surprise,
            corpus_affinity=affinity,
            shap_contribs_json=_make_shap_json(affinity=affinity, prestige=4.2),
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert len(slate.papers) == 5
    assert slate.pool_size == 20
    # At least one paper should be the surprise pick.
    roles = [p.role for p in slate.papers]
    assert "surprise" in roles
    # Model role (top-2 by composite) should appear too.
    assert "model" in roles
    # All papers should be SlatePaper instances with required fields.
    for paper in slate.papers:
        assert isinstance(paper, SlatePaper)
        assert paper.item_key
        assert paper.role in {"model", "surprise", "audit", "diversity", "model_fallback"}


def test_assemble_daily_slate_respects_lookback_hours(triage_db: Path) -> None:
    recent_ts = _DEFAULT_NOW - timedelta(hours=24)
    old_ts = _DEFAULT_NOW - timedelta(days=30)
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"NEW{i}",
            decision="awaiting_review",
            composite_score=3.0 + i * 0.1,
            created_at=recent_ts,
        )
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"OLD{i}",
            decision="awaiting_review",
            composite_score=3.0 + i * 0.1,
            created_at=old_ts,
        )
    slate = assemble_daily_slate(
        db_path=triage_db, K=5, lookback_hours=72, now=_DEFAULT_NOW
    )
    assert slate.pool_size == 5
    chosen_keys = {p.item_key for p in slate.papers}
    assert all(k.startswith("NEW") for k in chosen_keys)


def test_assemble_daily_slate_dedupes_by_item_key(triage_db: Path) -> None:
    older = _DEFAULT_NOW - timedelta(hours=10)
    newer = _DEFAULT_NOW - timedelta(hours=1)
    _insert(
        triage_db,
        item_key="DUPE",
        decision="awaiting_review",
        composite_score=2.0,
        created_at=older,
        title="old title",
    )
    _insert(
        triage_db,
        item_key="DUPE",
        decision="awaiting_review",
        composite_score=4.0,
        created_at=newer,
        title="new title",
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert slate.pool_size == 1
    assert slate.papers[0].title == "new title"
    assert slate.papers[0].composite_score == pytest.approx(4.0)


def test_assemble_daily_slate_backlog_cap(triage_db: Path) -> None:
    for i in range(100):
        _insert(
            triage_db,
            item_key=f"P{i:03d}",
            decision="awaiting_review",
            composite_score=float(i) / 20.0,  # 0..5
        )
    slate = assemble_daily_slate(
        db_path=triage_db, K=5, backlog_cap=10, now=_DEFAULT_NOW
    )
    assert slate.pool_size == 100
    assert slate.capped_at == 10
    # Top 10 by composite_score should still be chosen at the head.
    top_paper = max(slate.papers, key=lambda p: p.composite_score)
    assert top_paper.composite_score >= 4.0


def test_assemble_daily_slate_surprise_floor(triage_db: Path) -> None:
    for i in range(10):
        _insert(
            triage_db,
            item_key=f"S{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.1 * i,
            surprise_score=0.10,  # all below the 0.30 floor
            corpus_affinity=0.3,
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert "surprise" in slate.empty_role_events
    # No paper should claim the surprise role.
    assert all(p.role != "surprise" for p in slate.papers)


def test_assemble_daily_slate_diversity_requires_negative_affinity(
    triage_db: Path,
) -> None:
    for i in range(10):
        _insert(
            triage_db,
            item_key=f"A{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.1 * i,
            corpus_affinity=0.3,  # all positive
            shap_contribs_json=_make_shap_json(affinity=0.3, prestige=4.2),
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    assert "diversity" in slate.empty_role_events
    assert all(p.role != "diversity" for p in slate.papers)


def test_assemble_daily_slate_audit_pool_from_gate_rejected(triage_db: Path) -> None:
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"R{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.2 * i,
        )
    for i in range(3):
        _insert(
            triage_db,
            item_key=f"G{i}",
            decision="gate_rejected",
            composite_score=1.0,
            shap_contribs_json=_make_shap_json(affinity=0.0, prestige=4.5),
        )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    audit_papers = [p for p in slate.papers if p.role == "audit"]
    assert len(audit_papers) == 1
    assert audit_papers[0].item_key.startswith("G")
    assert audit_papers[0].decision == "gate_rejected"


def test_assemble_daily_slate_audit_deterministic_within_day(triage_db: Path) -> None:
    # Seed enough primary rows so model/surprise/diversity don't consume audit fallback.
    for i in range(5):
        _insert(
            triage_db,
            item_key=f"R{i}",
            decision="awaiting_review",
            composite_score=3.0 + 0.2 * i,
        )
    # 8 gate_rejected candidates so the audit RNG actually picks one of many.
    for i in range(8):
        _insert(
            triage_db,
            item_key=f"G{i}",
            decision="gate_rejected",
            composite_score=1.0,
            shap_contribs_json=_make_shap_json(affinity=0.0, prestige=4.5),
        )
    slate_a = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    slate_b = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    audit_a = [p.item_key for p in slate_a.papers if p.role == "audit"]
    audit_b = [p.item_key for p in slate_b.papers if p.role == "audit"]
    assert audit_a == audit_b
    assert audit_a  # non-empty


def test_assemble_daily_slate_K_smaller_than_pool(triage_db: Path) -> None:
    for i in range(20):
        _insert(
            triage_db,
            item_key=f"P{i:02d}",
            decision="awaiting_review",
            composite_score=1.0 + 0.2 * i,
        )
    slate = assemble_daily_slate(db_path=triage_db, K=3, now=_DEFAULT_NOW)
    assert len(slate.papers) == 3


def test_assemble_daily_slate_K_larger_than_pool(triage_db: Path) -> None:
    _insert(
        triage_db,
        item_key="P0",
        decision="awaiting_review",
        composite_score=3.0,
        corpus_affinity=0.3,
    )
    _insert(
        triage_db,
        item_key="P1",
        decision="awaiting_review",
        composite_score=4.0,
        corpus_affinity=0.3,
    )
    slate = assemble_daily_slate(db_path=triage_db, K=5, now=_DEFAULT_NOW)
    # 2 papers from a pool of 2, multiple empty_role_events because
    # surprise+audit+diversity all fall through to model_fallback which itself
    # runs out of candidates.
    assert len(slate.papers) == 2
    # Surprise should be in empty roles (no surprise_score >= 0.30).
    assert "surprise" in slate.empty_role_events
    # Diversity also empty (no negative-affinity rows).
    assert "diversity" in slate.empty_role_events
    # Audit also empty (no gate_rejected rows).
    assert "audit" in slate.empty_role_events


def test_assemble_daily_slate_rejects_invalid_K(triage_db: Path) -> None:
    with pytest.raises(ValueError, match="K must be positive"):
        assemble_daily_slate(db_path=triage_db, K=0, now=_DEFAULT_NOW)


def test_assemble_daily_slate_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        assemble_daily_slate(
            db_path=tmp_path / "does_not_exist.db", K=5, now=_DEFAULT_NOW
        )
