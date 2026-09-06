"""Today, its counter and rescoring share the cleaned, UTC-ordered pool."""
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.triage import daily_select, rescore_slate as rs
from zotero_summarizer.storage import feeds as fs, repositories as repo
from tests.test_rescore_slate import _conn, _FakeGate, _seed_row


NOW = datetime(2026, 1, 2, 13, tzinfo=timezone.utc)


def _row(conn, item_id, timestamp, **changes):
    rid = _seed_row(
        conn, item_id=item_id, guid=f"g{item_id}", decision=fs.DECISION_TRIAGED_PENDING,
        composite=4.0, priority="should_read",
    )
    conn.execute("UPDATE processed_feed_items SET created_at=? WHERE id=?", (timestamp, rid))
    for key, value in changes.items():
        assert key in {"guid", "doi", "arxiv_id", "decision", "final_outcome"}
        conn.execute(f"UPDATE processed_feed_items SET {key}=? WHERE id=?", (value, rid))
    conn.commit()
    return rid


def _check_consumers(db, monkeypatch, expected, *, fallback, cap=25):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    gate = _FakeGate({key: (4.5, "should_read", 0.9) for key in expected.values()})
    monkeypatch.setattr(rs, "datetime", Clock)
    monkeypatch.setattr(rs, "get_settings", lambda: SimpleNamespace(
        triage_db_path=db, corpus_db_path=db.parent / "corpus.db"))
    monkeypatch.setattr(rs, "get_state", lambda: SimpleNamespace(
        classifier_gate=gate, app_state=SimpleNamespace(config=SimpleNamespace())))
    monkeypatch.setattr(daily_select, "attach_quality_from_reviews", lambda _: None)
    slate = daily_select.assemble_daily_slate(
        db_path=db, now=NOW, lookback_hours=1, backlog_cap=cap, K=30, quality_first=False,
    )
    assert {p.item_id for p in slate.papers} == set(expected)
    assert slate.fellback_to_recent is fallback
    assert slate.pool_size == len(expected)
    assert daily_select.count_awaiting_unhandled(
        db, now=NOW, lookback_hours=1, backlog_cap=cap,
    ) == len(expected)
    result = rs.rescore_slate(lookback_hours=1, backlog_cap=cap)
    assert result["rescored"] == len(expected)
    with sqlite3.connect(db) as conn:
        updated = {r[0] for r in conn.execute(
            "SELECT id FROM processed_feed_items WHERE composite_score=4.5"
        )}
    assert updated == set(expected)


@pytest.mark.parametrize("obstruction", ["handled_old", "handled_window", "doi", "guid", "trash"])
def test_fallback_cap_counts_eligible_unique_papers(tmp_path, monkeypatch, obstruction):
    db, conn = _conn(tmp_path)
    try:
        expected = {}
        for item_id in range(1, 27):
            changes = {}
            if obstruction == "doi":
                changes["doi"] = "10.1234/shared"
            elif obstruction == "guid":
                changes["guid"] = "shared"
            elif obstruction == "trash":
                changes["final_outcome"] = "trashed"
            timestamp = f"2026-01-01 12:00:{60-item_id:02d}"
            if obstruction == "handled_window":
                timestamp = "2026-01-02 12:30:00"
            rid = _row(conn, item_id, timestamp, **changes)
            if item_id == 1 and obstruction in {"doi", "guid"}:
                expected[rid] = changes.get("guid", "g1")
        older = _row(conn, 99, "2025-12-31 12:00:00")
        expected[older] = "g99"
    finally:
        conn.close()
    if obstruction.startswith("handled"):
        for item_id in range(1, 27):
            repo.insert_or_update_label_verdict(
                db, item_key=f"feed:{item_id}", original_derived_priority="should_read",
                user_priority="dont_read", comment="",
            )
    _check_consumers(db, monkeypatch, expected, fallback=True)


def test_window_compares_instants_and_keeps_entire_pool(tmp_path, monkeypatch):
    db, conn = _conn(tmp_path)
    try:
        _row(conn, 1, "2026-01-02T01:00:00+00:00")
        _row(conn, 2, "2026-01-02T13:00:00+02:00")
        expected = {
            _row(conn, 3, "2026-01-02 12:00:00"): "g3",
            _row(conn, 4, "2026-01-02T07:15:00-05:00"): "g4",
            _row(conn, 5, "2026-01-02T12:30:00Z"): "g5",
        }
    finally:
        conn.close()
    _check_consumers(db, monkeypatch, expected, fallback=False, cap=1)


@pytest.mark.parametrize("identity", [None, "doi", "guid"])
def test_fallback_and_dedup_order_by_instant(tmp_path, monkeypatch, identity):
    db, conn = _conn(tmp_path)
    changes = {identity: "shared"} if identity else {}
    try:
        _row(conn, 1, "2026-01-01T23:00:00+05:00", **changes)
        newest = _row(conn, 2, "2026-01-01 20:00:00", **changes)
    finally:
        conn.close()
    _check_consumers(db, monkeypatch, {newest: changes.get("guid", "g2")}, fallback=True, cap=1)


def test_invalid_candidate_timestamp_is_not_an_empty_slate(tmp_path):
    db, conn = _conn(tmp_path)
    try:
        _row(conn, 1, "not-a-date")
    finally:
        conn.close()
    with pytest.raises(ValueError):
        daily_select.count_awaiting_unhandled(db, now=NOW)
