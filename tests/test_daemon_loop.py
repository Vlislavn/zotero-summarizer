"""Phase 1.5: daemon tick semantics — round-robin, idempotency, daily trigger."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._zotero_fixtures import add_feed_item, build_zotero_db, set_feed_item_read
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services.feeds import (
    _pick_unread_batch_round_robin,
    _should_run_daily_selection,
)
from zotero_summarizer.storage import feeds as fs


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    db_path = build_zotero_db(tmp_path / "zotero")
    return db_path.parent


# --- round-robin pick ------------------------------------------------------


def test_round_robin_pulls_from_multiple_feeds(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    # 5 unread in feed 2, 1 unread in feed 3
    for i in range(5):
        add_feed_item(db, feed_library_id=2, guid=f"A{i}", title=f"A{i}")
    add_feed_item(db, feed_library_id=3, guid="B1", title="B1")
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=3, feed_library_ids=[2, 3])

    feeds_in_batch = {it["feed_library_id"] for it in batch}
    # Both feeds are represented (round-robin doesn't starve the smaller one).
    assert feeds_in_batch == {2, 3}
    assert len(batch) == 3


def test_round_robin_handles_one_empty_feed(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    add_feed_item(db, feed_library_id=2, guid="A1", title="A1")
    # Feed 3 has zero items
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=5, feed_library_ids=[2, 3])
    assert len(batch) == 1
    assert batch[0]["feed_library_id"] == 2


def test_round_robin_skips_already_read(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    a = add_feed_item(db, feed_library_id=2, guid="A1", title="A1")
    b = add_feed_item(db, feed_library_id=2, guid="A2", title="A2")
    set_feed_item_read(db, feed_item_id=a)
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=5, feed_library_ids=[2])
    assert {it["item_id"] for it in batch} == {b}


def test_round_robin_respects_batch_size(zotero_dir: Path):
    db = zotero_dir / "zotero.sqlite"
    for i in range(20):
        add_feed_item(db, feed_library_id=2, guid=f"A{i}", title=f"A{i}")
    reader = ZoteroReader(zotero_dir)
    batch = _pick_unread_batch_round_robin(reader, batch_size=5, feed_library_ids=[2])
    assert len(batch) == 5


# --- daily-selection trigger logic ----------------------------------------


_MINIMAL_GOALS_YAML = """
research_goals:
  - Test research goal
relevance_scale:
  1: low
  2: low-mid
  3: mid
  4: high-mid
  5: high
llm:
  draft_model: test
  refine_model: test
  api_base: http://localhost:9999/v1
  api_key_env: TEST_KEY
"""


def _bootstrap_minimal_settings(project: Path, monkeypatch) -> "object":
    """Build a Settings instance with a valid (minimal) goals.yaml."""
    from zotero_summarizer.runtime import AppContext, set_context
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.settings import Settings

    project.mkdir(parents=True, exist_ok=True)
    (project / "goals.yaml").write_text(_MINIMAL_GOALS_YAML, encoding="utf-8")
    monkeypatch.setenv("TEST_KEY", "test-key-not-used")
    settings = Settings.load(project_root=project)
    set_context(AppContext(settings=settings))
    lifecycle.startup()
    return settings


def test_should_run_daily_selection_first_time(tmp_path: Path, monkeypatch):
    """On a fresh DB with no prior selection run, should-run returns True."""
    _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)
    feeds_cfg = {"daily_selection_interval_hours": 24}
    # No selected/black-swan rows yet -> should run.
    assert _should_run_daily_selection(feeds_cfg) is True


def test_should_run_daily_selection_respects_interval(tmp_path: Path, monkeypatch):
    """If a daily selection ran 1 hour ago and interval=24h, should-run returns False."""
    settings = _bootstrap_minimal_settings(tmp_path / "proj", monkeypatch)

    import sqlite3
    conn = sqlite3.connect(str(settings.triage_db_path))
    fs.init_feeds_schema(conn)
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO processed_feed_items (
            feed_library_id, feed_item_id, guid, title, decision, run_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "g", "t", fs.DECISION_SELECTED, "r", one_hour_ago),
    )
    conn.commit()
    conn.close()

    assert _should_run_daily_selection({"daily_selection_interval_hours": 24}) is False
    # With zero-hour interval, it should always be True.
    assert _should_run_daily_selection({"daily_selection_interval_hours": 0}) is True
