"""Today persistence: a saved verdict + label survive a page reload.

Covers the join reader ``get_label_priorities_by_pks`` and the route helper
``_attach_saved_verdicts`` that overlays saved state onto the slate payload,
so the must/should/could/don't label and the worth/waste rating don't vanish
on refresh (the bug: React state was session-only and the payload echoed
nothing back).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from zotero_summarizer.api.routes import daily
from zotero_summarizer.storage import feeds as fs
from zotero_summarizer.storage import repositories as repo


def _build_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage_history.db"
    conn = sqlite3.connect(str(db))
    try:
        fs.init_feeds_schema(conn)
        conn.execute(repo._CREATE_LABEL_VERDICTS_TABLE)
        conn.execute(repo._CREATE_ROLE_VALUE_VERDICTS_TABLE)
        conn.commit()
    finally:
        conn.close()
    return db


def _record_processed(db: Path, *, feed_item_id: int) -> int:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        fs.record_decision(
            conn,
            run_id="r1",
            feed_item={
                "feed_library_id": 1,
                "item_id": feed_item_id,
                "guid": f"http://arxiv.org/abs/{feed_item_id}",
                "title": f"P{feed_item_id}",
            },
            decision=fs.DECISION_TRIAGED_PENDING,
            composite_score=2.0,
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM processed_feed_items WHERE feed_item_id=?",
            (feed_item_id,),
        ).fetchone()
        return int(row["id"])
    finally:
        conn.close()


def test_label_priorities_join_by_pk(tmp_path):
    db = _build_db(tmp_path)
    pk = _record_processed(db, feed_item_id=4242)
    repo.insert_or_update_label_verdict(
        db,
        item_key="feed:4242",
        original_derived_priority="could_read",
        user_priority="must_read",
        comment="",
    )
    out = repo.get_label_priorities_by_pks(db, [pk, 99999])
    assert out == {pk: "must_read"}


def test_label_priorities_empty_input(tmp_path):
    db = _build_db(tmp_path)
    assert repo.get_label_priorities_by_pks(db, []) == {}


def test_attach_saved_verdicts_overlays_both(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    pk = _record_processed(db, feed_item_id=4242)
    key = "http://arxiv.org/abs/4242"
    repo.insert_or_update_label_verdict(
        db,
        item_key="feed:4242",
        original_derived_priority="could_read",
        user_priority="should_read",
        comment="",
    )
    repo.insert_role_value_verdict(
        db,
        item_key=key,
        role="model",
        verdict="waste",
        composite_score=2.0,
        surprise_score=None,
        corpus_affinity=None,
    )
    monkeypatch.setattr(daily, "_db_path", lambda: db)
    papers = [{"item_key": key, "item_id": pk}]
    daily._attach_saved_verdicts(papers)
    assert papers[0]["role_value_verdict"] == "waste"
    assert papers[0]["user_priority"] == "should_read"


def test_attach_saved_verdicts_none_when_unrated(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    pk = _record_processed(db, feed_item_id=7)
    monkeypatch.setattr(daily, "_db_path", lambda: db)
    papers = [{"item_key": "http://arxiv.org/abs/7", "item_id": pk}]
    daily._attach_saved_verdicts(papers)
    assert papers[0]["role_value_verdict"] is None
    assert papers[0]["user_priority"] is None
