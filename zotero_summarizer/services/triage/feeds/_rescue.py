"""Acquire-before-score rescue for abstract-less feed items."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    TriagedCandidate,
    _has_usable_abstract,
    get_state,
)


def _max_goal_sim(pred: Any) -> float:
    """Return the strongest research-goal cosine already computed by the gate."""
    aux = getattr(pred, "aux_context", None) or {}
    goal_sims = aux.get("goal_sims") or {}
    vals = [float(v) for v in goal_sims.values() if v is not None]
    return max(vals) if vals else 0.0


def _rescue_one(item: dict[str, Any], *, tick_id: str) -> TriagedCandidate | None:
    """Fetch full text for one abstract-less item and score it on the PDF."""
    from zotero_summarizer.models import SummarizeRequest
    from zotero_summarizer.services.library._pdf_acquire import acquire_pdf_for
    from zotero_summarizer.services.triage.feeds._triage import _apply_prestige
    from zotero_summarizer.services.triage.summarization import run_pipeline

    item_key = str(item.get("item_key") or item.get("item_id") or "")
    title = str(item.get("title") or "").strip()
    detail = {"url": item.get("url") or "", "doi": item.get("doi") or "", "has_pdf": False}
    try:
        acquired = acquire_pdf_for(item_key, detail)
    except Exception as exc:  # noqa: BLE001 — I/O boundary; logged, verdict stands
        LOGGER.warning("[%s] recover-abstract: fetch error for %r: %s", tick_id, title[:60], exc)
        return None
    if acquired.path is None:
        LOGGER.info(
            "[%s] recover-abstract: no fetchable full text for %r (needs_login=%s) — gate verdict stands",
            tick_id, title[:60], acquired.needs_login,
        )
        return None
    try:
        req = SummarizeRequest(
            title=title or "Untitled",
            doi=(item.get("doi") or "").strip() or None,
            abstract=str(item.get("abstract") or ""),
            pdf_path=str(acquired.path),
        )
        summary = run_pipeline(req, log_prefix=tick_id)
        _apply_prestige(summary, item, log_prefix=tick_id)
    except Exception as exc:  # noqa: BLE001 — I/O boundary (LLM); logged, verdict stands
        LOGGER.warning("[%s] recover-abstract: re-score error for %r: %s", tick_id, title[:60], exc)
        return None
    composite = float(summary.composite_relevance_score)
    LOGGER.info(
        "[%s] recover-abstract: rescued %r → composite=%.2f priority=%s",
        tick_id, title[:60], composite, summary.reading_priority,
    )
    return TriagedCandidate(
        feed_item=item, summary=summary, composite_score=composite, surprise_score=0.0,
    )


def recover_abstractless_rescues(
    gate_rejected: list[tuple[dict[str, Any], Any]],
    *,
    tick_id: str,
) -> tuple[
    list[tuple[dict[str, Any], TriagedCandidate]],
    list[tuple[dict[str, Any], Any]],
]:
    """Re-score abstract-less, high-goal gate rejects on acquired full text."""
    if not gate_rejected:
        return [], gate_rejected
    cfg = getattr(get_state().app_state.config, "recover_abstract", None)
    if cfg is None or not cfg.enabled:
        return [], gate_rejected

    rescued: list[tuple[dict[str, Any], TriagedCandidate]] = []
    still_rejected: list[tuple[dict[str, Any], Any]] = []
    attempted = 0
    deferred = 0
    for item, pred in gate_rejected:
        eligible = (
            pred is not None
            and not _has_usable_abstract(item, min_chars=cfg.min_abstract_chars)
            and _max_goal_sim(pred) >= cfg.goal_sim_threshold
        )
        if not eligible:
            still_rejected.append((item, pred))
            continue
        if attempted >= cfg.max_per_tick:
            deferred += 1
            still_rejected.append((item, pred))
            continue
        attempted += 1
        cand = _rescue_one(item, tick_id=tick_id)
        if cand is None:
            still_rejected.append((item, pred))
        else:
            rescued.append((item, cand))
    if deferred:
        LOGGER.info(
            "[%s] recover-abstract: %d eligible item(s) deferred — hit max_per_tick=%d",
            tick_id, deferred, cfg.max_per_tick,
        )
    if rescued:
        LOGGER.info(
            "[%s] recover-abstract: rescued %d abstract-less high-goal item(s)",
            tick_id, len(rescued),
        )
    return rescued, still_rejected
