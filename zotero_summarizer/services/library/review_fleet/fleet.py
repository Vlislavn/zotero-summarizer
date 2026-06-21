"""The review-fleet background job: pre-decide a reading verdict for the top-N picks.

Single-flight (its own ``FlightLatch``), and deliberately **serial**. For each of
the top-``top_k`` reading-queue picks it:

    1. reuses the cached deep review if present (``deep_review.get_cached_review``);
    2. otherwise triggers ``deep_review.start([key])`` and POLLS that job's status
       until it settles — one paper at a time, so we NEVER fire two model loads at
       once (RAM safety on the unified-memory Mac, matching ``deep_review``'s own
       local-serial rule);
    3. folds the cached digest + quality signals into a ``ProposedVerdict`` via the
       pure ``propose.propose_verdict`` (no LLM here) and ``verdict_store.upsert``s it.

It writes ONLY the proposed-verdict sidecar — never ``label_verdicts``, never
Zotero. The proposals are suggestions the human Confirms/Overrides later.

For a pick with no local Zotero PDF it ACQUIRES one via ``_pdf_acquire`` (arXiv/OA
headless, then the university browser for Cloudflare/SSO paywalls) into a local
cache and re-reviews FROM THAT PATH — never a Zotero write, so a verdict works while
Zotero is open.

``status()`` mirrors ``deep_review``'s poll shape and adds a per-outcome tally:
``{status, total, completed, proposed, no_fetchable_source, needs_library_login,
failed, error, started_at, progress}``. ``completed`` counts rows PROCESSED;
``proposed`` counts verdicts actually WRITTEN — so a run that proposed nothing is
``completed>0, proposed==0`` and surfaces as ``status="done_empty"`` (the honest
"decided nothing"), never a false ``ready``. ``no_fetchable_source`` = no PDF source
at all; ``needs_library_login`` = a proxied source exists but the browser isn't
logged in (actionable). A per-item failure is logged and the job moves on
(background-worker boundary); a job-level failure (no queue / no reader, surfaced by
the deep-review job) sets the status ``error``.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from zotero_summarizer.services._common import LOGGER, now_iso_z
from zotero_summarizer.services.library import _flight, deep_review, reading_queue
from zotero_summarizer.services.library.review_fleet import propose, verdict_store

_DEFAULT_TOP_K = 5

# Safety-net cap (NOT a per-call magic timeout): how long we wait for ONE paper's
# deep review to finish before giving up on it and moving to the next. A local
# full-tier review is minutes; this is the work-agnostic upper bound, env-free
# because the fleet is launch/owner-driven, not a tight loop.
_PER_ITEM_WAIT_SECS = 600.0
_POLL_INTERVAL_SECS = 2.0
# Rank the whole library when selecting undecided picks (matches the UI's
# QUEUE_LIMIT): the queue pins already-labeled papers to its top, so a fixed
# window is starved on a heavily-labeled library. _select_keys early-exits.
_SELECTION_SCAN_LIMIT = 5000

_LATCH = _flight.FlightLatch()
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "total": 0,
    "completed": 0,
    # Per-outcome tally so the UI can tell "did something" from "touched N rows but
    # decided nothing": `completed` counts rows processed; `proposed` counts verdicts
    # actually written. A run over PDF-less papers is completed>0, proposed==0.
    "proposed": 0,
    # No fetchable PDF source at all (web article / no arXiv / no OA copy).
    "no_fetchable_source": 0,
    # A proxied/paywalled source EXISTS but the browser couldn't fetch it because the
    # university profile isn't logged in (or the `browser` extra is missing) — actionable.
    "needs_library_login": 0,
    "failed": 0,
    "started_at": None,
    "progress": {},
}


def try_start() -> bool:
    """Claim the single-flight slot. ``False`` when a run is already in flight."""
    if not _LATCH.try_start():
        return False
    with _LOCK:
        _STATE["total"] = 0
        _STATE["completed"] = 0
        _STATE["proposed"] = 0
        _STATE["no_fetchable_source"] = 0
        _STATE["needs_library_login"] = 0
        _STATE["failed"] = 0
        _STATE["started_at"] = now_iso_z()
        _STATE["progress"] = {}
    return True


def finish(error: str | None = None) -> None:
    _LATCH.finish(error)
    with _LOCK:
        _STATE["progress"] = {}


def status() -> dict[str, Any]:
    """Poll payload ``{status, total, completed, proposed, skipped_no_fulltext,
    failed, error, started_at, progress}``.

    ``status`` is ``running`` while in flight, ``error`` after a job-level failure,
    ``done_empty`` when a run finished having PROCESSED rows but PROPOSED none (e.g.
    the picks had no full-text PDF — the honest "did nothing" the user sees, never
    masqueraded as ``ready``), ``ready`` once at least one verdict was proposed,
    else ``idle``."""
    running = _LATCH.is_running()
    error = _LATCH.last_error()
    with _LOCK:
        total = int(_STATE["total"])
        completed = int(_STATE["completed"])
        proposed = int(_STATE["proposed"])
        no_fetchable_source = int(_STATE["no_fetchable_source"])
        needs_library_login = int(_STATE["needs_library_login"])
        failed = int(_STATE["failed"])
        started_at = _STATE["started_at"]
        progress = dict(_STATE["progress"])
    if running:
        state = "running"
    elif error:
        state = "error"
    elif completed > 0 and proposed == 0:
        state = "done_empty"
    elif proposed > 0:
        state = "ready"
    else:
        state = "idle"
    return {
        "status": state,
        "total": total,
        "completed": completed,
        "proposed": proposed,
        "no_fetchable_source": no_fetchable_source,
        "needs_library_login": needs_library_login,
        "failed": failed,
        "error": error,
        "started_at": started_at,
        "progress": progress,
    }


def _set_progress(progress: dict[str, Any]) -> None:
    with _LOCK:
        _STATE["progress"] = progress


def _ensure_review(item_key: str, *, force: bool = False, pdf_path: str | None = None) -> dict[str, Any] | None:
    """Return the cached deep review for ``item_key``, computing it SERIALLY first
    if absent. Triggers ``deep_review.start([key])`` and polls its single-flight
    status until it settles (never a parallel model load), then re-reads the cache.

    ``force`` bypasses the cache and recomputes — used after a PDF was acquired, so
    the paper is re-reviewed with its new full text. ``pdf_path`` (a local cache
    download from the university/OA chain) is injected via ``deep_review``'s
    ``pdf_overrides`` so the review runs from that path with NO Zotero attachment.
    Returns the cached review dict, or ``None`` when the review never materialized
    (e.g. the paper has no PDF, or the deep-review job errored — via its status)."""
    if not force:
        cached = deep_review.get_cached_review(item_key)
        # Reuse only a USABLE cache: a real digest, or an honest needs_pdf (a
        # re-review without a PDF is futile). A digest-less, has-PDF entry is a
        # STALE FAILURE → fall through and recompute (deep review works now).
        if cached is not None and (cached.get("digest") is not None or cached.get("needs_pdf")):
            return cached

    # Two SEPARATE budgets so a long FOREIGN job can't starve OUR own review:
    # `foreign_waited` caps how long we wait for a foreign latch to free, `own_waited`
    # caps OUR review once we've claimed the slot. A foreign job that runs out almost
    # the whole window still leaves the full own-review budget when we re-claim.
    foreign_waited = own_waited = 0.0
    while True:
        # `accepted` is False when a FOREIGN deep-review job (the startup prewarm, or
        # the user's own "Run deeper review") holds the slot — the job we poll below
        # is NOT ours, so its settling must not be read as "our item is done" (the
        # old bug: every item reported 'failed').
        overrides = {item_key: pdf_path} if pdf_path else None
        accepted = bool(deep_review.start(item_keys=[item_key], pdf_overrides=overrides).get("accepted"))
        while True:
            dr_status = deep_review.status()
            # Merge onto the base {item_key, index, total} set by the caller so the UI
            # keeps "paper i of n" while it reports the cold paper's deep-review status.
            with _LOCK:
                merged = dict(_STATE["progress"])
            merged.update({"item_key": item_key, "deep_review": dr_status})
            _set_progress(merged)
            if dr_status["status"] != "running":
                break
            if (own_waited if accepted else foreign_waited) >= _PER_ITEM_WAIT_SECS:
                break  # this budget is spent
            time.sleep(_POLL_INTERVAL_SECS)
            if accepted:
                own_waited += _POLL_INTERVAL_SECS
            else:
                foreign_waited += _POLL_INTERVAL_SECS
        cached = deep_review.get_cached_review(item_key)
        if cached is not None:
            return cached
        if accepted:
            # OUR job ran but cached nothing for this item → it genuinely errored.
            # Retrying would just error again, so stop (caller records 'failed').
            return None
        if foreign_waited >= _PER_ITEM_WAIT_SECS:
            return None  # waited a full budget for the latch and it never freed
        # A foreign job settled without reviewing our item — wait a tick (so
        # `foreign_waited` always advances → termination) then re-claim the slot.
        time.sleep(_POLL_INTERVAL_SECS)
        foreign_waited += _POLL_INTERVAL_SECS


def _propose_for_item(item_key: str) -> str:
    """Compute + store the proposed verdict for one item, returning the OUTCOME:

      - ``"proposed"``             — a verdict was written;
      - ``"no_fetchable_source"``  — no usable full text and no fetchable PDF source
        (web article / no arXiv / no OA copy / download failed);
      - ``"needs_library_login"``  — a proxied/paywalled source exists but the browser
        couldn't fetch it (university profile not logged in / `browser` extra missing);
      - ``"failed"``               — the review never materialized (no review dict).

    When the first review has no usable full text, we ACQUIRE a PDF (arXiv/OA headless,
    then the university browser) into a local cache and re-review FROM THAT PATH — no
    Zotero write, so it works while Zotero is open. The caller tallies the outcomes."""
    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise

    review = _ensure_review(item_key)
    if review is None or review.get("needs_pdf") or review.get("digest") is None:
        detail = get_zotero_reader_or_raise().get_item_detail(item_key) or {}
        # Only acquire when there's no local Zotero PDF — a has_pdf item with no digest
        # is a genuine extraction failure, not a missing source.
        if not detail.get("has_pdf"):
            result = _pdf_acquire.acquire_pdf_for(item_key, detail)
            if result.path is not None:
                review = _ensure_review(item_key, force=True, pdf_path=str(result.path))
            elif result.needs_login:
                return "needs_library_login"
    if review is None:
        return "failed"
    if review.get("needs_pdf") or review.get("digest") is None:
        return "no_fetchable_source"
    proposal = propose.propose_verdict(
        review.get("digest"),
        review.get("quality"),
        goal_summaries=review.get("goal_summaries"),
    )
    verdict_store.upsert(item_key, proposal.model_dump())
    return "proposed"


def _select_keys(queue: dict[str, Any], top_k: int) -> list[str]:
    """The next ``top_k`` reading-queue item_keys that are still UNDECIDED — neither
    already proposed by the fleet (``proposed_verdict``) nor labeled by the human
    (``user_priority``). Skipping the decided ones is what makes a re-run ADVANCE
    down the queue instead of re-chewing the same top picks. ``dont_read`` rows are
    already excluded upstream (they are "handled" in ``build_reading_queue``)."""
    keys: list[str] = []
    for row in queue.get("items") or []:
        if row.get("proposed_verdict") or row.get("user_priority"):
            continue
        key = str(row.get("item_key") or "")
        if key:
            keys.append(key)
        if len(keys) >= top_k:
            break
    return keys


def _run_job(top_k: int) -> None:
    """Background worker: serially pre-decide the next ``top_k`` UNDECIDED picks."""
    try:
        # Scan the WHOLE ranked library, not a fixed prefix: the queue PINS the
        # user's already-labeled papers to its top, so on a heavily-labeled library
        # a small window is all-decided and selects ZERO undecided picks (the fleet
        # silently no-ops). _select_keys early-exits after top_k, so a big ranked
        # list is ~free; this matches the UI's QUEUE_LIMIT.
        queue = reading_queue.build_reading_queue(limit=_SELECTION_SCAN_LIMIT)
        keys = _select_keys(queue, top_k)
        with _LOCK:
            _STATE["total"] = len(keys)
            _STATE["completed"] = 0
        for i, item_key in enumerate(keys):
            # Base progress (paper i of n) BEFORE the work, so a cold paper's long
            # deep review reports its position; _ensure_review then merges in the
            # live deep_review status under the same dict.
            _set_progress({"item_key": item_key, "index": i + 1, "total": len(keys)})
            try:
                outcome = _propose_for_item(item_key)
            except Exception as exc:  # noqa: BLE001 — per-item background boundary
                LOGGER.warning("review_fleet failed item=%s: %s", item_key, exc)
                outcome = "failed"
            with _LOCK:
                _STATE["completed"] += 1
                _STATE[outcome] += 1
        finish(error=None)
    except Exception as exc:  # noqa: BLE001 — background-worker boundary
        LOGGER.exception("review_fleet job crashed")
        finish(error=f"{type(exc).__name__}: {exc}")


def start(top_k: int = _DEFAULT_TOP_K) -> dict[str, Any]:
    """Kick off a review-fleet run (single-flight). Pre-decides the top-``top_k``
    Read-next picks in the background, serially. Returns the current ``status()``;
    a no-op (returns the in-flight status) when a run is already going."""
    if not try_start():
        return status()
    _flight.run_in_background(lambda: _run_job(max(1, top_k)))
    return status()


__all__ = ["start", "status", "try_start", "finish"]
