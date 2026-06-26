"""The review-fleet background job: pre-decide a reading verdict for the top-N picks.

Single-flight (its own ``FlightLatch``). It hands the picks to ``deep_review`` in
**one batched call** and lets ``deep_review``'s own provider-aware fan-out decide
concurrency: **parallel for a remote/API provider, serial for a local one**
(``deep_review_fleet_concurrency`` — a remote batch fans out, capped by the provider's
``max_sub_concurrency``, while one on-device model is never asked to serve concurrent
inference and thrash host RAM). The fleet runs three passes:

    1. **review** the picks that lack a usable cached deep review — one
       ``deep_review.start(keys)`` call, polled until it settles;
    2. **acquire** a PDF for any pick still without usable full text (no local
       Zotero attachment) — SEQUENTIALLY via ``_pdf_acquire`` (arXiv/OA headless,
       then the university browser for Cloudflare/SSO paywalls; a stateful browser
       session, never parallelized) — then one batched re-review FROM THAT PATH;
    3. **propose** — fold each cached digest + quality signals into a
       ``ProposedVerdict`` via the pure ``propose.propose_verdict`` (no LLM here)
       and ``verdict_store.upsert`` it.

It writes ONLY the proposed-verdict sidecar — never ``label_verdicts``, never
Zotero. The proposals are suggestions the human Confirms/Overrides later. The
acquired PDF goes to a local cache, never a Zotero write, so a verdict works while
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

# Safety-net cap (NOT a per-call magic timeout): how long we wait for ONE batched
# deep-review pass to finish before giving up and moving on. A local full-tier
# review is minutes per paper and a local batch runs serially, so this is the
# work-agnostic upper bound, env-free because the fleet is launch/owner-driven.
_PER_BATCH_WAIT_SECS = 600.0
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
    # session for that publisher is stale/absent — actionable.
    "needs_library_login": 0,
    # The gated picks as ``[{item_key, title, url}]`` — the UI surfaces each as a
    # clickable link the user opens to sign in (refreshing the session), then re-Predicts.
    "needs_login_items": [],
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
        _STATE["needs_login_items"] = []
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
        needs_login_items = list(_STATE["needs_login_items"])
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
        "needs_login_items": needs_login_items,
        "failed": failed,
        "error": error,
        "started_at": started_at,
        "progress": progress,
    }


def _set_progress(progress: dict[str, Any]) -> None:
    with _LOCK:
        _STATE["progress"] = progress


def _usable_cache(item_key: str) -> dict[str, Any] | None:
    """The cached deep review for ``item_key`` IF it's usable — a real digest, or an
    honest ``needs_pdf`` (a re-review without a PDF is futile). A digest-less,
    has-PDF entry is a STALE FAILURE → ``None`` so it's recomputed (deep review works
    now). ``None`` when absent or stale."""
    cached = deep_review.get_cached_review(item_key)
    if cached is not None and (cached.get("digest") is not None or cached.get("needs_pdf")):
        return cached
    return None


def _has_digest(item_key: str) -> bool:
    """True once ``item_key`` carries a real digest — the terminal state a re-review
    aims for, so a re-acquired batch stops asking for it."""
    cached = deep_review.get_cached_review(item_key)
    return cached is not None and cached.get("digest") is not None


def _run_batched_review(item_keys: list[str], *, overrides: dict[str, str] | None = None, force: bool = False) -> None:
    """Review a BATCH of items in ONE ``deep_review.start`` call, polled until OUR
    accepted job settles. ``deep_review`` fans the batch out provider-aware — parallel
    for a remote provider, serial (one model load at a time) for a local one — so this
    is where "parallel for API, serial for local" actually lands. PDF acquisition is
    NOT here (the caller does that sequentially); ``overrides`` injects already-acquired
    cache paths via ``pdf_overrides``.

    Honors the FOREIGN latch: when a foreign deep-review job (the startup prewarm, or
    the user's own "Run deeper review") holds the single-flight slot, ``accepted`` is
    False — we wait for it to drain then RE-CLAIM for the keys it didn't finish, never
    reading the foreign job's settle as "our items failed" (the old per-item bug). Two
    SEPARATE budgets so a long foreign job can't starve our own pass. Best-effort:
    leaves whatever it couldn't review uncached for the propose pass to tally."""
    if not item_keys:
        return
    done = _has_digest if force else (lambda k: _usable_cache(k) is not None)
    foreign_waited = own_waited = 0.0
    with _LOCK:
        display_total = int(_STATE["total"]) or len(item_keys)
    while True:
        # Re-filter each round: a foreign job may have finished some of our keys while
        # we waited — don't redo its work. `force` keeps needs_pdf keys pending so the
        # re-acquired PDF is actually reviewed.
        pending = [k for k in item_keys if not done(k)]
        if not pending:
            return
        ov = {k: overrides[k] for k in pending if overrides and k in overrides} or None
        accepted = bool(deep_review.start(item_keys=pending, pdf_overrides=ov).get("accepted"))
        while True:
            dr_status = deep_review.status()
            with _LOCK:
                merged = dict(_STATE["progress"])
            merged.update({"index": int(dr_status.get("completed") or 0), "total": display_total, "deep_review": dr_status})
            _set_progress(merged)
            if dr_status["status"] != "running":
                break
            if (own_waited if accepted else foreign_waited) >= _PER_BATCH_WAIT_SECS:
                break  # this budget is spent
            time.sleep(_POLL_INTERVAL_SECS)
            if accepted:
                own_waited += _POLL_INTERVAL_SECS
            else:
                foreign_waited += _POLL_INTERVAL_SECS
        if accepted:
            return  # OUR batch ran (settled or budget spent) — propose pass reads the cache
        if foreign_waited >= _PER_BATCH_WAIT_SECS:
            return  # waited a full budget for the latch and it never freed
        # A foreign job settled without finishing our keys — wait a tick (so
        # `foreign_waited` always advances → termination) then re-claim the slot.
        time.sleep(_POLL_INTERVAL_SECS)
        foreign_waited += _POLL_INTERVAL_SECS


def _acquire_missing_pdfs(keys: list[str], outcomes: dict[str, str]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """SEQUENTIALLY acquire a PDF for each pick still without usable full text (no
    local Zotero attachment), returning ``({key: local_path}, {key: {title, url}})``.
    The second map is the GATED picks — a real PDF exists but the session for that
    publisher is stale/absent; its ``url`` is surfaced as a clickable sign-in link.
    Acquisition drives a stateful university browser session, so it is never
    parallelized regardless of provider. A pick with a Zotero PDF but no digest is a
    genuine extraction failure, not a missing source — skipped. A per-key acquisition
    failure is isolated to that key (recorded ``failed`` in ``outcomes``)."""
    from zotero_summarizer.services.library import _pdf_acquire
    from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise

    overrides: dict[str, str] = {}
    login: dict[str, dict[str, str]] = {}
    for item_key in keys:
        if item_key in outcomes:
            continue
        review = deep_review.get_cached_review(item_key)
        if not (review is None or review.get("needs_pdf") or review.get("digest") is None):
            continue  # already has a digest — nothing to acquire
        try:
            detail = get_zotero_reader_or_raise().get_item_detail(item_key) or {}
            if detail.get("has_pdf"):
                continue  # has a local PDF but no digest → extraction failure, not a missing source
            result = _pdf_acquire.acquire_pdf_for(item_key, detail)
            if result.path is not None:
                overrides[item_key] = str(result.path)
            elif result.needs_login:
                doi = str(detail.get("doi") or "")
                login[item_key] = {
                    "title": str(detail.get("title") or item_key),
                    # the page to open + sign into (refreshing the session the fetch reuses)
                    "url": str(detail.get("url") or (f"https://doi.org/{doi}" if doi else "")),
                }
        except Exception as exc:  # noqa: BLE001 — per-item background boundary
            LOGGER.warning("review_fleet acquire failed item=%s: %s", item_key, exc)
            outcomes[item_key] = "failed"
    return overrides, login


def _propose_for_item(item_key: str, *, login: dict[str, dict[str, str]]) -> str:
    """Fold the cached review into a stored proposal, returning the OUTCOME:

      - ``"proposed"``             — a verdict was written;
      - ``"no_fetchable_source"``  — no usable full text and no fetchable PDF source
        (web article / no arXiv / no OA copy / download failed);
      - ``"needs_library_login"``  — a proxied/paywalled source exists but the browser
        couldn't fetch it (university profile not logged in / `browser` extra missing);
      - ``"failed"``               — the review never materialized (no review dict).

    Pure read of the cache the review/acquire passes populated — no LLM, no model load."""
    review = deep_review.get_cached_review(item_key)
    if review is None or review.get("needs_pdf") or review.get("digest") is None:
        if item_key in login:
            return "needs_library_login"
        if review is None:
            return "failed"
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


def _run_job(top_k: int, item_keys: list[str] | None = None) -> None:
    """Background worker: pre-decide picks in three passes (review →
    acquire-and-re-review → propose). With ``item_keys`` it reviews EXACTLY those
    (the client's pinned "Review cool papers" set — so the fleet targets the SAME
    rows the UI counts, never a band-agnostic ``_select_keys`` slice that buries the
    cool stragglers behind higher-blended could-read rows); otherwise the next
    ``top_k`` UNDECIDED picks. The two review passes are batched into ``deep_review``,
    which fans them out parallel for a remote provider / serial for a local one; PDF
    acquisition between them stays sequential."""
    try:
        if item_keys:
            # Client-pinned cool set: review exactly these (no queue scan / selector).
            keys = [str(k) for k in item_keys]
        else:
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

        # PASS 1 — review the picks without a usable cached deep review, in one batched
        # provider-aware call. A per-key cache read that blows up is isolated here so
        # one bad pick can't sink the whole batch (recorded `failed` for the propose pass).
        outcomes: dict[str, str] = {}
        to_review: list[str] = []
        for item_key in keys:
            try:
                if _usable_cache(item_key) is None:
                    to_review.append(item_key)
            except Exception as exc:  # noqa: BLE001 — per-item background boundary
                LOGGER.warning("review_fleet partition failed item=%s: %s", item_key, exc)
                outcomes[item_key] = "failed"
        _run_batched_review(to_review)

        # PASS 2 — acquire a PDF (sequentially) for picks still without full text, then
        # one batched re-review FROM the acquired paths (force: a needs_pdf cache is not
        # "done" until the new PDF yields a digest).
        overrides, login = _acquire_missing_pdfs(keys, outcomes)
        _run_batched_review(list(overrides), overrides=overrides, force=True)

        # PASS 3 — propose from the cache the review passes populated (pure, no LLM).
        for item_key in keys:
            outcome = outcomes.get(item_key)
            if outcome is None:
                try:
                    outcome = _propose_for_item(item_key, login=login)
                except Exception as exc:  # noqa: BLE001 — per-item background boundary
                    LOGGER.warning("review_fleet propose failed item=%s: %s", item_key, exc)
                    outcome = "failed"
            with _LOCK:
                _STATE["completed"] += 1
                _STATE[outcome] += 1
        # Surface the gated picks (with a sign-in link each) so the UI can show
        # "open, log in, then re-Predict" — only those with a URL to open.
        with _LOCK:
            _STATE["needs_login_items"] = [
                {"item_key": k, "title": v["title"], "url": v["url"]}
                for k, v in login.items() if v.get("url")
            ]
        finish(error=None)
    except Exception as exc:  # noqa: BLE001 — background-worker boundary
        LOGGER.exception("review_fleet job crashed")
        finish(error=f"{type(exc).__name__}: {exc}")


def start(top_k: int = _DEFAULT_TOP_K, *, item_keys: list[str] | None = None) -> dict[str, Any]:
    """Kick off a review-fleet run (single-flight). With ``item_keys`` it pre-decides
    EXACTLY those picks (the client's "Review cool papers" passes its cool — must/
    should-read — set so the fleet reviews the SAME rows the UI counts, not the
    band-agnostic top-of-undecided ``_select_keys`` slice); without it, the next
    ``top_k`` undecided picks (the startup prewarm path). Deep review is batched
    parallel-for-remote / serial-for-local.

    Returns ``status()`` plus an ``accepted`` flag: ``True`` when THIS call claimed the
    single-flight slot (our picks are now running), ``False`` when a run was already in
    flight (a prewarm / another click) so this call is a no-op returning the FOREIGN
    run's status. The client relies on ``accepted`` (not a started_at timestamp) to tell
    "my pinned keys are running" from "a foreign run holds the latch — wait it out", which
    is robust to a prewarm that fires AFTER the click."""
    if not try_start():
        return {**status(), "accepted": False}
    keys = [str(k) for k in item_keys] if item_keys else None
    _flight.run_in_background(lambda: _run_job(max(1, top_k), item_keys=keys))
    return {**status(), "accepted": True}


__all__ = ["start", "status", "try_start", "finish"]
