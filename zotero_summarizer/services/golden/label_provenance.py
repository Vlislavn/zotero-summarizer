"""Phase 1.18 Step 1 — label-provenance audit tool.

Mirrors :func:`services.goldenset._infer_label` (the additive-scoring
chain that derives ``gold_priority_final`` from emoji tags, annotations,
notes, trash, and age decay) but exposes EVERY contribution as a
structured breakdown the user can inspect.

The goal: for any paper in the golden CSV, answer "WHY does it have this
priority?" — the chain of reasoning that produced it. Without this tool,
the user cannot audit the ground-truth labels, and therefore cannot
trust any validation metric (Precision@5, Spearman ρ, calibration)
computed against those labels.

This module is a PURE FUNCTION: same inputs → same outputs, no I/O, no
state. Tests verify it returns the same final priority as
``_infer_label`` for every input combination.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zotero_summarizer.services import emoji_signals


@dataclass(frozen=True)
class EmojiContribution:
    """One emoji's contribution to the engagement score."""

    emoji: str
    description: str
    tier: str
    raw_delta: float       # base score_delta from emoji_signals.SIGNALS
    decayed_delta: float   # raw_delta * decay_weight, applied to engagement_sum


@dataclass(frozen=True)
class LabelProvenance:
    """Full derivation breakdown for one paper's gold_priority_final.

    Reproduces the math in ``services.goldenset._infer_label`` step-by-step
    so the user can see each contribution.
    """

    # Identity
    item_key: str
    title: str

    # Final outputs
    derived_priority: str
    derived_score: float        # the [1.0, 5.0] inferred relevance
    derived_strength: str

    # Hard short-circuits (applied BEFORE additive scoring)
    in_trash_override: bool     # True → forces dont_read (score=1.0)
    hard_veto_emojis: list[str] # if non-empty → forces dont_read (score=1.0)

    # Additive-scoring trace (only populated if no hard short-circuit fired)
    baseline: float             # NEUTRAL_SCORE (3.0)
    emoji_contributions: list[EmojiContribution]
    annotation_count: int
    annotation_score_raw: float       # uncapped: count * ANNOTATION_SCORE_DELTA
    annotation_score_capped: float    # min(raw, ANNOTATION_SCORE_CAP)
    annotation_decayed: float         # capped * decay_weight
    user_note_count: int
    user_note_score_raw: float        # uncapped: count * NOTE_SCORE_DELTA
    user_note_score_capped: float
    user_note_decayed: float
    days_since_added: int
    decay_factor: float               # 0.5 ** (days / 180)
    engagement_sum_raw: float         # emoji + annotation + note (uncapped/decayed)
    engagement_sum_capped: float      # after caps applied
    engagement_sum_decayed: float     # after caps + decay

    # Thresholds used to bin the final score → priority class
    threshold_dont_read_upper: float
    threshold_could_read_upper: float
    threshold_should_read_upper: float

    # Three trust tiers for the persisted label:
    # - is_direct_user_verdict: True for feed:* / note:* rows. The persisted
    #   priority came directly from a button click in the Feed Review UI;
    #   the derivation does NOT apply (raw signals are mostly empty). Trust
    #   the persisted value, ignore the derived one.
    # - is_manual_override: True for library rows where derivation disagrees
    #   with persisted. Strongest positive signal: user typed it.
    # - Neither: derivation == persisted; the label is purely auto-derived.
    persisted_priority: str
    persisted_score: float
    is_direct_user_verdict: bool
    is_manual_override: bool

    # Flags for the UI
    flags: list[str] = field(default_factory=list)


def compute_provenance(
    *,
    item_key: str,
    title: str,
    tags: list[str],
    in_trash: bool,
    annotation_count: int,
    user_note_count: int,
    days_since_added: int,
    persisted_priority: str = "",
    persisted_score: float = 0.0,
    is_direct_user_verdict: bool = False,
) -> LabelProvenance:
    """Compute the full provenance breakdown for one paper.

    Inputs MUST be raw (uncapped/un-decayed). This function applies the
    same math as :func:`services.goldenset._infer_label` and exposes every
    intermediate value.

    ``is_direct_user_verdict``: True for golden-CSV rows whose ``item_key``
    is namespaced (``feed:*`` / ``note:*``). Those persisted labels come
    from button clicks in the Feed Review UI; the additive derivation does
    NOT apply (raw signals are empty). The persisted value is authoritative.
    """
    flags: list[str] = []

    # Hard short-circuit 1: trash → dont_read
    if in_trash:
        return LabelProvenance(
            item_key=item_key,
            title=title,
            derived_priority="dont_read",
            derived_score=1.0,
            derived_strength="high",
            in_trash_override=True,
            hard_veto_emojis=[],
            baseline=emoji_signals.NEUTRAL_SCORE,
            emoji_contributions=[],
            annotation_count=annotation_count,
            annotation_score_raw=0.0,
            annotation_score_capped=0.0,
            annotation_decayed=0.0,
            user_note_count=user_note_count,
            user_note_score_raw=0.0,
            user_note_score_capped=0.0,
            user_note_decayed=0.0,
            days_since_added=days_since_added,
            decay_factor=emoji_signals.decay_weight(days_since_added),
            engagement_sum_raw=0.0,
            engagement_sum_capped=0.0,
            engagement_sum_decayed=0.0,
            threshold_dont_read_upper=emoji_signals.SCORE_DONT_READ_UPPER,
            threshold_could_read_upper=emoji_signals.SCORE_COULD_READ_UPPER,
            threshold_should_read_upper=emoji_signals.SCORE_SHOULD_READ_UPPER,
            persisted_priority=persisted_priority,
            persisted_score=persisted_score,
            is_direct_user_verdict=is_direct_user_verdict,
            is_manual_override=_is_manual_override(
                "dont_read", persisted_priority, is_direct_user_verdict,
            ),
            flags=flags,
        )

    # Hard short-circuit 2: hard-veto emoji → dont_read
    veto_emojis = [
        e for e in emoji_signals.HARD_VETO_EMOJIS
        if any(e in t for t in tags if t)
    ]
    if veto_emojis:
        return LabelProvenance(
            item_key=item_key,
            title=title,
            derived_priority="dont_read",
            derived_score=1.0,
            derived_strength="high",
            in_trash_override=False,
            hard_veto_emojis=veto_emojis,
            baseline=emoji_signals.NEUTRAL_SCORE,
            emoji_contributions=[],
            annotation_count=annotation_count,
            annotation_score_raw=0.0,
            annotation_score_capped=0.0,
            annotation_decayed=0.0,
            user_note_count=user_note_count,
            user_note_score_raw=0.0,
            user_note_score_capped=0.0,
            user_note_decayed=0.0,
            days_since_added=days_since_added,
            decay_factor=emoji_signals.decay_weight(days_since_added),
            engagement_sum_raw=0.0,
            engagement_sum_capped=0.0,
            engagement_sum_decayed=0.0,
            threshold_dont_read_upper=emoji_signals.SCORE_DONT_READ_UPPER,
            threshold_could_read_upper=emoji_signals.SCORE_COULD_READ_UPPER,
            threshold_should_read_upper=emoji_signals.SCORE_SHOULD_READ_UPPER,
            persisted_priority=persisted_priority,
            persisted_score=persisted_score,
            is_direct_user_verdict=is_direct_user_verdict,
            is_manual_override=_is_manual_override(
                "dont_read", persisted_priority, is_direct_user_verdict,
            ),
            flags=flags,
        )

    # Additive scoring path
    signals = emoji_signals.detect_signals(tags)
    decay = emoji_signals.decay_weight(days_since_added)

    emoji_contribs: list[EmojiContribution] = []
    emoji_sum_raw = 0.0
    for s in signals:
        emoji_contribs.append(
            EmojiContribution(
                emoji=s.emoji,
                description=s.description,
                tier=s.tier,
                raw_delta=s.score_delta,
                decayed_delta=s.score_delta * decay,
            )
        )
        emoji_sum_raw += s.score_delta

    ann_raw = annotation_count * emoji_signals.ANNOTATION_SCORE_DELTA
    ann_capped = min(ann_raw, emoji_signals.ANNOTATION_SCORE_CAP)
    note_raw = user_note_count * emoji_signals.NOTE_SCORE_DELTA
    note_capped = min(note_raw, emoji_signals.NOTE_SCORE_CAP)

    engagement_raw = emoji_sum_raw + ann_raw + note_raw
    engagement_capped = emoji_sum_raw + ann_capped + note_capped
    engagement_decayed = engagement_capped * decay

    # Order-of-operations matters: bin BEFORE rounding (matches _infer_label
    # in services/goldenset.py which calls priority_for_score on the
    # unrounded score, then rounds for display). Otherwise rows whose
    # unrounded score is 3.498 → rounded 3.5 → wrongly binned as should_read.
    raw_score = emoji_signals.NEUTRAL_SCORE + engagement_decayed
    unrounded_score = max(1.0, min(5.0, raw_score))
    priority = emoji_signals.priority_for_score(unrounded_score)
    strength = emoji_signals.strength_for_score(
        unrounded_score, num_signals=len(signals),
    )
    final_score = round(float(unrounded_score), 2)

    # Flags for the UI: borderline / weak / strong derivations
    if priority == "must_read" and len(signals) <= 1 and annotation_count == 0 and user_note_count == 0:
        flags.append("weak_must_read")  # single-signal must_read = borderline
    if priority == "must_read" and decay < 0.3:
        flags.append("heavily_decayed")  # must_read with > 270d decay → distrust
    if final_score >= emoji_signals.SCORE_SHOULD_READ_UPPER - 0.2 and priority == "should_read":
        flags.append("near_must_read")  # within 0.2 of the must_read boundary

    return LabelProvenance(
        item_key=item_key,
        title=title,
        derived_priority=priority,
        derived_score=final_score,
        derived_strength=strength,
        in_trash_override=False,
        hard_veto_emojis=[],
        baseline=emoji_signals.NEUTRAL_SCORE,
        emoji_contributions=emoji_contribs,
        annotation_count=annotation_count,
        annotation_score_raw=round(ann_raw, 4),
        annotation_score_capped=round(ann_capped, 4),
        annotation_decayed=round(ann_capped * decay, 4),
        user_note_count=user_note_count,
        user_note_score_raw=round(note_raw, 4),
        user_note_score_capped=round(note_capped, 4),
        user_note_decayed=round(note_capped * decay, 4),
        days_since_added=days_since_added,
        decay_factor=round(decay, 4),
        engagement_sum_raw=round(engagement_raw, 4),
        engagement_sum_capped=round(engagement_capped, 4),
        engagement_sum_decayed=round(engagement_decayed, 4),
        threshold_dont_read_upper=emoji_signals.SCORE_DONT_READ_UPPER,
        threshold_could_read_upper=emoji_signals.SCORE_COULD_READ_UPPER,
        threshold_should_read_upper=emoji_signals.SCORE_SHOULD_READ_UPPER,
        persisted_priority=persisted_priority,
        persisted_score=persisted_score,
        is_direct_user_verdict=is_direct_user_verdict,
        is_manual_override=_is_manual_override(
            priority, persisted_priority, is_direct_user_verdict,
        ),
        flags=flags,
    )


def _is_manual_override(
    derived: str, persisted: str, is_direct_user_verdict: bool,
) -> bool:
    """True if the persisted CSV priority disagrees with the derivation
    AND the persisted value is non-empty (i.e., not just unset)
    AND the row is NOT a direct user verdict (those bypass derivation
    entirely, so a disagreement is expected, not an override)."""
    if is_direct_user_verdict:
        return False
    if not persisted:
        return False
    return derived != persisted


# ---------------------------------------------------------------------------
# Golden-CSV row -> compute_provenance bridge
# ---------------------------------------------------------------------------


def provenance_from_row(row: dict[str, str]) -> LabelProvenance:
    """Build a LabelProvenance from one ``zotero-summarizer-golden.csv`` row.

    The CSV stores the raw inputs (matched_emojis, annotation_count,
    note_count, in_trash, days_since_added) alongside the derived label.
    Fails fast on missing required fields.
    """
    item_key = (row.get("item_key") or "").strip()
    if not item_key:
        raise ValueError("row missing item_key")
    title = (row.get("title") or "").strip()
    tags = [t for t in (row.get("matched_emojis") or "").split() if t]
    in_trash = (row.get("in_trash") or "").strip().lower() == "true"
    annotation_count = _parse_int(
        row.get("annotation_count"), default=0, field_name="annotation_count",
    )
    note_count = _parse_int(
        row.get("note_count"), default=0, field_name="note_count",
    )
    days_since_added = _parse_int(
        row.get("days_since_added"), default=0, field_name="days_since_added",
    )
    persisted_priority = (row.get("gold_priority_final") or "").strip()
    persisted_score = _parse_float(
        row.get("gold_inferred_relevance"), default=0.0,
        field_name="gold_inferred_relevance",
    )

    # Direct user-verdict rows (item_key has a colon namespace prefix)
    # come from button clicks in the Feed Review UI, not from the
    # additive derivation. The persisted label is authoritative for them.
    is_direct_user_verdict = ":" in item_key

    return compute_provenance(
        item_key=item_key,
        title=title,
        tags=tags,
        in_trash=in_trash,
        annotation_count=annotation_count,
        user_note_count=note_count,
        days_since_added=days_since_added,
        persisted_priority=persisted_priority,
        persisted_score=persisted_score,
        is_direct_user_verdict=is_direct_user_verdict,
    )


def _parse_int(value: Any, *, default: int, field_name: str) -> int:
    """Parse a CSV column as int. Empty string → default (the column is
    legitimately absent for some rows). Non-empty garbage → ValueError,
    fail fast so the user knows their CSV is malformed."""
    if value is None or str(value).strip() == "":
        return default
    return int(value)  # raises ValueError on garbage — caller must NOT mask


def _parse_float(value: Any, *, default: float, field_name: str) -> float:
    """Parse a CSV column as float. Same contract as :func:`_parse_int`."""
    if value is None or str(value).strip() == "":
        return default
    return float(value)  # raises ValueError on garbage — caller must NOT mask


# ---------------------------------------------------------------------------
# Bulk loading + search
# ---------------------------------------------------------------------------


def load_golden_provenance(csv_path: Path) -> list[LabelProvenance]:
    """Load every row from the golden CSV and compute its provenance."""
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV not found at {csv_path}")
    out: list[LabelProvenance] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(provenance_from_row(row))
    return out


def find_provenance(provs: list[LabelProvenance], item_key: str) -> LabelProvenance:
    """Return the provenance for one item_key. Raises if not found."""
    for p in provs:
        if p.item_key == item_key:
            return p
    raise KeyError(f"item_key {item_key!r} not found in golden CSV")


def flag_summary(provs: list[LabelProvenance]) -> dict[str, list[str]]:
    """Return {flag_name: [item_keys with that flag]} for UI surfacing."""
    out: dict[str, list[str]] = {}
    for p in provs:
        for f in p.flags:
            out.setdefault(f, []).append(p.item_key)
        if p.is_manual_override:
            out.setdefault("manual_override", []).append(p.item_key)
    return out


def provenance_to_dict(p: LabelProvenance) -> dict[str, Any]:
    """JSON-serializable view for the API + UI."""
    return {
        "item_key": p.item_key,
        "title": p.title,
        "derived_priority": p.derived_priority,
        "derived_score": p.derived_score,
        "derived_strength": p.derived_strength,
        "persisted_priority": p.persisted_priority,
        "persisted_score": p.persisted_score,
        "is_direct_user_verdict": p.is_direct_user_verdict,
        "is_manual_override": p.is_manual_override,
        "flags": list(p.flags),
        "short_circuits": {
            "in_trash_override": p.in_trash_override,
            "hard_veto_emojis": list(p.hard_veto_emojis),
        },
        "additive_scoring": {
            "baseline": p.baseline,
            "emoji_contributions": [
                {
                    "emoji": c.emoji,
                    "description": c.description,
                    "tier": c.tier,
                    "raw_delta": c.raw_delta,
                    "decayed_delta": round(c.decayed_delta, 4),
                }
                for c in p.emoji_contributions
            ],
            "annotation_count": p.annotation_count,
            "annotation_score_raw": p.annotation_score_raw,
            "annotation_score_capped": p.annotation_score_capped,
            "annotation_decayed": p.annotation_decayed,
            "user_note_count": p.user_note_count,
            "user_note_score_raw": p.user_note_score_raw,
            "user_note_score_capped": p.user_note_score_capped,
            "user_note_decayed": p.user_note_decayed,
            "days_since_added": p.days_since_added,
            "decay_factor": p.decay_factor,
            "engagement_sum_raw": p.engagement_sum_raw,
            "engagement_sum_capped": p.engagement_sum_capped,
            "engagement_sum_decayed": p.engagement_sum_decayed,
        },
        "thresholds": {
            "dont_read_upper": p.threshold_dont_read_upper,
            "could_read_upper": p.threshold_could_read_upper,
            "should_read_upper": p.threshold_should_read_upper,
        },
    }
