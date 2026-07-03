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

from zotero_summarizer.integrations.app_rss import AppRssReader
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    DaemonTickReport,
    TriagedCandidate,
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
from zotero_summarizer.services.triage.feeds._rescue_l1 import (
    recover_abstractless_l1_candidates,
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


def _maybe_refresh_app_rss(reader: ZoteroReader, feeds_cfg: dict[str, Any], tick_id: str) -> None:
    """Refresh app-RSS feeds in place before picking, when the reader supports it."""
    if hasattr(reader, "refresh_feeds"):
        refresh_cfg = feeds_cfg.get("startup_refresh") or feeds_cfg.get("refresh") or {}
        max_feeds = int(refresh_cfg.get("max_feeds_per_pass") or feeds_cfg.get("max_feeds_per_pass") or 10)
        max_new = int(refresh_cfg.get("max_new_items_per_feed") or feeds_cfg.get("max_new_items_per_feed") or 25)
        timeout = float(refresh_cfg.get("per_feed_timeout_secs") or feeds_cfg.get("per_feed_timeout_secs") or 10.0)
        try:
            refreshed = reader.refresh_feeds(
                max_feeds=max_feeds,
                max_new_items_per_feed=max_new,
                per_feed_timeout=timeout,
            )
            LOGGER.info("[%s] app RSS refresh: %s", tick_id, refreshed)
        except Exception:
            LOGGER.exception("[%s] app RSS refresh failed", tick_id)


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
        from zotero_summarizer.services._common import settings
        from zotero_summarizer.services.library import deep_review
        from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
        from zotero_summarizer.services.triage.daily_select import assemble_daily_slate

        db_path = settings().triage_db_path
        slate = assemble_daily_slate(db_path=db_path, K=k)
        done = deep_review.cached_review_keys()
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
    as a library item. Renders only keys ALREADY reviewed (``cached_review_keys`` — so the
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
        from zotero_summarizer.services._common import settings
        from zotero_summarizer.services.library import deep_review, paper_render
        from zotero_summarizer.services.triage.daily_select import assemble_daily_slate

        db_path = settings().triage_db_path
        slate = assemble_daily_slate(db_path=db_path, K=max(k, 5))
        reviewed = deep_review.cached_review_keys()
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

    reader = reader or AppRssReader(get_settings().triage_db_path)
    if writer is None:
        try:
            writer = ZoteroWriter(get_settings().zotero_data_dir)
        except Exception as exc:  # noqa: BLE001 — Zotero is now an optional adapter
            writer = None
            LOGGER.info("[%s] Zotero writer unavailable; app RSS read-state only: %s", tick_id, exc)

    # batch_size semantics: None = unlimited (feeds run full-exhaust); int = bounded.
    effective_batch: int | None = batch_size
    flags = _resolve_tick_flags(feeds_cfg)
    LOGGER.info("[%s] tick start batch=%s", tick_id, effective_batch if effective_batch is not None else "unlimited")

    _maybe_refresh_app_rss(reader, feeds_cfg, tick_id)

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

    # 3.5 Acquire-before-score for L1-hide candidates (G10): an abstract-less row scored
    #     <= llm_floor was scored on its TITLE alone — re-score it on the full text before the
    #     verdict is recorded + the L1 gate acts, so the hide decision is content-grounded (not
    #     a title-grounded false-positive). No-op when recover_abstract is off or in gate_only
    #     (no LLM). Reuses the SAME llm_floor the auto quality-gate reads (quality_gate._gate_config).
    #     Non-blocking (mirrors fire_full at step 7.6): the rescue is a precision step; an
    #     expected failure (bad config value, PDF/LLM I/O) is logged + skipped — fire_full then
    #     reads its own floor and the title-grounded verdict stands. A real bug propagates.
    triaged_results = _maybe_rescue_l1(triaged_results, tick_id, gate_only)

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
    daily_materialized_keys: list[str] = []
    if not review_mode and allow_daily_selection:
        daily_ran, daily_materialized, daily_rejected, daily_materialized_keys = maybe_run_daily(
            feeds_cfg, reader=reader, writer=writer, tick_id=tick_id,
            feed_library_ids=feed_library_ids, force=force_daily_selection, dry_run=dry_run,
        )

    # 7.5 Auto full-text quality review of the just-materialized Today picks.
    # Before this, deep_review only fired at startup (prewarm) or on a manual
    # button/API trigger — so papers triaged in during a tick got NO full-text
    # review. Daily selection just materialized them into Zotero (real keys +
    # fetchable PDFs), so this is the first point they can be reviewed. Fire-
    # and-forget on the deep_review job pool (provider-aware: parallel on a
    # remote provider, queued on a local one) — never blocks the tick. Reuses
    # the SAME assess_digest path the library/per-paper button uses (not a
    # second review). Skipped in dry_run. See QualityReviewConfig.auto_on_tick_k.
    if daily_materialized_keys and not dry_run:
        _maybe_auto_review(tick_id, daily_materialized_keys)

    # 7.55 In-place review of the broader Today slate (no Zotero write) so EVERY shown
    # card can carry a quality grade, not just the few daily picks materialized above.
    # Keyed by stable_feed_key; cheap on re-ticks (skips already-reviewed keys).
    if not dry_run:
        _auto_review_slate(tick_id)

    # 7.56 Heavy paper RENDER for the TOP-K feed papers (reviewed last tick), so a top
    # feed paper opens with the same full brief as a library item. Lags the review by a
    # tick (renders only already-reviewed keys), cheap (skips already-rendered, no LLM).
    if not dry_run:
        _auto_render_slate(tick_id)

    # 7.6 Auto QUALITY GATE — hide bad-quality shown rows (precision mode). The
    # LLM relevance_score just landed in shap_contribs_json for the triaged rows, so
    # this is the first point L1 (score floor) can reach them; L2 (grade D | flag) also
    # re-runs over the deep-review cache. Conjunctive: quality is a HARD filter (a bad
    # paper on-topic is hidden, even if goal-matched); match stays the ranker among
    # survivors. Writes dont_read with source=auto_quality (one-tap reversible, never
    # clobbers a user verdict). Non-blocking; skipped in dry_run. See quality_gate.
    _maybe_auto_quality_gate(tick_id, dry_run)

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
