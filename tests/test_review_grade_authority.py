from copy import deepcopy

import pytest

from tests.test_review_fleet_propose import _digest, _goals, _quality
from zotero_summarizer.services.library.review_fleet.propose import propose_verdict


@pytest.mark.parametrize("digest_grade,quality_grade,priority", [
    ("A", "D", "could_read"),
    ("D", "A", "should_read"),
    ("B", "C", "could_read"),
    ("C", "B", "should_read"),
])
def test_proposal_uses_the_available_independent_quality_grade(digest_grade, quality_grade, priority):
    digest = _digest(read_decision="skim", grade=digest_grade)
    quality = _quality(band="neutral", grade=quality_grade)
    original = deepcopy((digest, quality))
    result = propose_verdict(digest, quality, goal_summaries=_goals(matched=True))
    assert result.grade == quality_grade
    assert result.proposed == priority
    assert (digest, quality) == original
    assert result.digest_read_decision == "skim"


@pytest.mark.parametrize("quality,grade,priority", [
    (None, "A", "should_read"),
    ({"grade": ""}, "", "could_read"),
    ({"grade": "unassessed"}, "", "could_read"),
])
def test_proposal_distinguishes_legacy_absence_from_explicit_unassessed_quality(quality, grade, priority):
    digest = _digest(read_decision="skim", grade="A")
    before = deepcopy((digest, quality))
    result = propose_verdict(digest, quality, goal_summaries=_goals(matched=True))
    assert result.grade == grade
    assert result.proposed == priority
    assert (digest, quality) == before
