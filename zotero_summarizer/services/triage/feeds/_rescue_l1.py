"""Acquire-before-score rescue for L1-hide candidates (G10 fix).

Split out of ``_tick_phases`` to respect the ≤500-LOC rule. The L1 auto-gate hides on the
feed-stage LLM ``relevance_score <= llm_floor``; G10 measured that for ABSTRACT-LESS rows
(scored on title alone) this is unsafe — 1/5 was a clear false-positive, 4/5 borderline. This
re-scores those candidates on the full text before the verdict stands, so the L1 floor acts on
grounded evidence. Reuses :func:`_rescue._rescue_one` (PDF acquire + re-score).
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.triage.feeds._common import (
    LOGGER,
    TriagedCandidate,
    _has_usable_abstract,
    get_state,
)
from zotero_summarizer.services.triage.feeds._rescue import _rescue_one


def recover_abstractless_l1_candidates(
    triaged: list[tuple[dict[str, Any], TriagedCandidate]], *, tick_id: str, llm_floor: int
) -> tuple[list[tuple[dict[str, Any], TriagedCandidate]], int]:
    """Acquire-before-score for L1-hide candidates that lack an abstract (G10 fix).

    After triage, an item whose LLM ``relevance_score <= llm_floor`` AND has no usable
    abstract was scored on its TITLE alone — the L1 auto-gate would then hide it on
    weakly-grounded evidence. G10 measured this: of 5 such L1-only hides, 1 was a clear
    false-positive and 4 were borderline-unstable (judges split 2-vs-3). The scale's 2 =
    "limited rigor" is a *quality* judgment, not relevance, and on a title-only score it
    catches on-topic-but-thin papers, not the "trash" the user means to hide.

    ``triaged`` is the ``run_triage_stage`` result shape: ``list[(feed_item, TriagedCandidate)]``
    (same as ``record_tick_decisions`` consumes). Rescue: fetch the full text + re-score on
    it (reuses :func:`_rescue._rescue_one`). If the re-score clears ``llm_floor``, the
    item SURVIVES (now grounded); if it still <= floor, the hide is GROUNDED (keep — the gate
    then acts on real evidence). Returns ``(updated_triaged, n_rescued)``. Reuses the
    ``recover_abstract`` config (cap, min_abstract_chars) and is a no-op when that feature is
    off — so the contract holds: no rescue, no behavior change, the L1 floor acts as before.
    Capped at ``max_per_tick`` (the cap is logged, never silent).
    """
    cfg: Any = getattr(get_state().app_state.config, "recover_abstract", None)
    if cfg is None or not cfg.enabled:
        return triaged, 0
    n_rescued = 0
    attempted = 0
    deferred = 0
    out: list[tuple[dict[str, Any], TriagedCandidate]] = []
    for item, cand in triaged:
        score = getattr(cand.summary, "relevance_score", None)
        eligible = (
            score is not None and int(score) <= llm_floor
            and not _has_usable_abstract(item, min_chars=cfg.min_abstract_chars)
        )
        if not eligible:
            out.append((item, cand))
            continue
        if attempted >= cfg.max_per_tick:
            deferred += 1
            out.append((item, cand))  # over cap: title-grounded verdict stands (gate acts as before)
            continue
        attempted += 1
        rescued = _rescue_one(item, tick_id=tick_id)
        if rescued is None:
            out.append((item, cand))  # no fetchable full text — gate verdict stands (grounded negative)
        else:
            out.append((item, rescued))
            n_rescued += 1
    if deferred:
        LOGGER.info(
            "[%s] recover-abstract-l1: %d candidate(s) deferred — hit max_per_tick=%d",
            tick_id, deferred, cfg.max_per_tick,
        )
    if n_rescued:
        LOGGER.info(
            "[%s] recover-abstract-l1: re-scored %d abstract-less L1-candidate(s) on full text",
            tick_id, n_rescued,
        )
    return out, n_rescued
