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


# Single source of truth for the continuous-score → 4-class boundaries.
# These MUST match the bins used to *derive* labels from engagement
# (services.emoji_signals.priority_for_score) — the regressor trains on a
# continuous relevance target and is binned back with score_to_priority, so a
# divergence silently mis-classifies every borderline paper. Bins:
#   dont_read  < 2.0 <= could_read < 3.5 <= should_read < 4.5 <= must_read
PRIORITY_MUST_READ_THRESHOLD = 4.5
PRIORITY_SHOULD_READ_THRESHOLD = 3.5
PRIORITY_COULD_READ_THRESHOLD = 2.0

# Canonical class → continuous relevance value, used whenever a 4-class label
# must be written as a regression target (user verdicts, review-appended rows,
# gate-only synthesis). Each value MUST round-trip through score_to_priority
# (e.g. should_read=4.0 → score_to_priority(4.0)=="should_read"); 4.5 would
# silently land on the must_read boundary, so should_read is 4.0.
PRIORITY_TO_RELEVANCE: dict[str, float] = {
    ReadingPriority.MUST_READ.value: 5.0,
    ReadingPriority.SHOULD_READ.value: 4.0,
    ReadingPriority.COULD_READ.value: 3.0,
    ReadingPriority.DONT_READ.value: 1.0,
}


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


_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:")


def normalize_doi(doi: str) -> str:
    """Canonicalise a DOI to its bare, lower-cased form.

    Strips the common URL/scheme prefixes and a trailing slash so that
    ``https://doi.org/10.1/X``, ``doi:10.1/x`` and ``10.1/x`` all compare equal.
    Single source of truth for DOI comparison (dedup, grouping). Returns "" for
    empty/blank input.
    """
    value = (doi or "").strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value.rstrip("/")


def paper_group_id(row: dict[str, str]) -> str:
    """Stable per-paper identity for grouped cross-validation.

    The same paper can appear as multiple golden-CSV rows (a ``feed:<id>`` row
    from triage AND a Zotero-key row once it's added/read). Random K-fold would
    split these near-identical-embedding twins across train/test, leaking signal
    and inflating the metrics. We group by DOI when present, else by a
    normalised title, else fall back to the row's own ``item_key`` so genuinely
    distinct rows are never merged into one group.
    """
    doi = normalize_doi(row.get("doi") or "")
    if doi:
        return f"doi:{doi}"
    title = " ".join((row.get("title") or "").lower().split())
    if title:
        return f"title:{title}"
    return f"key:{(row.get('item_key') or '').strip()}"


def score_to_priority(score: float) -> str:
    """Deterministic mapping of a continuous relevance score in [1, 5]
    to the four-class `ReadingPriority` label, using the thresholds defined
    above.

    Used by the regression-based classifier (Sprint 1) to translate the
    regressor's output into a label that the UI / Zotero notes / pending
    changes still consume. Single source of truth — do not re-derive
    thresholds anywhere else.

    must_read   if score >= 4.5
    should_read if 3.5 <= score < 4.5
    could_read  if 2.0 <= score < 3.5
    dont_read   if score < 2.0
    """
    if score >= PRIORITY_MUST_READ_THRESHOLD:
        return ReadingPriority.MUST_READ.value
    if score >= PRIORITY_SHOULD_READ_THRESHOLD:
        return ReadingPriority.SHOULD_READ.value
    if score >= PRIORITY_COULD_READ_THRESHOLD:
        return ReadingPriority.COULD_READ.value
    return ReadingPriority.DONT_READ.value


# One-band demotion for the prestige/quality floor. The top bands stay
# high-quality: a top-tier item whose KNOWN prestige is below a data-derived
# floor drops one band, one step at a time. Demote-only.
_DEMOTE_ONE_BAND: dict[str, str] = {
    ReadingPriority.MUST_READ.value: ReadingPriority.SHOULD_READ.value,
    ReadingPriority.SHOULD_READ.value: ReadingPriority.COULD_READ.value,
}


def apply_prestige_floor(
    priority: str,
    prestige_score: float | None,
    *,
    prestige_known: bool,
    floor: float | None,
) -> str:
    """Quality floor on the top bands: demote ``priority`` one step when its
    KNOWN prestige is below ``floor`` (must_read→should_read,
    should_read→could_read). Pure post-band policy — the raw score and
    :func:`score_to_priority` are untouched (the round-trip invariant holds; only
    the displayed/applied band changes).

    Never penalises missing evidence: ``floor is None`` (no known prestige in the
    library → inert), ``prestige_known is False`` (no OpenAlex record / cold-start
    with no signal), or ``prestige_score is None`` all return ``priority``
    unchanged. could_read / dont_read are never demoted."""
    if floor is None or not prestige_known or prestige_score is None:
        return priority
    if float(prestige_score) >= float(floor):
        return priority
    return _DEMOTE_ONE_BAND.get(priority, priority)


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