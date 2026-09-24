"""Daemon selection cannot discard or materialize undecided feed candidates."""
from __future__ import annotations

from zotero_summarizer.services.triage.feeds import _daily
from zotero_summarizer.services.triage.feeds._daily_materialize import _PendingScoredRow
from zotero_summarizer.storage import feeds
from tests.test_daily_actions import _build_db, _record


def test_repeated_daily_selection_keeps_prior_picks_pending(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    a, b = _record(db, 41), _record(db, 42)
    monkeypatch.setattr(_daily, "_triage_conn", lambda: feeds.open_triage_conn(db))
    monkeypatch.setattr(_daily, "_load_config", lambda: {
        "feeds": {"daily_target_min": 1, "daily_target_max": 1},
        "selection": {}, "surprise": {},
    })
    monkeypatch.setattr(_daily, "_refine_with_full_text", lambda *args, **kwargs: None)
    monkeypatch.setattr(_daily, "_score_candidates", lambda rows: [
        _PendingScoredRow(float(row["composite_score"] or 0), 0.0, False, row, str(row["id"]))
        for row in rows
    ])

    for _ in range(2):
        result = _daily.run_daily_selection()
        assert result["materialized"] == result["rejected"] == 0
        assert result["selected"] and all("title" in item for item in result["selected"])
        with feeds.open_triage_conn(db) as conn:
            rows = conn.execute(
                "SELECT id, decision, materialized_zotero_key FROM processed_feed_items ORDER BY id"
            ).fetchall()
        assert [row["id"] for row in rows] == [a, b]
        assert all(row["decision"] == feeds.DECISION_TRIAGED_PENDING
                   and row["materialized_zotero_key"] is None for row in rows)
