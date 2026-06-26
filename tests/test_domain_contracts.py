from __future__ import annotations

import pytest
from pydantic import ValidationError

from zotero_summarizer.domain import (
    FeedbackSignal,
    FeedbackVerdict,
    ReadingPriority,
    feedback_signal_from_verdict,
    feedback_verdict_from_signal,
    normalize_reading_priority,
    paper_group_id,
)
from zotero_summarizer.models import PendingPriorityOverrideRequest, RefinedSummary, TriageResult


def test_paper_group_id_unites_feed_and_zotero_rows_by_doi() -> None:
    # The same paper as a feed:* triage row and a Zotero-key library row must
    # share a group (DOI scheme variants normalise to the same id).
    feed = {"item_key": "feed:42", "doi": "https://doi.org/10.1/AB", "title": "T"}
    zot = {"item_key": "ABCD1234", "doi": "10.1/ab", "title": "different title"}
    assert paper_group_id(feed) == paper_group_id(zot) == "doi:10.1/ab"


def test_paper_group_id_falls_back_to_title_then_key() -> None:
    a = {"item_key": "feed:1", "doi": "", "title": "  Hello   World "}
    b = {"item_key": "ZZZ", "doi": "", "title": "hello world"}
    assert paper_group_id(a) == paper_group_id(b) == "title:hello world"
    # No DOI and no title → the row's own key, so distinct rows never merge.
    assert paper_group_id({"item_key": "K1"}) == "key:K1"
    assert paper_group_id({"item_key": "K2"}) != paper_group_id({"item_key": "K1"})


def test_paper_group_id_distinct_papers_differ() -> None:
    assert paper_group_id({"doi": "10.1/x"}) != paper_group_id({"doi": "10.1/y"})


def test_feedback_signal_round_trip() -> None:
    approve_signal = feedback_signal_from_verdict(FeedbackVerdict.APPROVE.value)
    reject_signal = feedback_signal_from_verdict(FeedbackVerdict.REJECT.value)

    assert approve_signal == FeedbackSignal.EXPLICIT_APPROVE.value
    assert reject_signal == FeedbackSignal.EXPLICIT_REJECT.value
    assert feedback_verdict_from_signal(approve_signal) == FeedbackVerdict.APPROVE.value
    assert feedback_verdict_from_signal(reject_signal) == FeedbackVerdict.REJECT.value


def test_normalize_reading_priority_falls_back_to_default() -> None:
    normalized = normalize_reading_priority("urgent_read")

    assert normalized == ReadingPriority.COULD_READ.value


def test_triage_result_coerces_unknown_priority() -> None:
    result = TriageResult(score=3, reading_priority="custom_priority", rationale="baseline rationale")

    assert result.reading_priority == ReadingPriority.COULD_READ.value


def test_pending_priority_override_rejects_unknown_priority() -> None:
    with pytest.raises(ValidationError):
        PendingPriorityOverrideRequest(
            item_key="RGW9H2AL",
            item_title="Paper",
            new_priority="urgent_read",
        )


def test_refined_summary_rejects_non_string_list_items() -> None:
    with pytest.raises(ValidationError):
        RefinedSummary(
            executive_summary="Summary",
            controversial_points=["valid", 42],
        )
