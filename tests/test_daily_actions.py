"""Tests for Today's Stage-1 keep/trash actions (services.daily_actions).

The Zotero materialization + golden CSV writes are I/O boundaries we stub; the
behaviour under test is: the right training labels are recorded, decisions are
set, and feed items are marked read.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zotero_summarizer.services.triage import daily_actions
from zotero_summarizer.services.library import review
from zotero_summarizer.storage import feeds as fs
from zotero_summarizer.storage import repositories as repo


class _FakeWriter:
    def __init__(self, *a, **k):
        self.read_ids: list[int] = []

    def mark_feed_items_read(self, ids):
        self.read_ids = list(ids)
        return len(ids)


class _FakeSettings:
    def __init__(self, db: Path, zdir: Path):
        self.triage_db_path = db
        self.zotero_data_dir = zdir


def _build_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage_history.db"
    conn = sqlite3.connect(str(db))
    try:
        fs.init_feeds_schema(conn)
        conn.execute(repo._CREATE_LABEL_VERDICTS_TABLE)
        conn.commit()
    finally:
        conn.close()
    return db


def _record(db: Path, feed_item_id: int) -> int:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        fs.record_decision(
            conn, run_id="r",
            feed_item={
                "feed_library_id": 1, "item_id": feed_item_id,
                "guid": f"http://arxiv.org/abs/{feed_item_id}", "title": f"P{feed_item_id}",
            },
            decision=fs.DECISION_TRIAGED_PENDING, composite_score=2.0,
        )
        conn.commit()
        return int(conn.execute(
            "SELECT id FROM processed_feed_items WHERE feed_item_id=?", (feed_item_id,),
        ).fetchone()["id"])
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    fake = _FakeSettings(db, tmp_path / "zot")
    monkeypatch.setattr(daily_actions, "get_settings", lambda: fake)
    monkeypatch.setattr(daily_actions, "ZoteroWriter", _FakeWriter)
    appended: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        review, "append_to_golden",
        lambda row, *, label, note, signal_tier="feed_user_label": appended.append(
            (int(row.get("feed_item_id") or 0), label, signal_tier)
        ) or True,
    )
    return db, appended


def _decision(db: Path, pk: int) -> str:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return str(conn.execute(
            "SELECT decision FROM processed_feed_items WHERE id=?", (pk,),
        ).fetchone()["decision"])
    finally:
        conn.close()


def test_trash_records_dont_read_rejects_and_marks_read(env):
    db, appended = env
    pk = _record(db, 100)
    res = daily_actions.trash([pk])
    assert res["trashed"] == 1
    assert res["marked_read"] == 1
    assert repo.get_label_verdict(db, "feed:100")["user_priority"] == "dont_read"
    assert _decision(db, pk) == fs.DECISION_USER_REJECTED
    # Trash is a confident negative → normal feed_user_label tier (weight 0.5).
    assert (100, "dont_read", "feed_user_label") in appended


def test_add_to_library_materializes_and_labels_should_read(env, monkeypatch):
    db, appended = env
    pk = _record(db, 200)
    materialized: list[int] = []
    monkeypatch.setattr(
        review, "materialize_row",
        lambda row, *, writer, used_keys, reason="x": materialized.append(int(row["feed_item_id"])) or "KEY1",
    )
    res = daily_actions.add_to_library([pk])
    assert res["added"] == 1
    assert materialized == [200]
    assert repo.get_label_verdict(db, "feed:200")["user_priority"] == "should_read"
    # Add is a soft pre-read interest signal → feed_interest tier (weight 0.3).
    assert (200, "should_read", "feed_interest") in appended


def test_batch_handles_multiple_ids(env, monkeypatch):
    db, appended = env
    monkeypatch.setattr(review, "materialize_row", lambda row, **k: "K")
    pks = [_record(db, fid) for fid in (301, 302, 303)]
    res = daily_actions.add_to_library(pks)
    assert res["added"] == 3
    assert {a[1] for a in appended} == {"should_read"}
    assert {a[2] for a in appended} == {"feed_interest"}  # all soft-tiered


def test_append_to_golden_writes_signal_tier_to_csv(tmp_path, monkeypatch):
    """End-to-end (no Zotero): the tier reaches the golden CSV column, which is
    what label_weights reads to assign the 0.3 weight."""
    import csv as _csv
    import dataclasses
    from types import SimpleNamespace
    from zotero_summarizer.services.golden.goldenset import GoldenSample

    fields = [f.name for f in dataclasses.fields(GoldenSample)]
    csv_path = tmp_path / "golden.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        _csv.DictWriter(f, fieldnames=fields).writeheader()

    monkeypatch.setattr(review, "_fetch_feed_metadata", lambda **k: {})
    monkeypatch.setattr(review, "get_settings", lambda: SimpleNamespace(project_root=tmp_path))

    review.append_to_golden(
        {"feed_item_id": 777, "feed_library_id": 1, "title": "T", "doi": ""},
        label="should_read", note="added from Today",
        signal_tier="feed_interest", golden_csv_path=csv_path,
    )
    review.append_to_golden(  # default tier path (e.g. trash / relabel)
        {"feed_item_id": 778, "feed_library_id": 1, "title": "T2", "doi": ""},
        label="dont_read", note="trashed", golden_csv_path=csv_path,
    )

    with csv_path.open(encoding="utf-8") as f:
        rows = {r["item_key"]: r for r in _csv.DictReader(f)}
    assert rows["feed:777"]["gold_signal_tier"] == "feed_interest"
    assert rows["feed:777"]["gold_priority_final"] == "should_read"
    assert rows["feed:778"]["gold_signal_tier"] == "feed_user_label"  # default preserved
