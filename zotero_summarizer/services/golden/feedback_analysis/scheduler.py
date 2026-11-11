"""Explicit main-thread weekly automation for the existing feed daemon only."""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone

from . import operation, schedule_state
from .history import aware_time
from .inputs import _no_symlinks
from .provenance import canonical_bytes, utc_iso
from .report import read_protocol

WEEK = timedelta(days=7)
ANCHOR = datetime(1970, 1, 5, tzinfo=timezone.utc)
RULES = {"anchor": "1970-01-05T00:00:00Z", "period_days": 7,
         "activation": "latest_closed_window", "catchup": "oldest_first_bounded",
         "late_events": "all_time_retained_observations_not_timestamp_watermark",
         "cancellation": "bounded_main_thread_drain_then_yield_before_cursor"}
CURSOR_KEYS = {"schema_version", "activation_cutoff", "last_confirmed_cutoff", "last_receipt", "status"}


def closed_cutoff(now):
    normalized = aware_time(utc_iso(now))
    return ANCHOR + ((normalized - ANCHOR) // WEEK) * WEEK


def weekly_intent(cutoff):
    return {"schema_version": 1, "intent_id": "weekly_" + cutoff.strftime("%Y%m%dT%H%M%SZ"),
            "window_start": utc_iso(cutoff - WEEK), "cutoff": utc_iso(cutoff),
            "schedule_rules_sha256": hashlib.sha256(canonical_bytes(RULES)).hexdigest()}


def _cursor(settings, guard):
    path = _no_symlinks(settings.feedback_schedule_dir / "cursor.json")
    if not path.exists():
        return None
    cursor = read_protocol(path, guard)
    if type(cursor) is not dict or cursor.keys() != CURSOR_KEYS or type(cursor["schema_version"]) is not int or cursor["schema_version"] != 1:
        raise ValueError("unsupported weekly cursor")
    last, activation = aware_time(cursor["last_confirmed_cutoff"]), aware_time(cursor["activation_cutoff"])
    if last is None or activation is None or last < activation:
        raise ValueError("invalid weekly cursor cutoff")
    schedule_state.validate_intent(weekly_intent(last), last)
    receipt = schedule_state.validate_receipt(settings, cursor["last_receipt"])
    if receipt["cutoff"] != utc_iso(last):
        raise ValueError("cursor lacks confirmed report")
    return cursor


def commit_cursor(settings, cursor):
    schedule_state.replace_protocol(settings.feedback_schedule_dir / "cursor.json", cursor)


def _status(settings, value):
    schedule_state.replace_protocol(settings.feedback_schedule_dir / "status.json", value)
    return value


def _summary(cursor, status, completed, latest):
    last = cursor["last_confirmed_cutoff"] if cursor else None
    next_due = utc_iso(aware_time(last) + WEEK) if last else utc_iso(latest)
    return {"schema_version": 1, "status": status, "completed_windows": completed,
            "last_confirmed_cutoff": last, "next_due": next_due,
            "last_receipt": cursor["last_receipt"] if cursor else None}


async def _windows(settings, latest, configured, guard):
    cursor = _cursor(settings, guard)
    activation = (schedule_state.pending_cutoff(settings, guard) or latest) if cursor is None else None
    completed = 0
    status = "no_due"
    for _ in range(settings.feedback_schedule_max_windows):
        cutoff = activation if cursor is None else aware_time(cursor["last_confirmed_cutoff"]) + WEEK
        if cutoff > latest:
            break
        guard.check()
        # Reserve a whole fixed operation cap; remaining attempt clocks never enter content identity.
        if guard.deadline - time.monotonic() < configured["operation"]["wall_cap_seconds"]:
            status = "catchup_pending"
            break
        intent = weekly_intent(cutoff)
        previous = cursor["last_confirmed_cutoff"] if cursor else None
        receipt = schedule_state.recover(settings, intent, guard, previous_cutoff=previous)
        if receipt is None:
            receipt = operation._run_feedback_analysis(settings, cutoff=cutoff, provenance={}, budgets=configured,
                schedule_intent=intent, previous_cutoff=previous)
        # The synchronous dispatcher has drained its owned child before task cancellation is delivered.
        await asyncio.sleep(0)
        guard.check()
        status = receipt["status"]
        if cursor and receipt["source_identity"] == cursor["last_receipt"]["source_identity"]:
            status = "no_new_data"
        updated = {"schema_version": 1, "activation_cutoff": cursor["activation_cutoff"] if cursor else utc_iso(cutoff),
                   "last_confirmed_cutoff": utc_iso(cutoff), "last_receipt": receipt, "status": status}
        pending = _summary(cursor, status, completed, latest)
        pending.update(cursor_commit="pending", pending_receipt=receipt)
        _status(settings, pending)
        commit_cursor(settings, updated)
        cursor, completed = updated, completed + 1
        await asyncio.sleep(0)
    return _summary(cursor, status, completed, latest)


async def run_due_feedback(settings, *, now, budgets=None):
    schedule_state.main_thread_only()
    latest = closed_cutoff(now)
    if not settings.feedback_schedule_enabled:
        return _summary(None, "disabled", 0, latest)
    operation.admit_sources(settings)
    configured = operation.resolve_budgets(budgets)
    if settings.feedback_schedule_wall_cap_seconds <= configured["operation"]["wall_cap_seconds"]:
        raise ValueError("feedback schedule wall cap must exceed the fixed operation cap")
    root = schedule_state.private_root(settings)
    cycle_bounds = configured["operation"] | {"wall_cap_seconds": settings.feedback_schedule_wall_cap_seconds}
    guard = operation.OperationGuard(cycle_bounds)
    with operation._single_run(root), operation._signals():
        try:
            return await _windows(settings, latest, configured, guard)
        except BaseException as exc:
            failure = {"schema_version": 1, "status": "failure", "error_class": type(exc).__name__,
                       "cursor_path": str(root / "cursor.json"), "confirmation": "consult_cursor_and_immutable_receipts"}
            try:
                _status(settings, failure)
            except OSError as persistence_error:
                exc.schedule_status_persistence_error = type(persistence_error).__name__
            raise
