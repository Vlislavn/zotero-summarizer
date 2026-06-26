"""QualityReview grade normalization + the row_quality parse contract."""
from __future__ import annotations

import pytest

from zotero_summarizer.models import QualityReview
from zotero_summarizer.services.triage.daily_select import _candidate as cand


def test_quality_review_grade_normalization():
    assert QualityReview(grade="a").grade == "A"
    assert QualityReview(grade="B)").grade == "B"
    assert QualityReview(grade="x").grade == ""


def test_row_quality_parse_and_contract():
    assert cand.row_quality({"quality_review_json": ""}) == {}
    assert cand.row_quality({"quality_review_json": '{"grade":"B"}'})["grade"] == "B"
    with pytest.raises(ValueError):
        cand.row_quality({"quality_review_json": "[1,2]"})  # JSON, but not an object
