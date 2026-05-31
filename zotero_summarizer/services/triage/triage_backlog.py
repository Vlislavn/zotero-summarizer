"""Background drain of the un-triaged feed backlog — ML-first.

The daily "Today" slate needs ``triaged_pending`` rows. Triage is otherwise
CLI/daemon-only (`run_daemon_tick`), so a fresh feed backlog (thousands of
unread items) never gets scored and Today stays empty. This module loops
``run_daemon_tick`` until the backlog drains, on a single background thread with
pollable status.

By default (``classifier_gate.bulk_drain_gate_only=True``) the drain is
**ML-only**: the classifier gate scores every survivor with NO per-item LLM call
— fast, memory-safe, GPU-accelerated embeddings. The LLM is reserved for an
on-demand full-text quality review per paper (Deep Review), never run in bulk.

Single responsibility: job lifecycle + accounting. The actual triage is
``services.feeds.run_daemon_tick``. Idempotent: it skips already-processed
items, so re-running is safe.
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
        s = dict(_STATE)
    # Derived gate-effectiveness (read-side, no extra accumulator state) so the
    # UI can show "the ML gate filtered X%" without computing ratios itself.
    onward = int(s["triaged"]) + int(s["fast_rejected"])   # items the gate let past
    total = int(s["gate_rejected"]) + onward                # items the gate judged
    s["gate_onward"] = onward
    s["gate_total_seen"] = total
    s["gate_reject_rate"] = round(int(s["gate_rejected"]) / total, 3) if total else None
    return s


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


def _drain_worker() -> None:
    """Loop ``run_daemon_tick`` until the backlog is empty. ML-only by default
    (no LLM); the legacy path uses the configured **backlog** stage client.

    Broad ``except`` is the documented background-worker boundary: there is
    no caller to receive the exception, so it is recorded in the job status
    (``error``) for the UI rather than lost. A missing key / unreachable backlog
    provider (legacy path only) surfaces here as a job error — it never crashes
    the app. Every other path lets errors surface via the per-tick ``errors``
    counter.
    """
    from zotero_summarizer.services.triage import feeds
    from zotero_summarizer.services._common import state

    try:
        # ML-first default: the gate scores every survivor with NO per-item LLM
        # call (gate_only), and we write triaged_pending + mark read
        # (review_mode=False) so the slate fills and the picker drains. The
        # full-text quality digest is on-demand per paper (Deep Review), not run
        # in bulk. Set classifier_gate.bulk_drain_gate_only=False for the legacy
        # gate→LLM path (then the backlog stage client is used).
        gate_only = bool(state().app_state.config.classifier_gate.bulk_drain_gate_only)
        triage_llm = None if gate_only else state().resolve_stage_client("backlog")
        drained = False
        for _ in range(_MAX_TICKS):
            report = feeds.run_daemon_tick(
                batch_size=_BATCH_SIZE,
                review_mode=False,           # writes triaged_pending (slate needs it)
                gate_only=gate_only,         # ML-only bulk: no per-item LLM
                allow_daily_selection=False,  # the UI button must not auto-materialize
                                              # papers into the Inbox — the user picks
                                              # on Today. Only the daemon auto-selects.
                triage_llm=triage_llm,
            )
            _accumulate(report)
            if report.fatal_llm_error:
                # Only reachable on the legacy LLM path: the endpoint is
                # down/unauthorized — every survivor fails the same way and
                # errored items are never marked read, so without this the loop
                # would re-fetch and spin to _MAX_TICKS.
                _finish(error="fatal LLM error (endpoint/auth) — drain stopped", done=False)
                return
            if int(report.fetched) == 0:
                # No more unread items fetched → backlog drained.
                drained = True
                break
        if drained:
            _finish(error=None, done=True)
        else:
            _finish(error=f"hit safety cap of {_MAX_TICKS} ticks", done=False)
    except Exception as exc:  # noqa: BLE001 — background worker boundary
        _finish(error=f"{type(exc).__name__}: {exc}", done=False)


def start_drain() -> bool:
    """Start the backlog drain on a daemon thread. Returns False if a drain
    is already running (the caller should poll ``status`` instead). The model
    is resolved from ``goals.yaml: llm_routing.backlog`` inside the worker."""
    if not _reset_and_claim():
        return False
    thread = threading.Thread(target=_drain_worker, daemon=True)
    thread.start()
    return True
