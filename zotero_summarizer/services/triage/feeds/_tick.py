"""feeds: one daemon tick — triage K unread items, mark read, side-work.

The primary daemon iteration: round-robin pick, dedup, classifier gate, LLM
triage, record decisions, mark read in Zotero, resolve due outcomes, and fire
daily selection when due. Each phase lives in :mod:`_tick_phases`; this module
is the thin orchestrator that sequences them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    DaemonTickReport,
    TriagedCandidate,
    _load_config,
    _tick_lock,
)
from zotero_summarizer.services.triage.feeds._gate import (
    _apply_classifier_gate,
    _maybe_schedule_gate_retrain,
)
from zotero_summarizer.services.triage.feeds._outcomes import _resolve_due_outcomes
from zotero_summarizer.services.triage.feeds._tick_dedup import (
    dedup_against_library,
    dedup_against_processed,
)
from zotero_summarizer.services.triage.feeds._tick_phases import (
    _TickResults,
    mark_processed_read,
    maybe_run_daily,
    pick_and_log,
    prepare_unprocessed,
    record_tick_decisions,
    run_triage_stage,
)
from zotero_summarizer.services.triage.feeds._rescue import recover_abstractless_rescues
from zotero_summarizer.services.triage.feeds._rescue_l1 import (
    recover_abstractless_l1_candidates,
)
from zotero_summarizer.services.triage.feeds._tick_setup import resolve_tick_adapters
from zotero_summarizer.services.triage.feeds._zotero_readsync import sync_zotero_read_state


@dataclass(frozen=True)
class _TickFlags:
    dedup_enabled: bool
    processed_dedup_enabled: bool
    mark_processed_as_read: bool
    zotero_read_sync: bool
    outcome_check_per_tick: int
    exclude_feed_names: set[str]


def _resolve_tick_flags(feeds_cfg: dict[str, Any]) -> _TickFlags:
    """Derive the per-tick dedup / mark-read / outcome flags from feeds config.

    ``dedup_against_processed`` defaults to the library-dedup flag (so a config
    that turned off duplicate protection stays off) but is independently
    switchable — ``None`` (the ``FeedsConfig`` default) means "follow". Non-paper
    feeds (e.g. GitHub releases) the user marked not-scholarly never enter
    triage (so never get materialised/scored)."""
    dedup_enabled = bool(feeds_cfg.get("dedup_against_library", True))
    processed_dedup_raw = feeds_cfg.get("dedup_against_processed")
    return _TickFlags(
        dedup_enabled=dedup_enabled,
        processed_dedup_enabled=dedup_enabled if processed_dedup_raw is None else bool(processed_dedup_raw),
        mark_processed_as_read=bool(feeds_cfg.get("mark_processed_as_read", True)),
        zotero_read_sync=bool(feeds_cfg.get("zotero_read_sync", True)),
        outcome_check_per_tick=int(feeds_cfg.get("outcome_check_per_tick", 3)),
        exclude_feed_names={
            str(name).strip().casefold()
            for name in (feeds_cfg.get("exclude_feeds") or [])
            if str(name).strip()
        },
    )




def _maybe_refresh_app_rss(reader: ZoteroReader, feeds_cfg: dict[str, Any], tick_id: str) -> None:
    """Refresh app-RSS feeds in place before picking, when the reader supports it.

    Keys are the flat ``FeedsConfig`` fields (the nested ``startup_refresh``/
    ``refresh`` dicts were unset everywhere and are gone). The refresh itself
    rotates least-recently-fetched-first, so a bounded pass covers every
    enabled feed across successive ticks."""
    if hasattr(reader, "refresh_feeds"):
        max_feeds = int(feeds_cfg.get("max_feeds_per_pass", 10))
        max_new = int(feeds_cfg.get("max_new_items_per_feed", 25))
        timeout = float(feeds_cfg.get("per_feed_timeout_secs", 10.0))
        refreshed = reader.refresh_feeds(
            max_feeds=max_feeds,
            max_new_items_per_feed=max_new,
            per_feed_timeout=timeout,
        )
        LOGGER.info("[%s] app RSS refresh: %s", tick_id, refreshed)


def _maybe_rescue_l1(
    triaged_results: list[tuple[dict[str, Any], TriagedCandidate]],
    tick_id: str,
    gate_only: bool,
) -> list[tuple[dict[str, Any], TriagedCandidate]]:
    """Acquire-before-score for L1-hide candidates (G10); no-op in gate_only (no LLM)."""
    if not gate_only:
        from zotero_summarizer.services.library import quality_gate
        try:
            _g_enabled, _g_floor, _g_grades, _g_bands = quality_gate._gate_config()
            if _g_enabled:
                triaged_results, _n_rescued = recover_abstractless_l1_candidates(
                    triaged_results, tick_id=tick_id, llm_floor=_g_floor,
                )
        except (ValueError, AttributeError, OSError) as exc:  # config/I/O only — bugs propagate
            LOGGER.warning("[%s] recover-abstract-l1 skipped: %s", tick_id, exc)
    return triaged_results


def _maybe_auto_review(tick_id: str, materialized_keys: list[str]) -> None:
    """Fire the full-text quality review for the just-materialized Today picks.

    This is the fix for "I never see the deep review during triage": before this
    hook, ``deep_review`` only ran at startup (prewarm) or on a manual button/API
    trigger. Daily selection has just materialized these items into Zotero, so
    their PDFs are fetchable and their library keys are real — the first point a
    full-text review can run. Reuses ``deep_review.start`` (single-flight: skips
    already-running + cached; provider-aware pool: parallel on a remote provider,
    queued on a local one) — the SAME ``assess_digest`` path the per-paper button
    and the library use, not a second review. Fire-and-forget: ``start`` submits
    to its pool and returns, so the tick is never blocked.

    Gated by ``quality_review.auto_on_tick_k`` (0 disables). Capped to that many
    keys per tick. A failure to *submit* is a daemon-tick boundary (logged, never
    crashes the tick); per-paper review failures are recorded on each item's job
    by the deep_review worker, not here.
    """
    config = _load_config()
    qr = config.get("quality_review") or {}
    k = int(qr.get("auto_on_tick_k") or 0)
    if k <= 0 or not materialized_keys:
        return
    keys = [str(key).strip() for key in materialized_keys if str(key).strip()]
    if not keys:
        return
    if len(keys) > k:
        keys = keys[:k]
    try:
        from zotero_summarizer.services.library import deep_review

        deep_review.start(item_keys=keys)
        LOGGER.info("[%s] auto-review: submitted %d materialized pick(s) for full-text review", tick_id, len(keys))
    except Exception:
        LOGGER.exception("[%s] auto-review submit failed", tick_id)


def _auto_review_slate(tick_id: str) -> None:
    """In-place full-text review of the TOP Today slate candidates so Today cards show a
    real quality grade — WITHOUT a Zotero write (the user asked: review in place, persist
    if later added). Resolves each candidate by its ``stable_feed_key`` (``AppLibraryReader``,
    decision-independent), acquires a PDF into the local cache, and reviews on the shared
    deep_review pool (provider-aware: parallel remote, queued local). The review caches under
    ``stable_feed_key`` — the Today quality bridge joins on it, and ``add_to_library`` copies it
    onto the new library key. Already-cached feed keys are skipped so re-ticks stay cheap.
    Gated by ``quality_review.auto_on_tick_k`` (0 disables). Daemon-tick boundary: a failure is
    logged, never crashes the tick."""
    config = _load_config()
    k = int((config.get("quality_review") or {}).get("auto_on_tick_k") or 0)
    if k <= 0:
        return
    try:
        from zotero_summarizer.services._common import rank_quality_first_enabled, settings
        from zotero_summarizer.services.library import deep_review
        from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
        from zotero_summarizer.services.triage.daily_select import assemble_daily_slate

        db_path = settings().triage_db_path
        slate = assemble_daily_slate(db_path=db_path, K=k, quality_first=rank_quality_first_enabled())  # explicit arm: the P3 interleave is user-facing-GET only — a daemon merge must never claim the day's interleave_log nor widen auto-review beyond the shipped arm (README)
        done = deep_review.current_review_keys()
        keys = [p.stable_feed_key for p in slate.papers
                if p.stable_feed_key and p.stable_feed_key not in done][:k]
        if not keys:
            return
        deep_review.start(item_keys=keys, acquire_missing=True, reader=AppLibraryReader(db_path))
        LOGGER.info("[%s] auto-review (in-place): submitted %d slate pick(s)", tick_id, len(keys))
    except Exception:
        LOGGER.exception("[%s] in-place auto-review submit failed", tick_id)


def _auto_render_slate(tick_id: str) -> None:
    """Build the full, heavy paper RENDER (notes.md / presentation.html / figures) for the
    TOP-``render_on_tick_k`` Today feed papers, so a top feed paper opens with the same brief
    as a library item. Renders only keys with a current review contract (so the
    brief folds the cached review in, one tick after ``_auto_review_slate``, never racing it)
    and NOT already rendered. Reuses ``paper_render.start_build`` (its own pool + single-flight;
    the PDF is a cache hit from the review, and the digest is cached → NO extra LLM). Resolves
    the feed key via the key-aware reader (``paper_render._item_detail`` → ``resolve_reader_for_key``).
    Gated by ``quality_review.render_on_tick_k`` (0 disables). Daemon-tick boundary: a failure is
    logged, never crashes the tick."""
    config = _load_config()
    k = int((config.get("quality_review") or {}).get("render_on_tick_k") or 0)
    if k <= 0:
        return
    try:
        from zotero_summarizer.services._common import rank_quality_first_enabled, settings
        from zotero_summarizer.services.library import deep_review, paper_render
        from zotero_summarizer.services.triage.daily_select import assemble_daily_slate

        db_path = settings().triage_db_path
        slate = assemble_daily_slate(db_path=db_path, K=max(k, 5), quality_first=rank_quality_first_enabled())  # explicit arm — bypasses the P3 interleave (see _auto_review_slate)
        reviewed = deep_review.current_review_keys()
        # Top-K by composite rank, restricted to reviewed feed keys (so the brief has the
        # review folded in). slate.papers is role-grouped, so sort by composite_score.
        ranked = sorted(
            (p for p in slate.papers if p.stable_feed_key and p.stable_feed_key in reviewed),
            key=lambda p: p.composite_score, reverse=True,
        )
        submitted = 0
        for paper in ranked[:k]:
            state = paper_render._read_state(paper.stable_feed_key)
            if state is not None and state.get("status") == "completed":
                continue  # already rendered — skip (self-heals a failed/missing one)
            paper_render.start_build(paper.stable_feed_key, allow_acquire_missing=True)
            submitted += 1
        if submitted:
            LOGGER.info("[%s] auto-render: submitted %d top feed paper(s)", tick_id, submitted)
    except Exception:
        LOGGER.exception("[%s] auto-render submit failed", tick_id)


def _maybe_auto_quality_gate(tick_id: str, dry_run: bool) -> None:
    """Hide bad-quality shown rows (precision mode); skipped in dry_run. See quality_gate."""
    if not dry_run:
        try:
            from zotero_summarizer.services.library import quality_gate
            hidden = quality_gate.fire_full()
            if hidden:
                LOGGER.info("[%s] auto quality-gate hid %d bad-quality row(s)", tick_id, hidden)
        except Exception:
            LOGGER.exception("[%s] auto quality-gate fire_full failed", tick_id)


@_tick_lock()
def run_daemon_tick(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    feed_library_ids: list[int] | None = None,
    batch_size: int | None = None,
    force_daily_selection: bool = False,
    allow_daily_selection: bool = True,
    dry_run: bool = False,
    review_mode: bool | None = None,
    gate_only: bool = False,
    triage_llm: Any | None = None,
) -> DaemonTickReport:
    """Score a feed pass; dry-run preserves decisions, read state and subscriptions.

    None means all unread; the CLI/daemon supply their configured finite batch.
    The service lock covers selection through persistence for every caller.
    Gate-only defaults to review mode unless explicitly overridden.
    """
    if batch_size is not None and batch_size <= 0:
        raise ValueError("batch_size must be positive or None")
    if review_mode is None:
        review_mode = bool(gate_only)
    started = time.perf_counter()
    tick_id = feeds_storage.new_run_id(prefix="tick")
    feeds_cfg = _load_config()["feeds"]
    flags = _resolve_tick_flags(feeds_cfg)
    reader, writer, zotero_reader = resolve_tick_adapters(reader, writer, tick_id=tick_id)
    if not dry_run:
        _maybe_refresh_app_rss(reader, feeds_cfg, tick_id)
    raw = pick_and_log(
        reader, batch_size=batch_size, feed_library_ids=feed_library_ids,
        exclude_feed_names=flags.exclude_feed_names, tick_id=tick_id,
    )
    unprocessed, skipped, stale_to_mark = prepare_unprocessed(raw, tick_id=tick_id, dry_run=dry_run)
    unprocessed, processed_dupes = dedup_against_processed(
        unprocessed, tick_id=tick_id, enabled=flags.processed_dedup_enabled,
    )
    to_triage, library_dupes = dedup_against_library(
        unprocessed, reader=zotero_reader, tick_id=tick_id, enabled=flags.dedup_enabled,
    )
    if not dry_run:
        _maybe_schedule_gate_retrain(tick_id)
    to_triage, gate_rejected = _apply_classifier_gate(tick_id, to_triage, gate_only=gate_only)
    rescued = []
    if not gate_only:
        rescued, gate_rejected = recover_abstractless_rescues(gate_rejected, tick_id=tick_id)
    triaged, fast_rejected, errors, fatal = run_triage_stage(
        to_triage, tick_id=tick_id, gate_only=gate_only, triage_llm=triage_llm,
    )
    triaged = _maybe_rescue_l1(rescued + triaged, tick_id, gate_only)
    results = _TickResults(triaged=triaged, fast_rejected=fast_rejected, errors=errors,
                           gate_rejected=gate_rejected, library_skipped=library_dupes,
                           processed_dup_skipped=processed_dupes)
    report = DaemonTickReport(
        tick_id=tick_id, fetched=len(raw), skipped_already_processed=skipped,
        skipped_processed_dedup=len(processed_dupes), skipped_library_dedup=len(library_dupes),
        triaged=len(triaged), fast_rejected=len(fast_rejected), gate_rejected=len(gate_rejected),
        errors=len(errors), fatal_llm_error=fatal, marked_read=0, outcomes_resolved=0,
    )
    if not dry_run:
        record_tick_decisions(results, tick_id=tick_id, review_mode=review_mode)
        if flags.mark_processed_as_read and not review_mode:
            report.marked_read = mark_processed_read(results, stale_to_mark, writer=writer, tick_id=tick_id)
        if flags.zotero_read_sync and not review_mode:
            sync_zotero_read_state(zotero_reader=zotero_reader, writer=writer, tick_id=tick_id)
        if flags.outcome_check_per_tick > 0 and zotero_reader is not None:
            report.outcomes_resolved = _resolve_due_outcomes(reader=zotero_reader, limit=flags.outcome_check_per_tick)
    materialized_keys = []
    if not review_mode and allow_daily_selection:
        report.daily_selection_ran, report.daily_materialized, report.daily_rejected, materialized_keys = maybe_run_daily(
            feeds_cfg, reader=reader, writer=writer, tick_id=tick_id,
            feed_library_ids=feed_library_ids, force=force_daily_selection, dry_run=dry_run,
        )
    if not dry_run:
        if materialized_keys:
            _maybe_auto_review(tick_id, materialized_keys)
        _auto_review_slate(tick_id)
        _auto_render_slate(tick_id)
        _maybe_auto_quality_gate(tick_id, dry_run=False)
    report.elapsed_seconds = time.perf_counter() - started
    LOGGER.info("[%s] tick done: %s", tick_id, report.as_dict())
    return report
