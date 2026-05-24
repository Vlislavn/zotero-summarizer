"""feeds: the abstract-only triage primitive + concurrent scoring + prestige.

Shared between the daemon tick (`_tick`) and daily-selection refine (`_daily`).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from zotero_summarizer.models import SummarizeRequest, SummarizeResponse
from zotero_summarizer.services.model import prestige as prestige_service
from zotero_summarizer.services.model import scoring as scoring_service
from zotero_summarizer.services.model import surprise as surprise_service
from zotero_summarizer.services.triage.summarization import run_abstract_pipeline
from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    TriagedCandidate,
    _dim_value,
    _is_fatal_llm_error,
    _parse_year,
    _triage_result_from_summary,
    get_settings,
    get_state,
)


def _triage_one(
    item: dict[str, Any],
    *,
    log_prefix: str,
    triage_llm: Any | None = None,
) -> tuple[TriagedCandidate | None, str | None, bool]:
    """Triage one feed item. Returns (candidate, error_msg, is_fatal).

    `is_fatal` is True for endpoint/auth errors that will recur on every
    subsequent call (401, connection error, etc.) — caller should abort.

    ``triage_llm`` optionally overrides the LLM provider for this scoring
    pass (the backlog drain uses the custom ``sota``); ``None`` uses the
    default refine client.
    """
    try:
        req = SummarizeRequest(
            title=item.get("title") or "Untitled",
            doi=(item.get("doi") or "").strip() or None,
            abstract=item.get("abstract") or "",
            pdf_path="",
        )
        summary = run_abstract_pipeline(req, log_prefix=log_prefix, llm_override=triage_llm)
        _apply_prestige(summary, item, log_prefix=log_prefix)
        surprise = surprise_service.compute_surprise_score(
            methodological_rigor=_dim_value(summary, "methodological_rigor"),
            novelty_for_goals=_dim_value(summary, "novelty_for_goals"),
            corpus_affinity=float(summary.corpus_affinity_score),
        )
        cand = TriagedCandidate(
            feed_item=item,
            summary=summary,
            composite_score=float(summary.composite_relevance_score),
            surprise_score=surprise,
        )
        return cand, None, False
    except Exception as exc:
        fatal = _is_fatal_llm_error(exc)
        return None, str(exc), fatal


def _score_survivors(
    to_triage: list[dict[str, Any]],
    *,
    tick_id: str,
    triage_llm: Any | None,
) -> tuple[
    list[tuple[dict[str, Any], "TriagedCandidate"]],
    list[tuple[dict[str, Any], "TriagedCandidate"]],
    list[tuple[dict[str, Any], str]],
    bool,
]:
    """LLM-score gate survivors CONCURRENTLY and partition the results.

    Returns ``(triaged, fast_rejected, errors, fatal_seen)``. The per-item LLM
    call is the drain's bottleneck and each item is independent I/O, so they run
    on a thread pool sized by ``triage_job_concurrency`` (TRIAGE_JOB_CONCURRENCY,
    default 4) — reusing the in-repo pattern from ``services.llm_classifier``.

    ``_triage_one`` converts every per-item failure into ``(None, err, fatal)``
    and never raises, so ``fut.result()`` is the per-item boundary, not an
    exception path. Partitioning preserves input order and matches the former
    sequential logic exactly (``cand is None`` → error; ``prefilter_low_corpus_affinity``
    tag → fast-reject; otherwise triaged).
    """
    triaged_results: list[tuple[dict[str, Any], "TriagedCandidate"]] = []
    fast_rejected_results: list[tuple[dict[str, Any], "TriagedCandidate"]] = []
    errors_results: list[tuple[dict[str, Any], str]] = []
    fatal_seen = False
    if not to_triage:
        return triaged_results, fast_rejected_results, errors_results, fatal_seen

    workers = max(1, min(get_settings().triage_job_concurrency, len(to_triage)))
    LOGGER.info("[%s] scoring %d survivors with %d workers", tick_id, len(to_triage), workers)
    outcomes: list[tuple[TriagedCandidate | None, str | None, bool] | None] = (
        [None] * len(to_triage)
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _triage_one, item,
                log_prefix=f"daemon:{tick_id}:{i + 1}", triage_llm=triage_llm,
            ): i
            for i, item in enumerate(to_triage)
        }
        for fut in as_completed(futures):
            outcomes[futures[fut]] = fut.result()

    for item, outcome in zip(to_triage, outcomes):
        cand, err, fatal = outcome  # type: ignore[misc]  # every slot is filled above
        if cand is None:
            errors_results.append((item, err or "unknown_error"))
            if fatal:
                fatal_seen = True
                LOGGER.error("[%s] FATAL LLM error: %s", tick_id, err)
            continue
        is_fast_reject = any(
            "prefilter_low_corpus_affinity" in (t or "").lower() for t in (cand.summary.tags or [])
        )
        if is_fast_reject:
            fast_rejected_results.append((item, cand))
        else:
            triaged_results.append((item, cand))
    return triaged_results, fast_rejected_results, errors_results, fatal_seen


def _apply_prestige(
    summary: SummarizeResponse,
    item: dict[str, Any],
    *,
    log_prefix: str,
) -> None:
    """Look up OpenAlex prestige and re-score the summary in place.

    No-op when prestige is disabled or no client is available. Errors are
    swallowed (logged at debug) — prestige must never block triage.
    """
    app_state = get_state()
    client = getattr(app_state, "openalex_client", None)
    config = getattr(app_state.app_state, "config", None) if hasattr(app_state, "app_state") else None
    prestige_cfg = getattr(config, "prestige", None) if config is not None else None
    if prestige_cfg is None or not prestige_cfg.enabled:
        return
    year = _parse_year(item.get("publication_date"))
    score, work = prestige_service.lookup_prestige(
        client,
        doi=(item.get("doi") or "").strip() or None,
        title=item.get("title") or "",
        year=year,
        require_doi=bool(prestige_cfg.require_doi),
        neutral=float(prestige_cfg.fallback_neutral),
    )
    triage = _triage_result_from_summary(summary)
    new_composite = scoring_service.compute_composite_score(
        triage,
        float(summary.corpus_affinity_score),
        prestige_score=score,
    )
    summary.prestige_score = score
    summary.prestige_venue = work.venue_display_name if work else ""
    summary.composite_relevance_score = float(new_composite)
    summary.reading_priority = scoring_service.map_priority_from_score(new_composite)
    LOGGER.info(
        "[%s] prestige: h=%d venue=%d cites=%d score=%.2f composite=%.2f",
        log_prefix,
        work.max_author_h_index if work else 0,
        work.venue_works_count if work else 0,
        work.cited_by_count if work else 0,
        score,
        new_composite,
    )
