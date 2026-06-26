"""Tests for the append-only agentic interaction log.

The whole point of this log is the IMMUTABLE trajectory the live verdict tables
(UPSERT / DELETE) destroy — so the load-bearing test is that re-rating the same
item appends BOTH events, and that a write failure is warned-not-swallowed.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from zotero_summarizer.runtime import AppContext, set_context
from zotero_summarizer.settings import Settings
from zotero_summarizer.services import interaction_log, run_log
from zotero_summarizer.storage import repositories


def _point_log_at(monkeypatch, log_path: Path) -> None:
    monkeypatch.setattr(
        interaction_log, "settings",
        lambda: SimpleNamespace(interaction_log_path=log_path),
    )


def test_key_kind_classifies_the_heterogeneous_namespace():
    assert interaction_log.key_kind("feed:3") == "feed"
    assert interaction_log.key_kind("processed:7") == "processed"
    assert interaction_log.key_kind("note:5") == "note"
    assert interaction_log.key_kind("http://arxiv.org/abs/2401.001") == "arxiv_url"
    assert interaction_log.key_kind("ABCD1234") == "zotero"


def test_human_feedback_is_append_only_trajectory(tmp_path: Path, monkeypatch):
    """Re-rating the SAME item appends both events in order (the property the
    UPSERT label_verdicts table cannot give)."""
    log_path = tmp_path / "interaction-events.jsonl"
    _point_log_at(monkeypatch, log_path)

    for value in ("must_read", "could_read"):
        interaction_log.log_human_feedback(
            item_key="feed:1", item_key_kind="feed", surface="today_priority",
            model={"priority": "should_read"},
            human={"kind": "priority", "value": value},
        )

    events = run_log.load_runs(log_path)
    assert [e["human"]["value"] for e in events] == ["must_read", "could_read"]
    assert all(e["event"] == "human_feedback" and e["schema"] == 1 for e in events)
    assert events[0]["item_key"] == "feed:1"


def test_log_feed_decision_extracts_model_block_from_row(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "e.jsonl"
    _point_log_at(monkeypatch, log_path)
    row = {
        "reading_priority": "should_read", "composite_score": 3.2,
        "surprise_score": 0.5, "corpus_affinity": 0.8,
        "feed_item_id": 9, "doi": "10.x", "arxiv_id": "2401.1",
    }

    interaction_log.log_feed_decision(
        row=row, item_key="feed:9", surface="today_trash",
        human={"kind": "trash", "value": "dont_read"},
    )

    ev = run_log.load_runs(log_path)[0]
    assert ev["model"] == {
        "priority": "should_read", "composite_score": 3.2,
        "surprise_score": 0.5, "corpus_affinity": 0.8,
    }
    assert ev["stable_id"]["feed_item_id"] == 9
    assert ev["human"]["value"] == "dont_read"
    assert ev["item_key_kind"] == "feed"


def test_behavioural_outcome_records_the_outcome(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "o.jsonl"
    _point_log_at(monkeypatch, log_path)

    interaction_log.log_behavioural_outcome(
        item_key="ZOTKEY1", item_key_kind="zotero",
        model={"priority": "should_read"}, outcome="trashed", signal_weight=-3.0,
        stable_id={"feed_item_id": 9},
    )

    ev = run_log.load_runs(log_path)[0]
    assert ev["event"] == "outcome_resolved"
    assert ev["human"] == {"kind": "outcome", "value": "trashed",
                           "signal_weight": -3.0, "elapsed_days": None}
    assert ev["stable_id"]["feed_item_id"] == 9


def test_emit_failure_is_warned_not_swallowed(tmp_path: Path, monkeypatch, caplog):
    """A logging failure must NOT break the durable decision write, but it MUST
    surface as a WARNING (no silent swallow — the global fail-fast rule)."""
    _point_log_at(monkeypatch, tmp_path / "x.jsonl")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(run_log, "append_run", boom)

    with caplog.at_level(logging.WARNING, logger="zotero_summarizer"):
        interaction_log.log_human_feedback(  # must NOT raise
            item_key="feed:1", item_key_kind="feed", surface="today_priority",
            model={}, human={"kind": "priority", "value": "must_read"},
        )

    assert any("interaction_log" in r.getMessage() for r in caplog.records)


# --- end-to-end: the real golden route handlers emit through the live chain ---


def test_golden_routes_emit_through_the_real_chain(tmp_path: Path):
    """Drive the actual submit_verdict + remove_verdict handlers under a real
    Settings context and confirm both the verdict and its retraction land in
    data/interaction-events.jsonl — the api→service→log chain, not a mock."""
    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.golden_csv_path.write_text(
        "item_key,title,abstract,gold_priority_final\n", encoding="utf-8",
    )
    conn = sqlite3.connect(str(settings.triage_db_path))
    try:
        conn.execute(repositories._CREATE_LABEL_VERDICTS_TABLE)
        conn.commit()
    finally:
        conn.close()
    set_context(AppContext(settings=settings))

    from zotero_summarizer.api.routes import golden

    asyncio.run(golden.submit_verdict(
        golden.VerdictRequest(item_key="ZKEY1", user_priority="must_read", comment="")
    ))
    asyncio.run(golden.remove_verdict("ZKEY1"))

    events = run_log.load_runs(settings.interaction_log_path)
    surfaces = [e["surface"] for e in events]
    assert "annotate_verdict" in surfaces
    assert "annotate_retract" in surfaces
    retract = next(e for e in events if e["surface"] == "annotate_retract")
    assert retract["human"] == {"kind": "retract", "value": "must_read"}
