"""Unknown goal assessment cannot become a negative action at a consumer boundary."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from zotero_summarizer.models import (
    GoalSummary, ResearchCandidate, ResearchFeedTriage, ResearchProfile,
)
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.library.review_fleet import propose
from zotero_summarizer.services.research_feed.card import build_card


MISS = GoalSummary(goal="Evaluation", retrieval_state="miss").model_dump()
UNKNOWN = GoalSummary(goal="Evaluation").model_dump()
ABSTAINED_HIT = GoalSummary(goal="Evaluation", retrieval_state="hit", relevant=True).model_dump()
HIT = GoalSummary(
    goal="Evaluation", retrieval_state="hit", relevant=True, abstained=False,
    summary="Evaluates agents.", supporting_quotes=["We evaluate agents."],
).model_dump()
DIGEST = {"read_decision": "skip", "read_why": "The digest is sufficient.", "grade": "C"}
QUALITY = {"quality_band": "neutral"}


@pytest.mark.parametrize("board", [
    None, [], [{}], [{"relevant": False}], [UNKNOWN], [ABSTAINED_HIT],
    [MISS, UNKNOWN], [MISS, {}], [MISS, "invalid"],
])
def test_unknown_board_withholds_negative_action_in_proposal_and_weekly_card(board):
    proposal = propose.propose_verdict(DIGEST, QUALITY, goal_summaries=board)
    action, flags = propose.effective_read_decision(DIGEST, QUALITY, goal_summaries=board)
    card = build_card(
        ResearchCandidate(source_id="paper", source="fixture", title="Agent evaluation"),
        ResearchFeedTriage(include=True, score=3, confidence=0.5, rationale="Fixture"),
        {"digest": DIGEST, "quality": QUALITY, "goal_summaries": board},
        ResearchProfile(themes=["Evaluation"], projects=["Harness"]),
    )

    assert proposal.proposed == "could_read"
    assert proposal.confidence < 0.6
    assert action == ""
    assert "goals_not_assessed" in flags
    assert card.worth_reading == "unknown"
    assert "goals_not_assessed" in card.evidence_gaps


@pytest.mark.parametrize("board, expected", [
    ([MISS], "dont_read"), ([MISS, MISS], "dont_read"),
    ([HIT], "could_read"), ([MISS, HIT], "could_read"),
])
def test_only_explicit_complete_miss_licenses_hide(board, expected):
    assert propose.propose_verdict(DIGEST, QUALITY, goal_summaries=board).proposed == expected
    action, _ = propose.effective_read_decision(DIGEST, QUALITY, goal_summaries=board)
    assert action == "skip"


def test_cached_review_projection_withholds_legacy_skip_without_rewriting_source():
    entry = {"digest": DIGEST, "quality": QUALITY, "goal_summaries": [UNKNOWN]}
    _review_cache._write_one("PAPER001", entry)

    projected = _review_cache.get_cached_review("PAPER001")

    assert projected["digest"]["read_decision"] == ""
    assert projected["model_read_decision"] == "skip"
    assert "goals_not_assessed" in projected["reading_policy_flags"]
    assert _review_cache._read_all()["PAPER001"] == entry


@pytest.mark.parametrize("board", [[UNKNOWN], [ABSTAINED_HIT], [MISS, UNKNOWN]])
def test_missing_digest_does_not_invent_a_negative_brief_verdict(board):
    from zotero_summarizer.services.library import _paper_read_brief

    html = _paper_read_brief.brief_html({}, quality=QUALITY, goal_summaries=board)

    assert "REVIEW" in html
    assert "none of your research goals are addressed" not in html
    assert "OFF GOAL" not in html


@pytest.mark.parametrize("fields", [{}, {"relevant": None}, {"relevant": 0}, {"relevant": "false"}])
@pytest.mark.parametrize("batched", [False, True])
def test_facet_response_cannot_default_or_coerce_an_assessed_miss(fields, batched):
    from zotero_summarizer.services.library._paper_goal_summaries import BatchedGoalResponse, GoalFacetResponse

    with pytest.raises(ValidationError):
        if batched:
            BatchedGoalResponse.model_validate({"summaries": [{"goal_index": 0, **fields}]})
        else:
            GoalFacetResponse.model_validate(fields)
