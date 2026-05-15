"""Phase 1.6: unlimited batch, scoped daily selection, feed name resolution."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests._zotero_fixtures import add_feed, add_feed_item, build_zotero_db
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services.feeds import _pick_unread_batch_round_robin
from zotero_summarizer.storage import feeds as fs


# ---------------------------------------------------------------------------
# batch_size=None — unlimited exhaustion
# ---------------------------------------------------------------------------


def test_pick_all_returns_every_unread_item(tmp_path: Path):
    """batch_size=None fetches all unread items (not capped at 5)."""
    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=2, name="TestFeed")
    for i in range(12):
        add_feed_item(db, feed_library_id=2, item_id=i + 100)

    reader = ZoteroReader(db.parent)
    items = _pick_unread_batch_round_robin(reader, batch_size=None, feed_library_ids=[2])
    assert len(items) == 12


def test_pick_bounded_respects_batch_size(tmp_path: Path):
    """An integer batch_size still caps the result."""
    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=2, name="TestFeed")
    for i in range(20):
        add_feed_item(db, feed_library_id=2, item_id=i + 200)

    reader = ZoteroReader(db.parent)
    items = _pick_unread_batch_round_robin(reader, batch_size=5, feed_library_ids=[2])
    assert len(items) <= 5


def test_pick_all_skips_already_read(tmp_path: Path):
    """Items with readTime set are excluded from the unlimited fetch."""
    from tests._zotero_fixtures import set_feed_item_read

    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=3, name="ReadFeed")
    add_feed_item(db, feed_library_id=3, item_id=300)
    add_feed_item(db, feed_library_id=3, item_id=301)
    set_feed_item_read(db, feed_item_id=300)

    reader = ZoteroReader(db.parent)
    items = _pick_unread_batch_round_robin(reader, batch_size=None, feed_library_ids=[3])
    ids = [i["item_id"] for i in items]
    assert 300 not in ids
    assert 301 in ids


# ---------------------------------------------------------------------------
# select_pending_triaged — feed-scoped filtering
# ---------------------------------------------------------------------------


@pytest.fixture
def triage_db(tmp_path: Path):
    path = tmp_path / "triage.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    fs.init_feeds_schema(conn)
    return conn


def _insert_triaged(conn, feed_library_id: int, feed_item_id: int, score: float):
    conn.execute(
        """
        INSERT INTO processed_feed_items
            (feed_library_id, feed_item_id, guid, title, decision, run_id,
             composite_score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (feed_library_id, feed_item_id, f"g{feed_item_id}", f"t{feed_item_id}",
         fs.DECISION_TRIAGED_PENDING, "run-test", score),
    )
    conn.commit()


def test_select_pending_triaged_no_filter_returns_all(triage_db):
    """Without feed_library_ids, all feeds' pending rows are returned."""
    _insert_triaged(triage_db, feed_library_id=2, feed_item_id=1, score=4.0)
    _insert_triaged(triage_db, feed_library_id=5, feed_item_id=2, score=3.0)

    rows = fs.select_pending_triaged(triage_db, since_hours=24)
    assert len(rows) == 2


def test_select_pending_triaged_scoped_to_feed(triage_db):
    """With feed_library_ids=[2], only feed 2's rows are returned."""
    _insert_triaged(triage_db, feed_library_id=2, feed_item_id=1, score=4.0)
    _insert_triaged(triage_db, feed_library_id=5, feed_item_id=2, score=3.0)
    _insert_triaged(triage_db, feed_library_id=2, feed_item_id=3, score=2.5)

    rows = fs.select_pending_triaged(triage_db, since_hours=24, feed_library_ids=[2])
    feed_ids = {r["feed_library_id"] for r in rows}
    assert feed_ids == {2}
    assert len(rows) == 2


def test_select_pending_triaged_scoped_multi_feed(triage_db):
    """feed_library_ids=[2,5] returns from both feeds, not feed 9."""
    _insert_triaged(triage_db, feed_library_id=2, feed_item_id=1, score=4.0)
    _insert_triaged(triage_db, feed_library_id=5, feed_item_id=2, score=3.0)
    _insert_triaged(triage_db, feed_library_id=9, feed_item_id=3, score=2.0)

    rows = fs.select_pending_triaged(triage_db, since_hours=24, feed_library_ids=[2, 5])
    feed_ids = {r["feed_library_id"] for r in rows}
    assert 9 not in feed_ids
    assert {2, 5}.issubset(feed_ids)


# ---------------------------------------------------------------------------
# _resolve_feed_ids — name substring + numeric passthrough
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path, zotero_dir: Path):
    """Build a minimal Settings pointing at tmp_path as project root."""
    from zotero_summarizer.settings import Settings

    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    # Minimal .env so Settings.load can succeed without a real Zotero home
    (proj / ".env").write_text(
        f"OPENAI_API_KEY=test\nOPENAI_API_BASE=http://localhost\nZOTERO_DATA_DIR={zotero_dir}\n"
    )
    (proj / "goals.yaml").write_text(
        "research_goals: []\ntriage_criteria: []\nllm:\n  draft_model: m\n  refine_model: m\n"
        "  api_base: http://localhost\n  api_key_env: OPENAI_API_KEY\ncorpus:\n  enabled: false\n"
        "  embedding_model: none\n  similarity_threshold: -0.3\n  stale_days_for_weak_negative: 30\n"
    )
    return Settings.load(project_root=proj)


def test_resolve_numeric_id(tmp_path: Path):
    """A numeric token is returned as-is without hitting Zotero."""
    from zotero_summarizer.cli import _resolve_feed_ids

    db = build_zotero_db(tmp_path / "zotero")
    settings = _make_settings(tmp_path, db.parent)
    ids = _resolve_feed_ids("2", settings)
    assert ids == [2]


def test_resolve_name_substring(tmp_path: Path):
    """A name substring resolves to the matching feed's library_id."""
    from zotero_summarizer.cli import _resolve_feed_ids

    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=7, name="Agents — Core LLM & Orchestration")
    settings = _make_settings(tmp_path, db.parent)
    ids = _resolve_feed_ids("Agents", settings)
    assert ids == [7]


def test_resolve_mixed_tokens(tmp_path: Path):
    """Mix of numeric ID and name substring both resolve correctly."""
    from zotero_summarizer.cli import _resolve_feed_ids

    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=9, name="bioRxiv — Bioinformatics")
    settings = _make_settings(tmp_path, db.parent)
    ids = _resolve_feed_ids("3,bioRxiv", settings)
    assert 3 in ids
    assert 9 in ids


def test_resolve_no_match_exits(tmp_path: Path):
    """An unrecognised name substring raises SystemExit."""
    from zotero_summarizer.cli import _resolve_feed_ids

    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=2, name="Some Feed")
    settings = _make_settings(tmp_path, db.parent)
    with pytest.raises(SystemExit):
        _resolve_feed_ids("NonExistentFeed", settings)


def test_resolve_ambiguous_name_exits(tmp_path: Path):
    """An ambiguous name (two feeds match) raises SystemExit."""
    from zotero_summarizer.cli import _resolve_feed_ids

    db = build_zotero_db(tmp_path / "zotero")
    add_feed(db, library_id=2, name="Agents — Core LLM")
    add_feed(db, library_id=3, name="Agents — Tooling")
    settings = _make_settings(tmp_path, db.parent)
    with pytest.raises(SystemExit):
        _resolve_feed_ids("Agents", settings)
