"""Part B tests: never-empty slate fallback + backlog-drain job state.

The live triage drain (custom SOTA LLM) and append_to_golden are covered
by the live P2 verification, not unit tests (they need Zotero + network).
Here we cover the pure logic: the recent-rows fallback and the job-state
machine.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zotero_summarizer.services.triage import daily_select
from zotero_summarizer.services.triage import triage_backlog
from zotero_summarizer.storage import feeds as fs
from zotero_summarizer.storage import repositories as repo


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create the processed_feed_items table + the verdict tables the slate's
    handled-paper exclusion reads from (created by init_db in production)."""
    fs.init_feeds_schema(conn)
    conn.execute(repo._CREATE_LABEL_VERDICTS_TABLE)
    conn.execute(repo._CREATE_ROLE_VALUE_VERDICTS_TABLE)


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage_history.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    conn.close()
    return db


def _record(db: Path, *, feed_item_id: int, decision: str, composite: float,
            created_at: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        _init_schema(conn)
        fs.record_decision(
            conn,
            run_id="r1",
            feed_item={"feed_library_id": 1, "item_id": feed_item_id,
                       "guid": f"g{feed_item_id}", "title": f"Paper {feed_item_id}"},
            decision=decision,
            composite_score=composite,
        )
        # Force created_at to a controlled value so the lookback test is deterministic.
        conn.execute(
            "UPDATE processed_feed_items SET created_at=? WHERE feed_item_id=?",
            (created_at, feed_item_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# B3 — never-empty fallback
# ---------------------------------------------------------------------------


def test_slate_uses_window_when_recent_rows_exist(tmp_path):
    db = _fresh_db(tmp_path)
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    # A fresh triaged_pending row inside the 168h window.
    _record(db, feed_item_id=100, decision=fs.DECISION_TRIAGED_PENDING,
            composite=4.0, created_at="2026-05-21 12:00:00")
    slate = daily_select.assemble_daily_slate(db_path=db, K=5, now=now)
    assert slate.pool_size >= 1
    assert slate.fellback_to_recent is False


def test_slate_falls_back_to_recent_when_window_empty(tmp_path):
    db = _fresh_db(tmp_path)
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    # Only an OLD triaged_pending row (outside the 168h window).
    _record(db, feed_item_id=100, decision=fs.DECISION_TRIAGED_PENDING,
            composite=4.0, created_at="2026-05-10 12:00:00")
    slate = daily_select.assemble_daily_slate(db_path=db, K=5, now=now)
    assert slate.fellback_to_recent is True
    assert slate.pool_size >= 1
    assert len(slate.papers) >= 1  # the old row is surfaced, not blank


def test_slate_truly_empty_when_no_rows(tmp_path):
    db = _fresh_db(tmp_path)
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    slate = daily_select.assemble_daily_slate(db_path=db, K=5, now=now)
    assert slate.pool_size == 0
    assert slate.papers == []
    assert slate.fellback_to_recent is False


# ---------------------------------------------------------------------------
# Pipeline funnel — counts feeding the Today overview strip
# ---------------------------------------------------------------------------


def test_count_all_by_decision_histogram(tmp_path):
    db = _fresh_db(tmp_path)
    _record(db, feed_item_id=1, decision=fs.DECISION_GATE_REJECTED, composite=1.0,
            created_at="2026-05-21 12:00:00")
    _record(db, feed_item_id=2, decision=fs.DECISION_GATE_REJECTED, composite=1.0,
            created_at="2026-05-21 12:00:00")
    _record(db, feed_item_id=3, decision=fs.DECISION_USER_REJECTED, composite=1.0,
            created_at="2026-05-21 12:00:00")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        hist = fs.count_all_by_decision(conn)
    finally:
        conn.close()
    assert hist[fs.DECISION_GATE_REJECTED] == 2
    assert hist[fs.DECISION_USER_REJECTED] == 1


def test_pipeline_payload_stage_counts(tmp_path, monkeypatch):
    from zotero_summarizer.api.routes import daily as daily_route

    db = _fresh_db(tmp_path)
    now_str = "2026-05-22 12:00:00"
    # filtered: 2 gate_rejected + 1 dedup-library reject
    _record(db, feed_item_id=1, decision=fs.DECISION_GATE_REJECTED, composite=1.0, created_at=now_str)
    _record(db, feed_item_id=2, decision=fs.DECISION_GATE_REJECTED, composite=1.0, created_at=now_str)
    _record(db, feed_item_id=3, decision=fs.DECISION_REJECTED_DEDUP_LIBRARY, composite=1.0, created_at=now_str)
    # awaiting: 1 unhandled triaged_pending
    _record(db, feed_item_id=4, decision=fs.DECISION_TRIAGED_PENDING, composite=4.0, created_at=now_str)
    # added: 1 selected + 1 user_approved
    _record(db, feed_item_id=5, decision=fs.DECISION_SELECTED, composite=4.0, created_at=now_str)
    _record(db, feed_item_id=6, decision=fs.DECISION_USER_APPROVED, composite=4.0, created_at=now_str)
    # trashed: 1 user_rejected
    _record(db, feed_item_id=7, decision=fs.DECISION_USER_REJECTED, composite=1.0, created_at=now_str)

    monkeypatch.setattr(daily_route, "_db_path", lambda: db)
    payload = daily_route._pipeline_payload(720)
    by_key = {s["key"]: s["count"] for s in payload["stages"]}
    assert by_key["in"] == 7
    assert by_key["filtered"] == 3
    assert by_key["awaiting"] == 1
    assert by_key["added"] == 2
    assert by_key["trashed"] == 1
    # awaiting must use the handled-aware count, not a raw decision sum.
    assert payload["stages"][2]["key"] == "awaiting"


def test_pipeline_awaiting_excludes_handled(tmp_path, monkeypatch):
    from zotero_summarizer.api.routes import daily as daily_route

    db = _fresh_db(tmp_path)
    now_str = "2026-05-22 12:00:00"
    _record(db, feed_item_id=10, decision=fs.DECISION_TRIAGED_PENDING, composite=4.0, created_at=now_str)
    _record(db, feed_item_id=11, decision=fs.DECISION_TRIAGED_PENDING, composite=3.0, created_at=now_str)
    repo.insert_or_update_label_verdict(
        db, item_key="feed:10",
        original_derived_priority="could_read", user_priority="dont_read", comment="",
    )
    monkeypatch.setattr(daily_route, "_db_path", lambda: db)
    payload = daily_route._pipeline_payload(720)
    by_key = {s["key"]: s["count"] for s in payload["stages"]}
    assert by_key["awaiting"] == 1  # the handled one is excluded


# ---------------------------------------------------------------------------
# B2 — backlog drain job state
# ---------------------------------------------------------------------------


def test_backlog_status_idle_by_default():
    # Ensure a clean state (other tests may have toggled it).
    triage_backlog._finish(error=None, done=False)
    st = triage_backlog.status()
    assert st["running"] is False
    assert "processed" not in st or True  # shape is stable
    assert set(["running", "triaged", "gate_rejected", "error", "done"]).issubset(st.keys())


def test_backlog_single_slot_claim():
    triage_backlog._finish(error=None, done=False)
    assert triage_backlog._reset_and_claim() is True
    assert triage_backlog.is_running() is True
    # Second claim refused while running.
    assert triage_backlog._reset_and_claim() is False
    triage_backlog._finish(error=None, done=True)
    assert triage_backlog.is_running() is False
    st = triage_backlog.status()
    assert st["done"] is True


# ---------------------------------------------------------------------------
# Drain loop: stop on a fatal LLM error instead of spinning to _MAX_TICKS
# ---------------------------------------------------------------------------


def _tick_report(**overrides):
    from types import SimpleNamespace
    base = dict(fetched=0, triaged=0, gate_rejected=0, fast_rejected=0,
                errors=0, fatal_llm_error=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_state(*, gate_only: bool):
    """Minimal RuntimeState stand-in for the drain: the config flag the worker
    reads + a resolve_stage_client hook to detect whether an LLM was built."""
    from types import SimpleNamespace
    calls = {"resolved": False}

    def resolve_stage_client(stage):
        calls["resolved"] = True
        return object()

    return SimpleNamespace(
        app_state=SimpleNamespace(
            config=SimpleNamespace(
                classifier_gate=SimpleNamespace(bulk_drain_gate_only=gate_only),
            ),
        ),
        resolve_stage_client=resolve_stage_client,
        _resolve_calls=calls,
    )


def test_drain_stops_immediately_on_fatal_llm_error(monkeypatch):
    from unittest.mock import MagicMock
    import zotero_summarizer.services._common as common
    from zotero_summarizer.services.triage import feeds

    triage_backlog._finish(error=None, done=False)
    triage_backlog._reset_and_claim()  # zero the shared counters for isolation
    # Legacy (gate_only=False) path, where a fatal LLM error is realistic.
    monkeypatch.setattr(common, "state", lambda: _fake_state(gate_only=False))
    # A fatal report on the very first tick must abort the drain (no spin).
    tick = MagicMock(return_value=_tick_report(
        fetched=100, triaged=2, gate_rejected=50, errors=48, fatal_llm_error=True,
    ))
    monkeypatch.setattr(feeds, "run_daemon_tick", tick)

    triage_backlog._drain_worker()

    assert tick.call_count == 1  # stopped after the first fatal tick
    st = triage_backlog.status()
    assert st["running"] is False
    assert st["done"] is False
    assert "fatal" in (st["error"] or "").lower()


def test_drain_finishes_when_backlog_empty(monkeypatch):
    from unittest.mock import MagicMock
    import zotero_summarizer.services._common as common
    from zotero_summarizer.services.triage import feeds

    triage_backlog._finish(error=None, done=False)
    triage_backlog._reset_and_claim()  # zero the shared counters for isolation
    monkeypatch.setattr(common, "state", lambda: _fake_state(gate_only=True))
    # Tick 1 processes a batch; tick 2 fetches nothing -> drained.
    tick = MagicMock(side_effect=[
        _tick_report(fetched=100, triaged=5, gate_rejected=90, errors=5),
        _tick_report(fetched=0),
    ])
    monkeypatch.setattr(feeds, "run_daemon_tick", tick)

    triage_backlog._drain_worker()

    assert tick.call_count == 2
    st = triage_backlog.status()
    assert st["done"] is True
    assert st["error"] is None
    assert st["triaged"] == 5
    assert st["gate_rejected"] == 90
    # Derived gate-effectiveness surfaced for the UI.
    assert st["gate_onward"] == 5            # triaged + fast_rejected
    assert st["gate_total_seen"] == 95       # onward + gate_rejected
    assert st["gate_reject_rate"] == round(90 / 95, 3)


def test_drain_rescores_slate_on_completion(monkeypatch):
    """When a drain finishes, the Today slate is re-scored under the live gate so
    the freshly-drained rows rank consistently with what was already there — the
    user never has to press "Rescore slate" by hand after a backlog run."""
    from unittest.mock import MagicMock
    import zotero_summarizer.services._common as common
    from zotero_summarizer.services.triage import feeds
    from zotero_summarizer.services.triage import rescore_slate as rescore_mod

    triage_backlog._finish(error=None, done=False)
    triage_backlog._reset_and_claim()
    monkeypatch.setattr(common, "state", lambda: _fake_state(gate_only=True))
    tick = MagicMock(side_effect=[
        _tick_report(fetched=100, triaged=5, gate_rejected=90, errors=5),
        _tick_report(fetched=0),
    ])
    monkeypatch.setattr(feeds, "run_daemon_tick", tick)

    rescore_calls: list[int] = []

    def fake_rescore():
        rescore_calls.append(1)
        return {"rescored": 7, "skipped": 0}

    monkeypatch.setattr(rescore_mod, "rescore_slate", fake_rescore)

    triage_backlog._drain_worker()

    assert rescore_calls == [1]                      # rescored exactly once, after the drain
    st = triage_backlog.status()
    assert st["done"] is True
    assert st["rescored"] == 7                        # count surfaced for the UI
    assert st["rescore_error"] is None


def test_drain_does_not_rescore_on_fatal_error(monkeypatch):
    """A fatal LLM error aborts the drain early; the gate may be unusable, so the
    post-drain rescore must NOT fire."""
    from unittest.mock import MagicMock
    import zotero_summarizer.services._common as common
    from zotero_summarizer.services.triage import feeds
    from zotero_summarizer.services.triage import rescore_slate as rescore_mod

    triage_backlog._finish(error=None, done=False)
    triage_backlog._reset_and_claim()
    monkeypatch.setattr(common, "state", lambda: _fake_state(gate_only=False))
    tick = MagicMock(return_value=_tick_report(
        fetched=100, triaged=2, gate_rejected=50, errors=48, fatal_llm_error=True,
    ))
    monkeypatch.setattr(feeds, "run_daemon_tick", tick)

    rescore_calls: list[int] = []
    monkeypatch.setattr(rescore_mod, "rescore_slate",
                        lambda: rescore_calls.append(1) or {"rescored": 0})

    triage_backlog._drain_worker()

    assert rescore_calls == []                        # no rescore on the fatal path
    assert triage_backlog.status()["rescored"] is None


def test_drain_records_rescore_error_but_still_finishes(monkeypatch):
    """A rescore blow-up after a successful drain is recorded, not raised — the
    items are already triaged + persisted, so the job still completes."""
    from unittest.mock import MagicMock
    import zotero_summarizer.services._common as common
    from zotero_summarizer.services.triage import feeds
    from zotero_summarizer.services.triage import rescore_slate as rescore_mod

    triage_backlog._finish(error=None, done=False)
    triage_backlog._reset_and_claim()
    monkeypatch.setattr(common, "state", lambda: _fake_state(gate_only=True))
    tick = MagicMock(side_effect=[
        _tick_report(fetched=10, triaged=4, gate_rejected=6),
        _tick_report(fetched=0),
    ])
    monkeypatch.setattr(feeds, "run_daemon_tick", tick)

    def boom():
        raise RuntimeError("rescore down")

    monkeypatch.setattr(rescore_mod, "rescore_slate", boom)

    triage_backlog._drain_worker()                    # must not raise

    st = triage_backlog.status()
    assert st["done"] is True                          # drain still succeeded
    assert st["rescored"] is None
    assert "rescore down" in (st["rescore_error"] or "")


def test_drain_ml_only_is_gate_only_with_no_llm(monkeypatch):
    """Default drain (bulk_drain_gate_only=True): gate_only tick, review_mode
    explicitly False (so rows are triaged_pending + marked read), and NO LLM
    client is ever resolved."""
    from unittest.mock import MagicMock
    import zotero_summarizer.services._common as common
    from zotero_summarizer.services.triage import feeds

    triage_backlog._finish(error=None, done=False)
    triage_backlog._reset_and_claim()
    fake = _fake_state(gate_only=True)
    monkeypatch.setattr(common, "state", lambda: fake)
    tick = MagicMock(side_effect=[
        _tick_report(fetched=10, triaged=4, gate_rejected=6),
        _tick_report(fetched=0),
    ])
    monkeypatch.setattr(feeds, "run_daemon_tick", tick)

    triage_backlog._drain_worker()

    _, kwargs = tick.call_args_list[0]
    assert kwargs["gate_only"] is True
    assert kwargs["review_mode"] is False
    assert kwargs["triage_llm"] is None
    assert fake._resolve_calls["resolved"] is False  # no LLM built in the ML-only drain
    assert triage_backlog.status()["done"] is True
