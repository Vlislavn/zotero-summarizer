"""Phase 1.8: compute_composite_score now blends prestige_score."""

from __future__ import annotations

from zotero_summarizer.models import TriageDimensions, TriageResult
from zotero_summarizer.services import scoring


def _triage() -> TriageResult:
    return TriageResult(
        score=4,
        reading_priority="should_read",
        tags=[],
        rationale="ok",
        dimensions=TriageDimensions(
            goal_alignment=4,
            novelty_for_goals=4,
            methodological_rigor=4,
            actionability=3,
            evidence_strength=4,
        ),
        confidence=0.9,
    )


def test_higher_prestige_raises_composite():
    triage = _triage()
    low = scoring.compute_composite_score(triage, 0.3, prestige_score=1.0)
    mid = scoring.compute_composite_score(triage, 0.3, prestige_score=3.0)
    high = scoring.compute_composite_score(triage, 0.3, prestige_score=5.0)
    assert low < mid < high


def test_none_prestige_uses_neutral_three():
    """Backwards compat: omitting prestige_score == passing 3.0 (neutral)."""
    triage = _triage()
    default = scoring.compute_composite_score(triage, 0.3)
    neutral = scoring.compute_composite_score(triage, 0.3, prestige_score=3.0)
    assert default == neutral


def test_low_alignment_cap_overrides_prestige():
    """Even with maximum prestige, the goal_alignment cap still applies."""
    triage = TriageResult(
        score=5,
        reading_priority="must_read",
        tags=[],
        rationale="ok",
        dimensions=TriageDimensions(
            goal_alignment=1,
            novelty_for_goals=5,
            methodological_rigor=5,
            actionability=5,
            evidence_strength=5,
        ),
        confidence=1.0,
    )
    score = scoring.compute_composite_score(triage, 1.0, prestige_score=5.0)
    assert score <= scoring.LOW_ALIGNMENT_CAP
