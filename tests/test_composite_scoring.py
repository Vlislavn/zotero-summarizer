from __future__ import annotations

from types import SimpleNamespace

from zotero_summarizer.services import feedback, scoring
from zotero_summarizer.models import TriageDimensions, TriageResult


def _build_triage(**overrides) -> TriageResult:
    dimensions = overrides.pop(
        "dimensions",
        TriageDimensions(
            goal_alignment=4,
            novelty_for_goals=4,
            methodological_rigor=4,
            actionability=3,
            evidence_strength=4,
        ),
    )
    payload = {
        "score": 4,
        "reading_priority": "should_read",
        "tags": ["topic:agents"],
        "rationale": "Strong paper.",
        "dimensions": dimensions,
        "confidence": 0.8,
    }
    payload.update(overrides)
    return TriageResult(**payload)


def test_composite_score_increases_with_corpus_affinity():
    triage = _build_triage()

    low_affinity = scoring.compute_composite_score(triage, -1.0)
    high_affinity = scoring.compute_composite_score(triage, 1.0)

    assert high_affinity > low_affinity


def test_composite_score_caps_low_goal_alignment():
    triage = _build_triage(
        score=5,
        confidence=1.0,
        dimensions=TriageDimensions(
            goal_alignment=1,
            novelty_for_goals=5,
            methodological_rigor=5,
            actionability=5,
            evidence_strength=5,
        ),
    )

    score = scoring.compute_composite_score(triage, 1.0)

    assert score <= 2.5


def test_infer_feedback_events_populates_original_priority_and_false_negative_type():
    item = SimpleNamespace(
        item_id="paper-1",
        tags=["🧠"],
        annotation_count=0,
        manual_note_count=0,
        created_at="2026-03-01T00:00:00Z",
    )

    events = feedback.infer_feedback_events_from_corpus_items(
        [item],
        stale_days_for_weak_negative=30,
        latest_results_by_item_id={
            "paper-1": {
                "reading_priority": "dont_read",
                "composite_score": 2.0,
            }
        },
    )

    assert len(events) == 1
    assert events[0]["original_priority"] == "dont_read"
    assert events[0]["feedback_type"] == "implicit_engagement_false_negative"
    assert events[0]["inferred_relevance"] == 5.0
