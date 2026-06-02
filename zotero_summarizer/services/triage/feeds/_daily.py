"""feeds: daily plateau selection from the rolling 24h of triaged_pending rows.

Includes the two-stage full-text refine and the helpers that rebuild a
materialization payload from a stored `processed_feed_items` row hours after
the original triage tick.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import SummarizeRequest
from zotero_summarizer.services.triage import select as select_service
from zotero_summarizer.services.triage.summarization import run_pipeline
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    _DEFAULT_BLACK_SWAN_TAG,
    _load_config,
    _triage_conn,
    get_settings,
    get_state,
)
from zotero_summarizer.services.triage.feeds._daily_materialize import (
    _MaterializeCtx,
    _PendingScoredRow,
    materialize_pick,
)
from zotero_summarizer.services.triage.feeds._triage import _apply_prestige


def _should_run_daily_selection(feeds_cfg: dict[str, Any]) -> bool:
    """Return True when daily selection should fire.

    Two modes:
    - ``daily_selection_at: "HH:MM"`` (preferred) — fires once per calendar day
      after that local clock time, regardless of when the daemon started.
    - ``daily_selection_interval_hours`` (legacy fallback) — fires when >= N hours
      have elapsed since the last selection run.  ``0`` means "always run".
    """
    with _triage_conn() as conn:
        row = conn.execute(
            "SELECT MAX(updated_at) AS ts FROM processed_feed_items WHERE decision IN (?, ?, ?)",
            (
                feeds_storage.DECISION_SELECTED,
                feeds_storage.DECISION_BLACK_SWAN,
                feeds_storage.DECISION_REJECTED_DAILY_CUTOFF,
            ),
        ).fetchone()
    last_ts = row["ts"] if row is not None else None

    target_time_str = feeds_cfg.get("daily_selection_at")
    if target_time_str:
        # Time-of-day mode: fire once per calendar day after the target local time.
        try:
            target_h, target_m = (int(x) for x in str(target_time_str).split(":"))
        except (ValueError, AttributeError):
            target_h, target_m = 8, 0
        now_local = datetime.now()  # local wall clock
        today_target = now_local.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        if now_local < today_target:
            return False  # too early today
        if not last_ts:
            return True
        try:
            last_dt_utc = datetime.strptime(str(last_ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            last_dt_local = last_dt_utc.astimezone().replace(tzinfo=None)
        except ValueError:
            return True
        # Already ran today at or after the target window.
        return last_dt_local < today_target

    # Legacy interval mode.
    interval_raw = feeds_cfg.get("daily_selection_interval_hours")
    interval_h = int(interval_raw if interval_raw is not None else 24)
    # interval_h <= 0 means "always run" (useful for tests and `feeds select-daily`).
    if interval_h <= 0:
        return True
    if not last_ts:
        return True
    try:
        last_dt = datetime.strptime(str(last_ts), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(hours=interval_h)


def _refine_with_full_text(
    final_inbox: list[_PendingScoredRow],
    *,
    run_id: str,
) -> None:
    """Two-stage triage: fetch PDF + re-score top plateau picks with full text.

    No-op when full_text_refine is disabled or no PDF source is resolvable. On
    success, mutates ``pick.composite_score`` and stashes the refined
    :class:`SummarizeResponse` on ``pick.refined_summary``.

    Any failure (no DOI/arXiv, no OA PDF, fetch timeout, %PDF magic fail,
    re-triage error) is swallowed — the pick keeps its abstract-derived score.
    """
    from zotero_summarizer.integrations.pdf_fetch import fetch_pdf, resolve_pdf_url

    app_state = get_state()
    cfg_root = getattr(app_state.app_state, "config", None) if hasattr(app_state, "app_state") else None
    ftr = getattr(cfg_root, "full_text_refine", None) if cfg_root is not None else None
    if ftr is None or not ftr.enabled:
        return
    unpaywall = getattr(app_state, "unpaywall_client", None)
    if not final_inbox:
        return
    top_k = max(1, int(ftr.top_k))
    for pick in final_inbox[:top_k]:
        row = pick.row
        title = str(row.get("title") or "")
        doi = (row.get("doi") or "").strip() or None
        arxiv_id = (row.get("arxiv_id") or "").strip() or None
        item_url = (row.get("url") or "").strip() or None
        pdf_url = resolve_pdf_url(
            doi=doi,
            arxiv_id=arxiv_id,
            url=item_url,
            unpaywall=unpaywall,
        )
        if not pdf_url:
            LOGGER.info("[%s] no OA PDF for %r — keeping abstract-derived score", run_id, title[:60])
            continue
        pdf_path = fetch_pdf(
            pdf_url,
            max_bytes=int(ftr.max_pdf_bytes),
            timeout=float(ftr.fetch_timeout_secs),
        )
        if pdf_path is None:
            LOGGER.info("[%s] PDF fetch failed for %r (url=%s)", run_id, title[:60], pdf_url)
            continue
        try:
            req = SummarizeRequest(
                title=title or "Untitled",
                doi=doi,
                abstract=str(row.get("abstract") or ""),
                pdf_path=str(pdf_path),
            )
            old_score = pick.composite_score
            new_summary = run_pipeline(req, log_prefix=run_id)
            _apply_prestige(new_summary, row, log_prefix=run_id)
            pick.refined_summary = new_summary
            pick.composite_score = float(new_summary.composite_relevance_score)
            row["composite_score"] = pick.composite_score
            LOGGER.info(
                "[%s] full-text refine: %r  composite %.2f -> %.2f",
                run_id, title[:60], old_score, pick.composite_score,
            )
        except Exception as exc:
            LOGGER.warning(
                "[%s] full-text refine error for %r: %s — keeping abstract score",
                run_id, title[:60], exc,
            )


def _score_candidates(candidate_rows: list[dict[str, Any]]) -> list[_PendingScoredRow]:
    """Wrap raw candidate rows as plateau-select-compatible scored rows."""
    return [
        _PendingScoredRow(
            composite_score=float(r.get("composite_score") or 0.0),
            surprise_score=float(r.get("surprise_score") or 0.0),
            is_black_swan=False,
            row=r,
            key=f"{int(r.get('feed_library_id') or 0)}:{int(r.get('feed_item_id') or 0)}",
        )
        for r in candidate_rows
    ]


def _allocate_black_swan(
    rejected_pool: list[_PendingScoredRow], *, bs_min_score: float, force: bool
) -> list[_PendingScoredRow]:
    """Flip in 0-1 high-surprise reject as a black-swan pick (force mode only).

    With daily_max=2 the 10% rule yields 0 slots; ``force`` unconditionally
    promotes the single highest-surprise rejected candidate above bs_min_score.
    """
    if not force:
        return []
    viable = [r for r in rejected_pool if r.surprise_score >= bs_min_score]
    if not viable:
        return []
    viable.sort(key=lambda r: r.surprise_score, reverse=True)
    picks = [viable[0]]
    for p in picks:
        p.is_black_swan = True
    return picks


def _reject_unselected(
    scored: list[_PendingScoredRow],
    final_inbox: list[_PendingScoredRow],
    *,
    decision_reason: str,
    run_id: str,
) -> int:
    """Flip every non-selected candidate to rejected_daily_cutoff; return count."""
    selected_keys = {p.key for p in final_inbox}
    rejected_count = 0
    with _triage_conn() as conn:
        for pick in scored:
            if pick.key in selected_keys:
                continue
            if feeds_storage.update_to_decision(
                conn,
                feed_library_id=int(pick.row.get("feed_library_id") or 0),
                feed_item_id=int(pick.row.get("feed_item_id") or 0),
                decision=feeds_storage.DECISION_REJECTED_DAILY_CUTOFF,
                decision_reason=decision_reason,
            ):
                LOGGER.debug(
                    "[%s] ✗ rejected: %r  composite=%.2f  reason=%s",
                    run_id, str(pick.row.get("title") or "")[:60],
                    pick.composite_score, decision_reason,
                )
                rejected_count += 1
        conn.commit()
    return rejected_count


def run_daily_selection(
    *,
    reader: ZoteroReader | None = None,
    writer: ZoteroWriter | None = None,
    dry_run: bool = False,
    feed_library_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Plateau-select 1-2 best from rolling 24h of `triaged_pending` rows.

    Reads `processed_feed_items` WHERE decision='triaged_pending'
    AND created_at >= now - daily_window_hours, plateau-selects with
    hard_min=daily_target_min (default 1) and hard_max=daily_target_max
    (default 2), allocates 0-1 black-swan from the rejected pool, and
    materializes selected items directly into Zotero (Inbox + matched
    collections + tags + v3 note). All other rows flip to
    `rejected_daily_cutoff`.

    When ``feed_library_ids`` is provided, the candidate pool is restricted
    to those feeds — used by ``feeds run --feeds <name>`` so selection stays
    scoped to the feed(s) the user asked to process.

    Returns a summary dict {materialized, rejected, black_swans, errors}.
    """
    config = _load_config()
    feeds_cfg = config["feeds"]
    selection_cfg = config["selection"]
    surprise_cfg = config["surprise"]

    daily_min = int(feeds_cfg.get("daily_target_min") or 1)
    daily_max = int(feeds_cfg.get("daily_target_max") or 2)
    daily_window_h = int(feeds_cfg.get("daily_window_hours") or 24)
    kneedle_S = float(selection_cfg.get("kneedle_sensitivity") or 1.0)
    inbox_collection_name = str(feeds_cfg.get("inbox_collection_name") or "Inbox")
    outcome_window_days = int(feeds_cfg.get("outcome_window_days") or 7)
    bs_min_score = float(surprise_cfg.get("min_score") or 0.30)
    black_swan_tag = str(surprise_cfg.get("black_swan_tag") or _DEFAULT_BLACK_SWAN_TAG)
    daily_force_black_swan = bool(feeds_cfg.get("daily_force_black_swan_every_run", False))

    reader = reader or ZoteroReader(get_settings().zotero_data_dir)
    writer = writer or ZoteroWriter(get_settings().zotero_data_dir)
    run_id = feeds_storage.new_run_id(prefix="daily")

    # 1. Gather candidates (optionally scoped to specific feeds).
    with _triage_conn() as conn:
        candidate_rows = feeds_storage.select_pending_triaged(
            conn,
            since_hours=daily_window_h,
            limit=1000,
            feed_library_ids=feed_library_ids,
        )

    if not candidate_rows:
        LOGGER.info("[%s] no triaged_pending rows in last %dh — skipping daily selection", run_id, daily_window_h)
        return {"run_id": run_id, "materialized": 0, "rejected": 0, "black_swans": 0, "errors": []}

    scored = _score_candidates(candidate_rows)

    # 2. Plateau-select top 1-2.
    selection = select_service.plateau_select(
        scored,
        target_fraction=max(0.01, daily_max / max(1, len(scored))),
        hard_min=min(daily_min, len(scored)),
        hard_max=min(daily_max, len(scored)),
        kneedle_sensitivity=kneedle_S,
    )
    selected: list[_PendingScoredRow] = list(selection.selected)
    rejected_pool: list[_PendingScoredRow] = list(selection.rejected)

    # 3. Black-swan allocation (0-1 high-surprise reject, force mode only).
    bs_picks = _allocate_black_swan(
        rejected_pool, bs_min_score=bs_min_score, force=daily_force_black_swan,
    )

    final_inbox: list[_PendingScoredRow] = list(selected) + list(bs_picks)
    LOGGER.info(
        "[%s] daily selection: %d candidates -> %d selected + %d black-swan (cutoff=%s reason=%s)",
        run_id,
        len(scored),
        len(selected),
        len(bs_picks),
        selection.cutoff,
        selection.reason,
    )

    # 3.5. Two-stage refine: fetch PDF + re-score top picks with full text.
    _refine_with_full_text(final_inbox, run_id=run_id)
    # Re-sort in case full-text scoring changed the ranking.
    final_inbox.sort(key=lambda p: p.composite_score, reverse=True)

    # 4. Materialize selected items directly.
    materialized_count = 0
    errors: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    if not dry_run:
        mat_ctx = _MaterializeCtx(
            inbox_collection_name=inbox_collection_name,
            black_swan_tag=black_swan_tag,
            outcome_window_days=outcome_window_days,
            decision_reason=selection.reason,
        )
        for pick in final_inbox:
            err = materialize_pick(
                pick, writer=writer, run_id=run_id, used_keys=used_keys, ctx=mat_ctx,
            )
            if err is None:
                materialized_count += 1
            else:
                errors.append(err)

    # 5. Flip all the rest to rejected_daily_cutoff.
    rejected_count = 0
    if not dry_run:
        rejected_count = _reject_unselected(
            scored, final_inbox, decision_reason=selection.reason, run_id=run_id,
        )

    return {
        "run_id": run_id,
        "materialized": materialized_count,
        "rejected": rejected_count,
        "black_swans": len(bs_picks),
        "errors": errors,
        "cutoff": selection.cutoff,
        "cutoff_reason": selection.reason,
    }
