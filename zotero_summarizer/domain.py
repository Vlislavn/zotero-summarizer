from __future__ import annotations

from enum import Enum


class ReadingPriority(str, Enum):
    MUST_READ = "must_read"
    SHOULD_READ = "should_read"
    COULD_READ = "could_read"
    DONT_READ = "dont_read"


READING_PRIORITY_VALUES = tuple(priority.value for priority in ReadingPriority)
POSITIVE_READING_PRIORITIES = frozenset(
    {
        ReadingPriority.MUST_READ.value,
        ReadingPriority.SHOULD_READ.value,
    }
)
READING_PRIORITY_SORT_RANK = {
    ReadingPriority.MUST_READ.value: 4,
    ReadingPriority.SHOULD_READ.value: 3,
    ReadingPriority.COULD_READ.value: 2,
    ReadingPriority.DONT_READ.value: 1,
}


class FeedbackVerdict(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class FeedbackSignal(str, Enum):
    EXPLICIT_APPROVE = "explicit_approve"
    EXPLICIT_REJECT = "explicit_reject"


EXPLICIT_FEEDBACK_SIGNALS = (
    FeedbackSignal.EXPLICIT_APPROVE.value,
    FeedbackSignal.EXPLICIT_REJECT.value,
)


class ChangeStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


PRIORITY_MUST_READ_THRESHOLD = 4.5
PRIORITY_SHOULD_READ_THRESHOLD = 3.6
PRIORITY_COULD_READ_THRESHOLD = 2.6

TRIAGE_APPROVED_TAG = "✅ triage-approved"
TRIAGE_REJECTED_TAG = "🚫 triage-rejected"
TRIAGE_APPROVED_TAG_TOKEN = "triage-approved"
TRIAGE_REJECTED_TAG_TOKEN = "triage-rejected"


def is_valid_reading_priority(value: str) -> bool:
    return value in READING_PRIORITY_VALUES


def normalize_reading_priority(value: str, default: str = ReadingPriority.COULD_READ.value) -> str:
    if is_valid_reading_priority(value):
        return value
    return default


def is_positive_priority(value: str) -> bool:
    return value in POSITIVE_READING_PRIORITIES


def feedback_signal_from_verdict(verdict: str) -> str:
    if verdict == FeedbackVerdict.APPROVE.value:
        return FeedbackSignal.EXPLICIT_APPROVE.value
    if verdict == FeedbackVerdict.REJECT.value:
        return FeedbackSignal.EXPLICIT_REJECT.value
    raise ValueError(f"Unsupported verdict: {verdict}")


def feedback_verdict_from_signal(signal: str) -> str | None:
    if signal == FeedbackSignal.EXPLICIT_APPROVE.value:
        return FeedbackVerdict.APPROVE.value
    if signal == FeedbackSignal.EXPLICIT_REJECT.value:
        return FeedbackVerdict.REJECT.value
    return None