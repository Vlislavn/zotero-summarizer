"""feeds: the discrete phases of one daemon tick.

``run_daemon_tick`` (in ``_tick``) orchestrates these in order: pick unread ->
prepare/dedup -> triage -> record decisions -> mark read -> daily selection.
They live here so the orchestrator stays a thin, readable sequence.
"""
from __future__ import annotations

import random
from itertools import chain, islice, zip_longest
from collections import Counter
from dataclasses import dataclass
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services._common import is_app_rss_source
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import rss as rss_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    TriagedCandidate,
    _triage_conn,
    get_state,
)
from zotero_summarizer.services.triage.feeds._gate import (
    _pack_review_payload,
    _synthesize_gate_only_candidate,
)
from zotero_summarizer.services.triage.feeds._triage import _score_survivors
from zotero_summarizer.services.triage.feeds._daily import (
    _should_run_daily_selection,
    run_daily_selection,
)


@dataclass
class _TickResults:
    """The per-decision buckets a tick produces, shared across record/mark phases."""

    triaged: list[tuple[dict[str, Any], TriagedCandidate]]
    fast_rejected: list[tuple[dict[str, Any], TriagedCandidate]]
    errors: list[tuple[dict[str, Any], str]]
    gate_rejected: list[tuple[dict[str, Any], Any]]
    library_skipped: list[dict[str, Any]]
    processed_dup_skipped: list[dict[str, Any]]


def _pick_unread_batch_round_robin(
    reader: ZoteroReader,
    *,
    batch_size: int | None,
    feed_library_ids: list[int] | None,
    exclude_feed_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Pick unread items across feeds.

    When ``batch_size`` is an integer: round-robin across feeds up to that
    limit.  Round-robin prevents one prolific feed (e.g. bioRxiv: 405 items)
    from starving smaller feeds.

    When ``batch_size`` is ``None``: fetch ALL unread items from every specified
    feed without any cap (used by ``feeds run`` for full exhaustion).

    ``exclude_feed_names`` (casefolded) drops non-paper feeds at the source when
    feeds are auto-resolved (``feeds.exclude_feeds`` config) — e.g. a
    GitHub-releases feed that emits changelogs, not papers. An explicit
    ``feed_library_ids`` (CLI ``--feed``) is the user's own choice and is not
    filtered.
    """
    if not feed_library_ids:
        feed_groups = reader.get_feed_groups()
        excluded = exclude_feed_names or set()
        kept = [f for f in feed_groups if str(f.get("name") or "").strip().casefold() not in excluded]
        if len(kept) != len(feed_groups):
            LOGGER.info(
                "excluding %d non-paper feed(s) from triage (feeds.exclude_feeds)",
                len(feed_groups) - len(kept),
            )
        feed_library_ids = [int(f["library_id"]) for f in kept]
    if not feed_library_ids:
        return []

    if batch_size is not None and batch_size <= 0:
        raise ValueError("batch_size must be positive or None")
    feed_order = list(dict.fromkeys(feed_library_ids))
    if batch_size is not None:
        random.shuffle(feed_order)
    pools = [
        reader.get_feed_items(
            feed_library_id=int(lib_id), unread_only=True, order="oldest_first",
            limit=None if batch_size is None else batch_size * 2,
        )
        for lib_id in feed_order
    ]
    if batch_size is None:
        return list(chain.from_iterable(pools))
    round_robin = (item for row in zip_longest(*pools) for item in row if item is not None)
    return list(islice(round_robin, batch_size))


def pick_and_log(
    reader: ZoteroReader,
    *,
    batch_size: int | None,
    feed_library_ids: list[int] | None,
    exclude_feed_names: set[str],
    tick_id: str,
) -> list[dict[str, Any]]:
    """Round-robin pick unread items and log the per-feed breakdown."""
    raw = _pick_unread_batch_round_robin(
        reader,
        batch_size=batch_size,
        feed_library_ids=feed_library_ids,
        exclude_feed_names=exclude_feed_names,
    )
    if raw:
        per_feed = Counter(item["feed_library_id"] for item in raw)
        feed_summary = " ".join(f"feed{fid}={cnt}" for fid, cnt in sorted(per_feed.items()))
        LOGGER.info("[%s] found %d unread: %s", tick_id, len(raw), feed_summary)
    else:
        LOGGER.info("[%s] no unread items — nothing to do", tick_id)
    return raw


def prepare_unprocessed(
    raw: list[dict[str, Any]], *, tick_id: str, dry_run: bool = False
) -> tuple[list[dict[str, Any]], int, list[tuple[int, int, str]]]:
    """Dedup against processed rows; collect stale-unread + clear retryable errors.

    Returns ``(unprocessed, skipped_processed, stale_to_mark)``. Already-decided
    items that linger unread saturate the bounded picker, so ``stale_to_mark``
    collects them for eviction; errored rows are cleared so the retry can record
    a fresh decision.
    """
    with _triage_conn() as conn:
        unprocessed, skipped_processed = feeds_storage.filter_unprocessed(conn, raw)
        stale_pairs = feeds_storage.select_stale_unread_to_mark(conn, raw)
        source_by_pair = {
            (int(item.get("feed_library_id") or 0), int(item.get("item_id") or 0)):
            str(item.get("source_type") or item.get("source") or "zotero")
            for item in raw
        }
        stale_to_mark = [
            (fl, fi, source_by_pair.get((fl, fi), "zotero"))
            for fl, fi in stale_pairs
        ]
        cleared = 0 if dry_run else feeds_storage.clear_error_rows(conn, unprocessed)
        if cleared:
            conn.commit()
            LOGGER.info("[%s] cleared %d stale error row(s) for retry", tick_id, cleared)
    return unprocessed, skipped_processed, stale_to_mark


def run_triage_stage(
    to_triage: list[dict[str, Any]],
    *,
    tick_id: str,
    gate_only: bool,
    triage_llm: Any | None,
) -> tuple[
    list[tuple[dict[str, Any], TriagedCandidate]],
    list[tuple[dict[str, Any], TriagedCandidate]],
    list[tuple[dict[str, Any], str]],
    bool,
]:
    """Triage the gate survivors; return (triaged, fast_rejected, errors, fatal_seen).

    ``gate_only`` synthesises a candidate per survivor from the classifier verdict
    and skips the LLM entirely. Otherwise the LLM stage runs with concurrency
    sized from the resolved per-stage provider (feed vs backlog).
    """
    triaged_results: list[tuple[dict[str, Any], TriagedCandidate]] = []
    fast_rejected_results: list[tuple[dict[str, Any], TriagedCandidate]] = []
    errors_results: list[tuple[dict[str, Any], str]] = []
    fatal_seen = False
    if gate_only:
        LOGGER.info("[%s] gate_only: synthesising %d candidates from gate predictions",
                    tick_id, len(to_triage))
        for item in to_triage:
            triaged_results.append((item, _synthesize_gate_only_candidate(item)))
    elif to_triage:
        # Stage identity: the live daemon passes triage_llm=None (feed stage,
        # resolved per-item); the backlog drain passes an explicit client. Size
        # the pool from that stage's provider.
        stage = "feed" if triage_llm is None else "backlog"
        provider = get_state().resolve_stage_provider(stage)
        triaged_results, fast_rejected_results, errors_results, fatal_seen = _score_survivors(
            to_triage, tick_id=tick_id, triage_llm=triage_llm, provider=provider,
        )
    return triaged_results, fast_rejected_results, errors_results, fatal_seen


def record_tick_decisions(results: _TickResults, *, tick_id: str, review_mode: bool) -> None:
    """Persist every per-bucket decision for the tick in one transaction."""
    triaged_decision = (
        feeds_storage.DECISION_AWAITING_REVIEW if review_mode
        else feeds_storage.DECISION_TRIAGED_PENDING
    )
    triaged_decision_reason = "awaiting_review" if review_mode else "pending_daily_selection"
    with _triage_conn() as conn:
        for item, cand in results.triaged:
            feeds_storage.record_decision(
                conn, run_id=tick_id, feed_item=item,
                decision=triaged_decision, decision_reason=triaged_decision_reason,
                composite_score=cand.composite_score, surprise_score=cand.surprise_score,
                corpus_affinity=float(cand.summary.corpus_affinity_score),
                reading_priority=cand.summary.reading_priority,
                matched_collections=list(cand.summary.suggested_collections or []),
                shap_contribs_json=_pack_review_payload(item, summary=cand.summary),
            )
        for item, cand in results.fast_rejected:
            feeds_storage.record_decision(
                conn, run_id=tick_id, feed_item=item,
                decision=feeds_storage.DECISION_REJECTED_LOW_SCORE,
                decision_reason="corpus_fast_reject",
                composite_score=cand.composite_score, surprise_score=cand.surprise_score,
                corpus_affinity=float(cand.summary.corpus_affinity_score),
                reading_priority=cand.summary.reading_priority,
                shap_contribs_json=_pack_review_payload(item, summary=cand.summary),
            )
        for item in results.processed_dup_skipped:
            feeds_storage.record_decision(
                conn, run_id=tick_id, feed_item=item,
                decision=feeds_storage.DECISION_REJECTED_DEDUP_PROCESSED,
                decision_reason="duplicate_of_processed",
            )
        for item in results.library_skipped:
            feeds_storage.record_decision(
                conn, run_id=tick_id, feed_item=item,
                decision=feeds_storage.DECISION_REJECTED_DEDUP_LIBRARY,
                decision_reason="already_in_library",
            )
        for item, pred in results.gate_rejected:
            if pred is None:
                # gate_only item the gate could not score (still no title+abstract
                # after the OpenAlex backfill). Record a terminal gate-reject with
                # no score/priority so the bounded picker stops re-fetching it;
                # it stays visible in the gate-rejected spot-check panel.
                feeds_storage.record_decision(
                    conn, run_id=tick_id, feed_item=item,
                    decision=feeds_storage.DECISION_GATE_REJECTED,
                    decision_reason="gate_unscorable:no_abstract",
                    shap_contribs_json=_pack_review_payload(item),
                )
                continue
            feeds_storage.record_decision(
                conn, run_id=tick_id, feed_item=item,
                decision=feeds_storage.DECISION_GATE_REJECTED,
                decision_reason=(
                    f"classifier_gate:{pred.predicted_priority} "
                    f"score={pred.calibrated_score:.3f}"
                ),
                # Map calibrated [0..1] to the [1..5] composite scale for parity.
                composite_score=float(pred.calibrated_score) * 5.0,
                surprise_score=0.0,
                reading_priority=pred.predicted_priority,
                shap_contribs_json=_pack_review_payload(item),
            )
        for item, err_msg in results.errors:
            feeds_storage.record_decision(
                conn, run_id=tick_id, feed_item=item,
                decision=feeds_storage.DECISION_SKIPPED_ERROR,
                decision_reason="triage_exception", error=err_msg,
            )
        conn.commit()


def mark_processed_read(
    results: _TickResults,
    stale_to_mark: list[tuple[int, int, str]],
    *,
    writer: ZoteroWriter | None,
    tick_id: str,
) -> int:
    """Mark every item the tick touched read in Zotero; return the marked count.

    Gate-rejected + dedup-skipped + stale-unread items are evicted too, else the
    bounded round-robin picker re-grabs the same batch forever. Fatal-error rows
    (item_id<=0) are spared so they get another chance next tick. A Zotero-write
    failure is logged and the tick proceeds (the rows stay unread, retried next
    tick).
    """
    zotero_ids: list[int] = []
    app_rss_ids: list[int] = []

    def _collect(item: dict[str, Any]) -> None:
        item_id = int(item.get("item_id") or 0)
        if item_id <= 0:
            return
        if str(item.get("source_type") or item.get("source") or "") == "app_rss":
            app_rss_ids.append(item_id)
        else:
            zotero_ids.append(item_id)

    for item, _cand in results.triaged + results.fast_rejected:
        _collect(item)
    for item in results.library_skipped + results.processed_dup_skipped:
        _collect(item)
    for item, _pred in results.gate_rejected:
        _collect(item)
    for _fl, _fi, source_type in stale_to_mark:
        if source_type == "app_rss":
            app_rss_ids.append(int(_fi))
        else:
            zotero_ids.append(int(_fi))
    zotero_ids = [i for i in zotero_ids if i > 0]
    app_rss_ids = [i for i in app_rss_ids if i > 0]
    if not zotero_ids and not app_rss_ids:
        return 0
    marked = 0
    touched_items = [
        item
        for item, _ in (
            results.triaged
            + results.fast_rejected
            + results.gate_rejected
        )
    ] + list(results.library_skipped) + list(results.processed_dup_skipped)

    if app_rss_ids:
        with _triage_conn() as conn:
            marked += rss_storage.mark_rss_items_read(conn, app_rss_ids)
            for item in touched_items:
                if not is_app_rss_source(item):
                    continue
                feeds_storage.record_read_marked(
                    conn,
                    feed_library_id=int(item.get("feed_library_id") or 0),
                    feed_item_id=int(item.get("item_id") or 0),
                )
            for _fl, _fi, _source_type in stale_to_mark:
                if _source_type != "app_rss":
                    continue
                feeds_storage.record_read_marked(
                    conn, feed_library_id=int(_fl), feed_item_id=int(_fi),
                )
            conn.commit()

    if not zotero_ids:
        return marked
    if writer is None:
        LOGGER.info("[%s] Zotero writer unavailable; skipped %d legacy readTime updates", tick_id, len(zotero_ids))
        return marked
    try:
        zotero_marked = writer.mark_feed_items_read(zotero_ids)
    except Exception as exc:
        LOGGER.warning("[%s] mark_feed_items_read failed: %s", tick_id, exc)
        return marked
    marked += zotero_marked
    with _triage_conn() as conn:
        for item in touched_items:
            if is_app_rss_source(item):
                continue
            feeds_storage.record_read_marked(
                conn,
                feed_library_id=int(item.get("feed_library_id") or 0),
                feed_item_id=int(item.get("item_id") or 0),
            )
        for _fl, _fi, _source_type in stale_to_mark:
            if _source_type == "app_rss":
                continue
            feeds_storage.record_read_marked(
                conn, feed_library_id=int(_fl), feed_item_id=int(_fi),
            )
        conn.commit()
    return marked


def maybe_run_daily(
    feeds_cfg: dict[str, Any],
    *,
    reader: ZoteroReader,
    writer: ZoteroWriter | None,
    tick_id: str,
    feed_library_ids: list[int] | None,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, int, int, list[str]]:
    """Run daily selection when forced or due; return (ran, materialized, rejected, materialized_keys).

    ``materialized_keys`` are the Zotero library keys just created — the tick
    auto-reviews them via deep_review when ``quality_review.auto_on_tick_k`` is set.
    ``force`` + ``feed_library_ids`` scopes the pool to those feeds. A selection
    failure is logged and reported as not-run (empty keys).
    """
    if not (force or _should_run_daily_selection(feeds_cfg)):
        return False, 0, 0, []
    if writer is None:
        LOGGER.info("[%s] daily selection skipped: Zotero writer unavailable", tick_id)
        return False, 0, 0, []
    try:
        scoped_ids = feed_library_ids if force else None
        sel = run_daily_selection(
            reader=reader, writer=writer, feed_library_ids=scoped_ids, dry_run=dry_run,
        )
        return True, sel.get("materialized", 0), sel.get("rejected", 0), list(sel.get("materialized_keys") or [])
    except Exception:
        LOGGER.exception("[%s] daily selection failed", tick_id)
        return False, 0, 0, []
