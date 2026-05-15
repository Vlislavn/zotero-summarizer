from __future__ import annotations

import math

from zotero_summarizer.domain import (
    PRIORITY_COULD_READ_THRESHOLD,
    PRIORITY_MUST_READ_THRESHOLD,
    PRIORITY_SHOULD_READ_THRESHOLD,
    ReadingPriority,
)
from zotero_summarizer.models import (
    BatchSummarizeItemResponse,
    BatchSummarizeResponse,
    SummarizeRequest,
    SummarizeResponse,
    TriageResult,
)
from zotero_summarizer.services._common import clamp


LOW_ALIGNMENT_CAP = 2.5
LOW_RIGOR_CAP = 2.5
LOW_AFFINITY_THRESHOLD = 0.1
LOW_AFFINITY_CAP = 2.0

RANK_MUST_PERCENTILE = 0.10
RANK_SHOULD_PERCENTILE = 0.30
RANK_COULD_PERCENTILE = 0.70


def map_priority_from_score(score: float) -> str:
    if score >= PRIORITY_MUST_READ_THRESHOLD:
        return ReadingPriority.MUST_READ.value
    if score >= PRIORITY_SHOULD_READ_THRESHOLD:
        return ReadingPriority.SHOULD_READ.value
    if score >= PRIORITY_COULD_READ_THRESHOLD:
        return ReadingPriority.COULD_READ.value
    return ReadingPriority.DONT_READ.value


def compute_composite_score(
    triage: TriageResult,
    corpus_affinity: float,
    prestige_score: float | None = None,
) -> float:
    affinity = clamp(corpus_affinity, -1.0, 1.0)
    corpus_scaled = 1.0 + 4.0 * ((affinity + 1.0) / 2.0)
    # Neutral 3.0 when prestige is unknown — does not perturb the baseline.
    prestige_norm = clamp(float(prestige_score) if prestige_score is not None else 3.0, 1.0, 5.0)

    if not triage.dimensions:
        baseline = float(triage.score)
        score = 0.5 * baseline + 0.5 * corpus_scaled
        score -= 1.0 * (1.0 - triage.confidence)
        return round(clamp(score, 1.0, 5.0), 2)

    dims = triage.dimensions
    floor = min(dims.goal_alignment, dims.methodological_rigor)
    # Weights sum to 1.0 across the LLM component (floor + sub-dims + prestige).
    llm_component = (
        0.50 * floor
        + 0.10 * dims.actionability
        + 0.10 * dims.novelty_for_goals
        + 0.10 * dims.evidence_strength
        + 0.05 * triage.score
        + 0.15 * prestige_norm
    )
    score = 0.5 * llm_component + 0.5 * corpus_scaled
    score -= 1.0 * (1.0 - triage.confidence)

    if dims.goal_alignment < 2:
        score = min(score, LOW_ALIGNMENT_CAP)
    if dims.methodological_rigor < 2 and dims.evidence_strength < 2:
        score = min(score, LOW_RIGOR_CAP)
    if affinity < LOW_AFFINITY_THRESHOLD and dims.goal_alignment < 3:
        score = min(score, LOW_AFFINITY_CAP)

    return round(clamp(score, 1.0, 5.0), 2)


def to_batch_normalized_score(composite_score: float) -> float:
    normalized = ((composite_score - 1.0) / 4.0) * 100.0
    return round(clamp(normalized, 0.0, 100.0), 2)


def forced_priority_from_rank(rank_index: int, total_items: int) -> str:
    if total_items <= 0:
        return ReadingPriority.COULD_READ.value

    must_n = max(1, math.ceil(total_items * RANK_MUST_PERCENTILE))
    should_n = max(must_n, math.ceil(total_items * RANK_SHOULD_PERCENTILE))
    could_n = max(should_n, math.ceil(total_items * RANK_COULD_PERCENTILE))
    rank_1based = rank_index + 1

    if rank_1based <= must_n:
        return ReadingPriority.MUST_READ.value
    if rank_1based <= should_n:
        return ReadingPriority.SHOULD_READ.value
    if rank_1based <= could_n:
        return ReadingPriority.COULD_READ.value
    return ReadingPriority.DONT_READ.value


def build_batch_response(
    results: list[tuple[str, SummarizeRequest, SummarizeResponse]],
    batch_id: str | None,
) -> BatchSummarizeResponse:
    ranked = sorted(results, key=lambda row: row[2].composite_relevance_score, reverse=True)
    total = len(ranked)
    ranked_items: list[BatchSummarizeItemResponse] = []

    for idx, (item_id, req, summary) in enumerate(ranked):
        percentile = ((total - idx) / total) * 100.0 if total else 0.0
        ranked_items.append(
            BatchSummarizeItemResponse(
                batch_id=batch_id,
                item_id=item_id,
                title=req.title,
                summary=summary,
                normalized_score=to_batch_normalized_score(summary.composite_relevance_score),
                percentile=round(percentile, 2),
                rank=idx + 1,
                forced_priority=forced_priority_from_rank(idx, total),
            )
        )

    return BatchSummarizeResponse(batch_id=batch_id, total_items=total, ranked_items=ranked_items)
