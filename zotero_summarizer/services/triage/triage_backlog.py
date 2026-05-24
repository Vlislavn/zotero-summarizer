"""Background drain of the un-triaged feed backlog via the custom ``sota`` provider.

The daily "Today" slate needs ``triaged_pending`` rows. Triage is otherwise
CLI/daemon-only (`run_daemon_tick`), so a fresh feed backlog (thousands of
unread items) never gets scored and Today stays empty. This module runs the
existing pipeline — gate fast-rejects the obvious non-matches for free, then
survivors are scored by the custom ``sota`` LLM — looping until the backlog
is drained, on a single background thread with pollable status.

Single responsibility: job lifecycle + accounting. The actual triage is
``services.feeds.run_daemon_tick`` (with a custom ``sota`` ``triage_llm``).
Idempotent: ``run_daemon_tick`` skips already-processed items, so re-running
is safe.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from zotero_summarizer.services._common import now_iso_z

LOGGER = logging.getLogger(__name__)


# Batch size per tick. Each tick: gate-rejects the obvious ones for free, then
# LLM-scores the survivors CONCURRENTLY (run_daemon_tick uses a thread pool
# sized by triage_job_concurrency). Larger batch = fewer ticks; the trade-off is
# coarser progress granularity and a bigger redo window if a tick is interrupted.
_BATCH_SIZE = 100
# Safety cap on total ticks so a runaway never loops forever.
_MAX_TICKS = 1000

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "fetched": 0,
    "triaged": 0,
    "gate_rejected": 0,
    "fast_rejected": 0,
    "errors": 0,
    "ticks": 0,
    "quality_reviewed": 0,
    "error": None,
    "done": False,
}



def status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def is_running() -> bool:
    with _LOCK:
        return bool(_STATE["running"])


def _reset_and_claim() -> bool:
    """Claim the single drain slot, resetting counters. False if one runs."""
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE.update(
            running=True, started_at=now_iso_z(), finished_at=None,
            fetched=0, triaged=0, gate_rejected=0, fast_rejected=0,
            errors=0, ticks=0, quality_reviewed=0, error=None, done=False,
        )
        return True


def _accumulate(report: Any) -> None:
    with _LOCK:
        _STATE["fetched"] += int(report.fetched)
        _STATE["triaged"] += int(report.triaged)
        _STATE["gate_rejected"] += int(report.gate_rejected)
        _STATE["fast_rejected"] += int(report.fast_rejected)
        _STATE["errors"] += int(report.errors)
        _STATE["ticks"] += 1


def _finish(error: str | None, done: bool) -> None:
    with _LOCK:
        _STATE["running"] = False
        _STATE["finished_at"] = now_iso_z()
        _STATE["error"] = error
        _STATE["done"] = done


def _review_top_k_quality(llm: Any) -> int:
    """Full-text quality review of the top-K triaged_pending picks (by composite).

    Reads each paper's PDF and runs the referee LLM (``services.quality_review``)
    on the top-K that don't yet have a review, persisting each to its row so the
    Today card can show it. Per-row failures are counted + skipped — this is a
    background worker, so one bad PDF must not strand the rest. No-op when the
    feature is disabled or no PDF extractor is available (cards stay
    "not assessed", which is honest, not a masked error).
    """
    import sqlite3

    from zotero_summarizer.services.library import quality_review
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services._common import state as get_state
    from zotero_summarizer.storage import feeds as fs

    app_state = get_state()
    config = app_state.app_state.config
    cfg = config.quality_review
    extractor = getattr(app_state, "pdf_extractor", None)
    if not cfg.enabled or extractor is None:
        return 0
    unpaywall = getattr(app_state, "unpaywall_client", None)

    conn = sqlite3.connect(str(get_settings().triage_db_path))
    reviewed = 0
    try:
        rows = fs.select_by_decisions(
            conn, decisions=[fs.DECISION_TRIAGED_PENDING],
            since_hours=24 * 14, limit=max(cfg.top_k * 10, 100),
        )
        todo = [r for r in rows if not str(r.get("quality_review_json") or "").strip()][: cfg.top_k]
        for row in todo:
            try:
                review = quality_review.review_row(
                    row, config=config, llm=llm, extractor=extractor, unpaywall=unpaywall,
                )
                fs.update_quality_review(
                    conn, row_id=int(row["id"]),
                    quality_review_json=review.model_dump_json(),
                )
                conn.commit()
                reviewed += 1
            except Exception as exc:  # noqa: BLE001 — background per-row boundary
                with _LOCK:
                    _STATE["errors"] += 1
                LOGGER.warning("quality review failed for row id=%s: %s", row.get("id"), exc)
    finally:
        conn.close()
    return reviewed


def _drain_worker(model: str) -> None:
    """Loop ``run_daemon_tick`` until the backlog is empty, then full-text
    quality-review the top-K picks.

    Broad ``except`` is the documented background-worker boundary: there is
    no caller to receive the exception, so it is recorded in the job status
    (``error``) for the UI rather than lost. Every other path lets errors
    surface via the per-tick ``errors`` counter.
    """
    from zotero_summarizer.services.triage import feeds
    from zotero_summarizer.services._adapters import build_triage_llm

    try:
        triage_llm = build_triage_llm(model)
        drained = False
        for _ in range(_MAX_TICKS):
            report = feeds.run_daemon_tick(
                batch_size=_BATCH_SIZE,
                review_mode=False,        # writes triaged_pending (slate needs it)
                triage_llm=triage_llm,
            )
            _accumulate(report)
            if report.fatal_llm_error:
                # The sota endpoint is down/unauthorized — every survivor will
                # fail the same way and errored items are never marked read, so
                # without this the loop would re-fetch and spin to _MAX_TICKS.
                _finish(error="fatal LLM error (endpoint/auth) — drain stopped", done=False)
                return
            if int(report.fetched) == 0:
                # No more unread items fetched → backlog drained.
                drained = True
                break
        reviewed = _review_top_k_quality(triage_llm)
        with _LOCK:
            _STATE["quality_reviewed"] = reviewed
        if drained:
            _finish(error=None, done=True)
        else:
            _finish(error=f"hit safety cap of {_MAX_TICKS} ticks", done=False)
    except Exception as exc:  # noqa: BLE001 — background worker boundary
        _finish(error=f"{type(exc).__name__}: {exc}", done=False)


def start_drain(model: str = "sota") -> bool:
    """Start the backlog drain on a daemon thread. Returns False if a drain
    is already running (the caller should poll ``status`` instead)."""
    if not _reset_and_claim():
        return False
    thread = threading.Thread(target=_drain_worker, args=(model,), daemon=True)
    thread.start()
    return True
