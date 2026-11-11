"""Keep a small pool of Today candidates analyzed ahead of the user's visit.

Preliminary ranking only decides what to analyze first; ``daily_select``'s
``recommendation`` decides what Today shows. One background thread prepares one
paper at a time through the existing deep-review worker and cache: headless
acquisition, no Zotero write, no visible browser. Current results, negative ones
included, are reused; a failing key is tried once per process. A process exit
loses only the paper in flight. Activation is ``quality_review.auto_on_tick_k``
(papers per run, 0 = off): a feed tick starts a run, and so does ``schedule_on_startup``
shortly after launch (``serve`` runs no tick loop, so Today would stay empty until a
manual batch). A run plans nothing when Today already has its target ready; the latch
keeps runs single-flight.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import LOGGER
from zotero_summarizer.services.library._flight import FlightLatch, run_in_background
from zotero_summarizer.services.triage.daily_select import ranked_unhandled_candidates
from zotero_summarizer.services.triage.daily_select._recommendation import recommendation, review_entry

TARGET_READY = 20   # eligible-or-pending items to keep ahead of Today (shows <= 15)
SCAN_LIMIT = 60     # never look deeper than this in the ranked pool
LOOKBACK_HOURS = 168
_POLL_SECONDS = 5
STARTUP_DELAY_SECONDS = 30  # let startup import/prewarm settle before the first run

_latch = FlightLatch()
_failed: set[str] = set()


def plan(candidates: list[dict], reviews: dict[str, Any], skip: set[str]) -> tuple[list[str], int]:
    """``(keys to analyze, eligible results seen)`` over the top of the ranked pool."""
    keys: list[str] = []
    ready = eligible_seen = 0
    for cand in candidates[:SCAN_LIMIT]:
        key = (cand.get("stable_feed_key") or "").strip()
        if not key or (cand.get("materialized_zotero_key") or "").strip():
            continue
        entry = review_entry(cand, reviews)
        if entry is not None:
            if recommendation(entry)["eligible"]:
                ready += 1
                eligible_seen += 1
        elif key not in skip:
            keys.append(key)
            ready += 1
        if ready == TARGET_READY:
            break
    return keys, eligible_seen


def status(db_path: Path) -> dict[str, Any]:
    """``ready``/``left`` come from the cache (any process); ``running`` is this process."""
    from zotero_summarizer.services.library import deep_review

    # Display counts use the same usable-review seam as the slate/members, so an
    # outdated-but-valid review counts as ready (not '0 ready, N not analyzed').
    # The prewarm run below stays current-only (it decides what to re-analyze).
    keys, eligible_seen = plan(
        ranked_unhandled_candidates(db_path, lookback_hours=LOOKBACK_HOURS),
        deep_review.usable_reviews(), set(),
    )
    return {"running": _latch.is_running(), "ready": eligible_seen, "left": len(keys)}


def start(db_path: Path, *, limit: int) -> bool:
    """Start one background run of at most ``limit`` new papers; False if busy or off."""
    if limit <= 0 or not _latch.try_start():
        return False
    run_in_background(lambda: _run(db_path, limit), name="today-preparation")
    return True


def schedule_on_startup(db_path: Path, *, limit: int, delay: float = STARTUP_DELAY_SECONDS) -> bool:
    """Start a run ``delay`` seconds after launch; False when off (``limit`` <= 0)."""
    if limit <= 0:
        return False

    def _start() -> None:
        try:
            if start(db_path, limit=limit):
                LOGGER.info("today preparation started at startup (limit=%d)", limit)
        except Exception:  # noqa: BLE001 — background-timer boundary; the next tick retries
            LOGGER.exception("today preparation startup start failed")

    timer = threading.Timer(delay, _start)
    timer.daemon = True
    timer.start()
    return True


def _run(db_path: Path, limit: int) -> None:
    error = None
    try:
        from zotero_summarizer.services.library import deep_review

        cands = ranked_unhandled_candidates(db_path, lookback_hours=LOOKBACK_HOURS)
        running = {key for cand in cands[:SCAN_LIMIT] if (key := cand.get("stable_feed_key"))
                   and deep_review.status(key)["status"] == "running"}
        keys = plan(cands, deep_review.current_reviews(), _failed | running)[0][:limit]
        counts = {"reviewed": 0, "unavailable": 0, "failed": 0}
        for key in keys:
            try:
                outcome = _prepare_one(db_path, key)
            except Exception:  # noqa: BLE001 — per-paper boundary; the run continues
                LOGGER.exception("today preparation failed key=%s", key)
                outcome = "failed"
            if outcome == "failed":
                _failed.add(key)
            counts[outcome] += 1
        LOGGER.info("today preparation: planned=%d %s", len(keys), counts)
    except Exception as exc:  # noqa: BLE001 — background-thread boundary
        LOGGER.exception("today preparation run failed")
        error = f"{type(exc).__name__}: {exc}"
    finally:
        _latch.finish(error)


def _prepare_one(db_path: Path, key: str) -> str:
    """Acquire headlessly, review on the existing worker, wait, classify the result."""
    from zotero_summarizer.services._common import state
    from zotero_summarizer.services.library import _pdf_acquire, deep_review
    from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
    from zotero_summarizer.services.library.review_eligibility import usable_review

    reader = AppLibraryReader(db_path)
    detail = reader.get_item_detail(key)
    if detail is None:
        return "failed"
    headless = bool(state().app_state.config.university_access.headless)
    acquired = _pdf_acquire.acquire_pdf_for(
        key, detail, allow_headed_fallback=False, allow_browser=headless)
    if acquired.path is not None and not acquired.web_article:
        overrides = {key: {"path": str(acquired.path), "source": acquired.source,
                           "source_url": acquired.source_url}}
        deep_review.start(item_keys=[key], pdf_overrides=overrides, reader=reader, background=True)
    else:  # publishes the existing needs_pdf placeholder; no model call
        deep_review.start(item_keys=[key], reader=reader, background=True)
    while deep_review.status(key)["status"] == "running":
        time.sleep(_POLL_SECONDS)
    if deep_review.status(key)["status"] == "error":
        return "failed"
    entry = deep_review.get_current_review(key)
    if entry is not None and entry.get("needs_pdf"):
        return "unavailable"
    return "reviewed" if usable_review(entry) else "failed"


__all__ = ["LOOKBACK_HOURS", "SCAN_LIMIT", "TARGET_READY", "plan", "schedule_on_startup", "start", "status"]
