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


# Training-row filter — Sprint 3+ (May 2026). Drop only `meta` (library
# items with zero positive engagement; truly no signal) and `in_trash`
# (user explicitly removed). `first_glance` rows used to be dropped as
# pure noise, but the user pointed out that mass UI auto-rejects still
# represent a real "skimmed title+abstract, decided no" judgement —
# weaker than a 🧠 tag but stronger than nothing. We now keep them and
# down-weight via :mod:`services.label_weights` (`WEIGHT_GLANCE = 0.2`).
TRAINING_DROP_TIERS = frozenset({"meta"})


def is_training_eligible(row: dict[str, str]) -> bool:
    """Return True iff this CSV row should enter the supervised training set.

    Implements the F5 (in_trash) + Sprint-3+ (drop only `meta`) hygiene
    cut. Rows still need `gold_priority_final` / `title` / `abstract`
    populated — callers check those separately because the empty checks
    differ slightly per code path (some also require
    `gold_inferred_relevance`). Weighting per row is decoupled — see
    :func:`services.label_weights.compute_row_weights`.
    """
    if str(row.get("in_trash", "")).strip().lower() in ("true", "1"):
        return False
    tier = (row.get("gold_signal_tier") or "").strip()
    if tier in TRAINING_DROP_TIERS:
        return False
    return True


def score_to_priority(score: float) -> str:
    """Deterministic mapping of a continuous relevance score in [1, 5]
    to the four-class `ReadingPriority` label, using the thresholds defined
    above.

    Used by the regression-based classifier (Sprint 1) to translate the
    regressor's output into a label that the UI / Zotero notes / pending
    changes still consume. Single source of truth — do not re-derive
    thresholds anywhere else.

    must_read   if score >= 4.5
    should_read if 3.6 <= score < 4.5
    could_read  if 2.6 <= score < 3.6
    dont_read   if score < 2.6
    """
    if score >= PRIORITY_MUST_READ_THRESHOLD:
        return ReadingPriority.MUST_READ.value
    if score >= PRIORITY_SHOULD_READ_THRESHOLD:
        return ReadingPriority.SHOULD_READ.value
    if score >= PRIORITY_COULD_READ_THRESHOLD:
        return ReadingPriority.COULD_READ.value
    return ReadingPriority.DONT_READ.value

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