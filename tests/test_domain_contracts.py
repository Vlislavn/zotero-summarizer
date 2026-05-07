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
)
from zotero_summarizer.models import PendingPriorityOverrideRequest, RefinedSummary, TriageResult


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
