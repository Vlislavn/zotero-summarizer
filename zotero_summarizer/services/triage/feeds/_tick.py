"""feeds: one daemon tick — triage K unread items, mark read, side-work.

The primary daemon iteration: round-robin pick, dedup, classifier gate, LLM
triage, record decisions, mark read in Zotero, resolve due outcomes, and fire
daily selection when due.
"""
from __future__ import annotations

import random
import time
from collections import Counter
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    DaemonTickReport,
    TriagedCandidate,
    _load_config,
    _triage_conn,
    get_settings,
)
from zotero_summarizer.services.triage.feeds._gate import (
    _apply_classifier_gate,
    _maybe_schedule_gate_retrain,
    _pack_review_payload,
    _synthesize_gate_only_candidate,
)
from zotero_summarizer.services.triage.feeds._triage import _score_survivors
from zotero_summarizer.services.triage.feeds._daily import (
    _should_run_daily_selection,
    run_daily_selection,
)
from zotero_summarizer.services.triage.feeds._outcomes import _resolve_due_outcomes


def _pick_unread_batch_round_robin(
    reader: ZoteroReader,
    *,
    batch_size: int | None,
    feed_library_ids: list[int] | None,
) -> list[dict[str, Any]]:
    """Pick unread items across feeds.

    When ``batch_size`` is an integer: round-robin across feeds up to that
    limit.  Round-robin prevents one prolific feed (e.g. bioRxiv: 405 items)
    from starving smaller feeds.

    When ``batch_size`` is ``None``: fetch ALL unread items from every specified
    feed without any cap (used by ``feeds run`` for full exhaustion).
    """
    if not feed_library_ids:
        feed_groups = reader.get_feed_groups()
        feed_library_ids = [int(f["library_id"]) for f in feed_groups]
    if not feed_library_ids:
        return []

    # Unlimited mode: return everything unread from all specified feeds.
    if batch_size is None:
        all_items: list[dict[str, Any]] = []
        for lib_id in feed_library_ids:
            try:
                items = reader.get_feed_items(
                    feed_library_id=int(lib_id),
                    unread_only=True,
                    order="oldest_first",
                )
            except Exception as exc:
                LOGGER.warning("get_feed_items failed for feed_library_id=%s: %s", lib_id, exc)
                items = []
            all_items.extend(items)
        return all_items

    # Bounded mode: probe each feed; tile round-robin until batch_size.
    per_feed_pool: dict[int, list[dict[str, Any]]] = {}
    for lib_id in feed_library_ids:
        try:
            items = reader.get_feed_items(
                feed_library_id=int(lib_id),
                unread_only=True,
                order="oldest_first",
                limit=batch_size * 2,  # small headroom for dedup losses
            )
        except Exception as exc:
            LOGGER.warning("get_feed_items failed for feed_library_id=%s: %s", lib_id, exc)
            items = []
        per_feed_pool[int(lib_id)] = items

    selected: list[dict[str, Any]] = []
    feed_order = list(feed_library_ids)
    random.shuffle(feed_order)  # avoid feed_id ordering bias across ticks
    cursor = 0
    while len(selected) < batch_size:
        progressed_this_round = False
        for _ in range(len(feed_order)):
            lib_id = feed_order[cursor % len(feed_order)]
            cursor += 1
            pool = per_feed_pool.get(lib_id, [])
            if pool:
                selected.append(pool.pop(0))
                progressed_this_round = True
                if len(selected) >= batch_size:
                    break
        if not progressed_this_round:
            break
    return selected


def run_daemon_tick(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    feed_library_ids: list[int] | None = None,
    batch_size: int | None = None,
    force_daily_selection: bool = False,
    dry_run: bool = False,
    review_mode: bool = False,
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
      * Implies ``review_mode=True`` (auto-materialise on classifier verdict
        alone is too risky).
      * The LLM triage loop is skipped entirely; each survivor of the gate
        gets a synthesised ``TriagedCandidate`` from its prediction.
      * Designed to bootstrap golden-CSV labels through the review UI
        without paying for LLM calls on every item.

    Returns the tick report (for logging / CLI display).
    """
    if gate_only:
        review_mode = True
    start_ts = time.perf_counter()
    tick_id = feeds_storage.new_run_id(prefix="tick")
    config = _load_config()
    feeds_cfg = config["feeds"]

    reader = reader or ZoteroReader(get_settings().zotero_data_dir)
    writer = writer or ZoteroWriter(get_settings().zotero_data_dir)

    # batch_size semantics: None = unlimited (feeds run full-exhaust mode); int = bounded.
    # The daemon loop passes daemon_batch_size explicitly; feeds run passes None.
    effective_batch: int | None = batch_size
    dedup_against_library = bool(feeds_cfg.get("dedup_against_library", True))
    mark_processed_as_read = bool(feeds_cfg.get("mark_processed_as_read", True))
    outcome_check_per_tick = int(feeds_cfg.get("outcome_check_per_tick") or 3)

    LOGGER.info("[%s] tick start batch=%s", tick_id, effective_batch if effective_batch is not None else "unlimited")

    # 1. Pick unread items (round-robin when bounded, full-exhaust when None).
    raw = _pick_unread_batch_round_robin(
        reader,
        batch_size=effective_batch,
        feed_library_ids=feed_library_ids,
    )
    fetched = len(raw)
    if raw:
        per_feed = Counter(item["feed_library_id"] for item in raw)
        feed_summary = " ".join(f"feed{fid}={cnt}" for fid, cnt in sorted(per_feed.items()))
        LOGGER.info("[%s] found %d unread: %s", tick_id, fetched, feed_summary)
    else:
        LOGGER.info("[%s] no unread items — nothing to do", tick_id)

    # 2. Dedup against processed_feed_items + library.
    with _triage_conn() as conn:
        unprocessed, skipped_processed = feeds_storage.filter_unprocessed(conn, raw)
        # Already-decided items that linger as unread in Zotero saturate the
        # bounded round-robin picker (it re-grabs the same batch every tick and
        # never reaches new items). Collect them so the mark-read step below
        # evicts them from the unread pool. Excludes skipped_error (retryable)
        # and awaiting_review (review flow keeps those unread on purpose).
        stale_to_mark = feeds_storage.select_stale_unread_to_mark(conn, raw)
        # Errored items are retryable: drop their stale skipped_error rows so
        # the retry below can record a fresh decision (record_decision is
        # INSERT OR IGNORE). Without this the picker spins on the same errored
        # items forever and never reaches newer unprocessed ones.
        cleared = feeds_storage.clear_error_rows(conn, unprocessed)
        if cleared:
            conn.commit()
            LOGGER.info("[%s] cleared %d stale error row(s) for retry", tick_id, cleared)

    library_skipped: list[dict[str, Any]] = []
    to_triage: list[dict[str, Any]] = []
    if dedup_against_library:
        for item in unprocessed:
            doi = (item.get("doi") or "").strip()
            arxiv = (item.get("arxiv_id") or "").strip()
            if not doi and not arxiv:
                to_triage.append(item)
                continue
            try:
                existing = reader.find_by_external_id(doi=doi or None, arxiv_id=arxiv or None)
            except Exception:
                existing = None
            if existing:
                LOGGER.info(
                    "[%s] skip dedup: %r (already in library)",
                    tick_id, (item.get("title") or "")[:60],
                )
                library_skipped.append(item)
            else:
                to_triage.append(item)
    else:
        to_triage = list(unprocessed)

    # 2.5 Classifier gate (Phase 1.13) — fast-reject before the LLM.
    #     Also kicks off a background retrain when the golden CSV's sha has
    #     changed since the cached model was trained.
    gate_rejected: list[tuple[dict[str, Any], Any]] = []
    _maybe_schedule_gate_retrain(tick_id)
    to_triage, gate_rejected = _apply_classifier_gate(tick_id, to_triage)

    # 3. Triage.
    triaged_results: list[tuple[dict[str, Any], TriagedCandidate]] = []
    fast_rejected_results: list[tuple[dict[str, Any], TriagedCandidate]] = []
    errors_results: list[tuple[dict[str, Any], str]] = []
    fatal_seen = False
    if gate_only:
        # Phase 1.14: skip the LLM entirely. Each survivor of the gate becomes
        # a synthesised candidate carrying the classifier's verdict + SHAP.
        LOGGER.info("[%s] gate_only: synthesising %d candidates from gate predictions",
                    tick_id, len(to_triage))
        for item in to_triage:
            triaged_results.append((item, _synthesize_gate_only_candidate(item)))
    else:
        triaged_results, fast_rejected_results, errors_results, fatal_seen = _score_survivors(
            to_triage, tick_id=tick_id, triage_llm=triage_llm,
        )

    # 4. Record decisions.
    triaged_decision = (
        feeds_storage.DECISION_AWAITING_REVIEW if review_mode
        else feeds_storage.DECISION_TRIAGED_PENDING
    )
    triaged_decision_reason = (
        "awaiting_review" if review_mode else "pending_daily_selection"
    )
    with _triage_conn() as conn:
        for item, cand in triaged_results:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=triaged_decision,
                decision_reason=triaged_decision_reason,
                composite_score=cand.composite_score,
                surprise_score=cand.surprise_score,
                corpus_affinity=float(cand.summary.corpus_affinity_score),
                reading_priority=cand.summary.reading_priority,
                matched_collections=list(cand.summary.suggested_collections or []),
                shap_contribs_json=_pack_review_payload(item, summary=cand.summary),
            )
        for item, cand in fast_rejected_results:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_REJECTED_LOW_SCORE,
                decision_reason="corpus_fast_reject",
                composite_score=cand.composite_score,
                surprise_score=cand.surprise_score,
                corpus_affinity=float(cand.summary.corpus_affinity_score),
                reading_priority=cand.summary.reading_priority,
                shap_contribs_json=_pack_review_payload(item, summary=cand.summary),
            )
        for item in library_skipped:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_REJECTED_DEDUP_LIBRARY,
                decision_reason="already_in_library",
            )
        for item, pred in gate_rejected:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_GATE_REJECTED,
                decision_reason=(
                    f"classifier_gate:{pred.predicted_priority} "
                    f"score={pred.calibrated_score:.3f}"
                ),
                # Map calibrated [0..1] to the existing [1..5] composite scale so
                # downstream queries / dashboards have a comparable number.
                composite_score=float(pred.calibrated_score) * 5.0,
                surprise_score=0.0,
                reading_priority=pred.predicted_priority,
                shap_contribs_json=_pack_review_payload(item),
            )
        for item, err_msg in errors_results:
            feeds_storage.record_decision(
                conn,
                run_id=tick_id,
                feed_item=item,
                decision=feeds_storage.DECISION_SKIPPED_ERROR,
                decision_reason="triage_exception",
                error=err_msg,
            )
        conn.commit()

    # 5. Mark all processed items read in Zotero (skipping fatal-error rows).
    #    In review_mode (Phase 1.14) we deliberately leave items unread so they
    #    keep showing up in the Zotero RSS view while the user decides.
    marked = 0
    if mark_processed_as_read and not dry_run and not review_mode:
        processed_ids: list[int] = []
        for item, _cand in triaged_results + fast_rejected_results:
            processed_ids.append(int(item.get("item_id") or 0))
        for item in library_skipped:
            processed_ids.append(int(item.get("item_id") or 0))
        # Phase 1.13: gate-rejected items must also be marked read; otherwise
        # the daemon will keep picking them up on every tick.
        for item, _pred in gate_rejected:
            processed_ids.append(int(item.get("item_id") or 0))
        # Already-decided items the dedup skipped this tick but that are still
        # unread in Zotero: evict them too, or the bounded picker re-grabs the
        # same batch forever and never reaches new items.
        for _fl, _fi in stale_to_mark:
            processed_ids.append(int(_fi))
        # Don't mark items as read if the LLM never saw them (fatal-error
        # items deserve another chance on the next tick).
        processed_ids = [i for i in processed_ids if i > 0]
        if processed_ids:
            try:
                marked = writer.mark_feed_items_read(processed_ids)
                with _triage_conn() as conn:
                    for item, _ in triaged_results + fast_rejected_results:
                        feeds_storage.record_read_marked(
                            conn,
                            feed_library_id=int(item.get("feed_library_id") or 0),
                            feed_item_id=int(item.get("item_id") or 0),
                        )
                    for item in library_skipped:
                        feeds_storage.record_read_marked(
                            conn,
                            feed_library_id=int(item.get("feed_library_id") or 0),
                            feed_item_id=int(item.get("item_id") or 0),
                        )
                    for _fl, _fi in stale_to_mark:
                        feeds_storage.record_read_marked(
                            conn, feed_library_id=int(_fl), feed_item_id=int(_fi),
                        )
                    conn.commit()
            except Exception as exc:
                LOGGER.warning("[%s] mark_feed_items_read failed: %s", tick_id, exc)

    # 6. Resolve up to N due outcomes.
    outcomes = 0
    if outcome_check_per_tick > 0:
        try:
            outcomes = _resolve_due_outcomes(
                reader=reader,
                limit=outcome_check_per_tick,
            )
        except Exception:
            LOGGER.exception("[%s] outcome resolution failed", tick_id)

    # 7. Daily selection trigger.
    #    Skipped entirely in review_mode (Phase 1.14): the user materialises
    #    items via the review UI, not via the auto-plateau path.
    daily_ran = False
    daily_materialized = 0
    daily_rejected = 0
    if not review_mode and (force_daily_selection or _should_run_daily_selection(feeds_cfg)):
        try:
            # When force_daily_selection is True (feeds run) and feed_library_ids
            # is set, scope the candidate pool to those feeds so the user sees
            # only results from the feed(s) they explicitly ran.
            # Normal daemon ticks (force=False) always pool across all feeds.
            scoped_ids = feed_library_ids if force_daily_selection else None
            sel = run_daily_selection(
                reader=reader, writer=writer,
                feed_library_ids=scoped_ids, dry_run=dry_run,
            )
            daily_ran = True
            daily_materialized = sel.get("materialized", 0)
            daily_rejected = sel.get("rejected", 0)
        except Exception:
            LOGGER.exception("[%s] daily selection failed", tick_id)

    elapsed = time.perf_counter() - start_ts
    report = DaemonTickReport(
        tick_id=tick_id,
        fetched=fetched,
        skipped_already_processed=skipped_processed,
        skipped_library_dedup=len(library_skipped),
        triaged=len(triaged_results),
        fast_rejected=len(fast_rejected_results),
        gate_rejected=len(gate_rejected),
        errors=len(errors_results),
        marked_read=marked,
        outcomes_resolved=outcomes,
        daily_selection_ran=daily_ran,
        daily_materialized=daily_materialized,
        daily_rejected=daily_rejected,
        fatal_llm_error=fatal_seen,
        elapsed_seconds=elapsed,
    )
    LOGGER.info(
        "[%s] tick done in %.2fs fetched=%d triaged=%d fast=%d gate=%d err=%d marked=%d outcomes=%d daily=%s",
        tick_id,
        elapsed,
        fetched,
        len(triaged_results),
        len(fast_rejected_results),
        len(gate_rejected),
        len(errors_results),
        marked,
        outcomes,
        daily_ran,
    )
    return report
