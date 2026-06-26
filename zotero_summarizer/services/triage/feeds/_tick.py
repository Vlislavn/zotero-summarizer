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
    _load_config,
    get_settings,
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
    recover_abstractless_rescues,
    run_triage_stage,
)


@dataclass(frozen=True)
class _TickFlags:
    dedup_enabled: bool
    processed_dedup_enabled: bool
    mark_processed_as_read: bool
    outcome_check_per_tick: int
    exclude_feed_names: set[str]


def _resolve_tick_flags(feeds_cfg: dict[str, Any]) -> _TickFlags:
    """Derive the per-tick dedup / mark-read / outcome flags from feeds config.

    ``dedup_against_processed`` defaults to the library-dedup flag (so a config
    that turned off duplicate protection stays off) but is independently
    switchable. Non-paper feeds (e.g. GitHub releases) the user marked not-scholarly
    never enter triage (so never get materialised/scored)."""
    dedup_enabled = bool(feeds_cfg.get("dedup_against_library", True))
    return _TickFlags(
        dedup_enabled=dedup_enabled,
        processed_dedup_enabled=bool(feeds_cfg.get("dedup_against_processed", dedup_enabled)),
        mark_processed_as_read=bool(feeds_cfg.get("mark_processed_as_read", True)),
        outcome_check_per_tick=int(feeds_cfg.get("outcome_check_per_tick") or 3),
        exclude_feed_names={
            str(name).strip().casefold()
            for name in (feeds_cfg.get("exclude_feeds") or [])
            if str(name).strip()
        },
    )


def _build_tick_report(
    *,
    tick_id: str,
    results: _TickResults,
    fetched: int,
    skipped_already_processed: int,
    marked_read: int,
    outcomes_resolved: int,
    daily_ran: bool,
    daily_materialized: int,
    daily_rejected: int,
    fatal_llm_error: bool,
    elapsed_seconds: float,
) -> DaemonTickReport:
    """Assemble the tick report from the phase results and emit the summary log."""
    report = DaemonTickReport(
        tick_id=tick_id,
        fetched=fetched,
        skipped_already_processed=skipped_already_processed,
        skipped_processed_dedup=len(results.processed_dup_skipped),
        skipped_library_dedup=len(results.library_skipped),
        triaged=len(results.triaged),
        fast_rejected=len(results.fast_rejected),
        gate_rejected=len(results.gate_rejected),
        errors=len(results.errors),
        marked_read=marked_read,
        outcomes_resolved=outcomes_resolved,
        daily_selection_ran=daily_ran,
        daily_materialized=daily_materialized,
        daily_rejected=daily_rejected,
        fatal_llm_error=fatal_llm_error,
        elapsed_seconds=elapsed_seconds,
    )
    LOGGER.info(
        "[%s] tick done in %.2fs fetched=%d triaged=%d fast=%d gate=%d err=%d marked=%d outcomes=%d daily=%s",
        tick_id, elapsed_seconds, fetched, len(results.triaged), len(results.fast_rejected),
        len(results.gate_rejected), len(results.errors), marked_read, outcomes_resolved, daily_ran,
    )
    return report


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
    """One daemon iteration: triage K unread items + opportunistic side-work.

    Each tick:
      1. Pick K unread items round-robin across feeds (default K=5).
      2. Dedup against `processed_feed_items` (resumability) + library DOI.
      3. Triage each (corpus fast-reject -> LLM if not pre-rejected).
      4. Insert as `triaged_pending` (or `rejected_low_score` / etc).
      5. Mark all processed items read in Zotero (feedItems.readTime).
      6. Resolve up to `outcome_check_per_tick` due outcomes -> user_feedback.
      7. If 24h has elapsed since last daily-selection, run it now.

    Phase 1.14 — when ``review_mode=True``:
      * Triaged items land as ``awaiting_review`` (NOT ``triaged_pending``)
        so the daily-selection job ignores them.
      * Daily selection is force-skipped regardless of ``force_daily_selection``.
      * Mark-as-read is suppressed so items still appear in the Zotero RSS view.
      * SHAP attribution from the gate is persisted with each row.

    Phase 1.14 — when ``gate_only=True``:
      * The LLM triage loop is skipped entirely; each survivor of the gate
        gets a synthesised ``TriagedCandidate`` from its prediction.
      * ``review_mode`` is DECOUPLED: left unset it defaults to ``True`` (the
        label-bootstrap flow through the Review UI). The ML-first backlog drain
        passes ``review_mode=False`` so gate-only scores are written as
        ``triaged_pending`` and items are marked read — otherwise un-read
        survivors saturate the round-robin picker and the drain never drains.

    Returns the tick report (for logging / CLI display).
    """
    # Decoupled gate_only/review_mode: honor an explicit review_mode; only
    # default to review_mode=True for gate_only when the caller didn't specify.
    if review_mode is None:
        review_mode = bool(gate_only)
    start_ts = time.perf_counter()
    tick_id = feeds_storage.new_run_id(prefix="tick")
    config = _load_config()
    feeds_cfg = config["feeds"]

    reader = reader or ZoteroReader(get_settings().zotero_data_dir)
    writer = writer or ZoteroWriter(get_settings().zotero_data_dir)

    # batch_size semantics: None = unlimited (feeds run full-exhaust); int = bounded.
    effective_batch: int | None = batch_size
    flags = _resolve_tick_flags(feeds_cfg)
    LOGGER.info("[%s] tick start batch=%s", tick_id, effective_batch if effective_batch is not None else "unlimited")

    # 1. Pick unread items (round-robin when bounded, full-exhaust when None).
    raw = pick_and_log(
        reader, batch_size=effective_batch, feed_library_ids=feed_library_ids,
        exclude_feed_names=flags.exclude_feed_names, tick_id=tick_id,
    )
    fetched = len(raw)

    # 2. Dedup: same-RSS-item (identity), then same-paper-by-content (different
    #    GUID / re-post / already decided), then against the Zotero library.
    unprocessed, skipped_processed, stale_to_mark = prepare_unprocessed(raw, tick_id=tick_id)
    unprocessed, processed_dup_skipped = dedup_against_processed(
        unprocessed, tick_id=tick_id, enabled=flags.processed_dedup_enabled,
    )
    to_triage, library_skipped = dedup_against_library(
        unprocessed, reader=reader, tick_id=tick_id, enabled=flags.dedup_enabled,
    )

    # 2.5 Classifier gate (Phase 1.13) — fast-reject before the LLM; also kicks
    #     off a background retrain when the golden CSV's sha changed.
    _maybe_schedule_gate_retrain(tick_id)
    to_triage, gate_rejected = _apply_classifier_gate(tick_id, to_triage, gate_only=gate_only)

    # 2.6 Acquire-before-score rescue: prestige-journal RSS (Nature/Science/Cell)
    #     ships no real abstract, so the gate drops high-goal papers on boilerplate.
    #     Recover their full text + re-score before the verdict stands. Skipped in
    #     gate_only mode (no LLM available there).
    rescued: list[tuple[dict[str, Any], Any]] = []
    if not gate_only:
        rescued, gate_rejected = recover_abstractless_rescues(gate_rejected, tick_id=tick_id)

    # 3. Triage the gate survivors; merge in the rescued (already-scored) picks.
    triaged_results, fast_rejected_results, errors_results, fatal_seen = run_triage_stage(
        to_triage, tick_id=tick_id, gate_only=gate_only, triage_llm=triage_llm,
    )
    triaged_results = rescued + triaged_results
    results = _TickResults(
        triaged=triaged_results,
        fast_rejected=fast_rejected_results,
        errors=errors_results,
        gate_rejected=gate_rejected,
        library_skipped=library_skipped,
        processed_dup_skipped=processed_dup_skipped,
    )

    # 4. Record decisions.
    record_tick_decisions(results, tick_id=tick_id, review_mode=review_mode)

    # 5. Mark all processed items read in Zotero (skipped in review_mode/dry_run
    #    so they keep showing in the Zotero RSS view while the user decides).
    marked = 0
    if flags.mark_processed_as_read and not dry_run and not review_mode:
        marked = mark_processed_read(results, stale_to_mark, writer=writer, tick_id=tick_id)

    # 6. Resolve up to N due outcomes.
    outcomes = 0
    if flags.outcome_check_per_tick > 0:
        try:
            outcomes = _resolve_due_outcomes(reader=reader, limit=flags.outcome_check_per_tick)
        except Exception:
            LOGGER.exception("[%s] outcome resolution failed", tick_id)

    # 7. Daily selection trigger (skipped entirely in review_mode).
    daily_ran, daily_materialized, daily_rejected = False, 0, 0
    if not review_mode and allow_daily_selection:
        daily_ran, daily_materialized, daily_rejected = maybe_run_daily(
            feeds_cfg, reader=reader, writer=writer, tick_id=tick_id,
            feed_library_ids=feed_library_ids, force=force_daily_selection, dry_run=dry_run,
        )

    return _build_tick_report(
        tick_id=tick_id,
        results=results,
        fetched=fetched,
        skipped_already_processed=skipped_processed,
        marked_read=marked,
        outcomes_resolved=outcomes,
        daily_ran=daily_ran,
        daily_materialized=daily_materialized,
        daily_rejected=daily_rejected,
        fatal_llm_error=fatal_seen,
        elapsed_seconds=time.perf_counter() - start_ts,
    )
