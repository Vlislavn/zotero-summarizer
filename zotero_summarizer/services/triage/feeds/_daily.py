"""feeds: daily plateau selection from the rolling 24h of triaged_pending rows.

Includes the two-stage full-text refine and the helpers that rebuild a
materialization payload from a stored `processed_feed_items` row hours after
the original triage tick.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import SummarizeRequest
from zotero_summarizer.services.triage import select as select_service
from zotero_summarizer.services.triage.summarization import run_pipeline
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    _load_config,
    _triage_conn,
    get_settings,
    get_state,
)
from zotero_summarizer.services.triage.feeds._daily_materialize import _PendingScoredRow
from zotero_summarizer.services.triage.feeds._triage import _apply_prestige


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
            cache_dir=get_settings().pdf_cache_dir,
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


def run_daily_selection(*, feed_library_ids: list[int] | None = None) -> dict[str, Any]:
    """Plateau-select 1-2 best from rolling 24h of `triaged_pending` rows.

    Reads `processed_feed_items` WHERE decision='triaged_pending'
    AND created_at >= now - daily_window_hours, plateau-selects with
    hard_min=daily_target_min (default 1) and hard_max=daily_target_max
    (default 2) and allocates 0-1 black-swan from the rejected pool. Selected
    candidates and all non-selected rows remain pending until the user
    explicitly Adds or Trashes a paper after inspecting its review.

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
    bs_min_score = float(surprise_cfg.get("min_score") or 0.30)
    daily_force_black_swan = bool(feeds_cfg.get("daily_force_black_swan_every_run", False))

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

    # Ranking is advisory. Only an explicit Add after a usable review may write
    # to Zotero; the daemon must not promote a merely selected paper.
    materialized_keys: list[str] = []

    # No automated decision: all candidates stay pending until explicit Add/Trash.
    return {
        "run_id": run_id,
        "materialized": len(materialized_keys),
        "materialized_keys": materialized_keys,
        "rejected": 0,
        "black_swans": len(bs_picks),
        "selected": [{"id": int(p.row["id"]), "title": str(p.row.get("title") or "")}
                     for p in final_inbox],
        "errors": [],
        "cutoff": selection.cutoff,
        "cutoff_reason": selection.reason,
    }
