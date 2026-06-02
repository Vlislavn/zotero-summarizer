from __future__ import annotations

from zotero_summarizer.domain import score_to_priority
from zotero_summarizer.models import TriageResult
from zotero_summarizer.services._common import clamp


LOW_ALIGNMENT_CAP = 2.5
LOW_RIGOR_CAP = 2.5
LOW_AFFINITY_THRESHOLD = 0.1
LOW_AFFINITY_CAP = 2.0

# Score -> ReadingPriority lives in `domain` (single source of truth for the
# thresholds). Re-exported here so existing `scoring.map_priority_from_score`
# callers keep working without re-deriving the mapping.
map_priority_from_score = score_to_priority


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
