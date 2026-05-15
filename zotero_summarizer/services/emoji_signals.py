"""Shared emoji-tag → engagement-signal taxonomy.

Used by:
- :mod:`services.goldenset` to derive gold labels for the golden set.
- :mod:`services.feedback` to convert implicit Zotero tags into feedback events.

The classification reflects the user's actual Zotero workflow (see emoji
inventory pulled 2026-05-13). Higher-priority tiers override lower-priority
tiers within a single item.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmojiSignal:
    """Additive score contribution for one emoji.

    Phase 1.14 (user-confirmed 2026-05-14): signals are no longer
    binary-tier classifiers. Each emoji contributes a ``score_delta`` to
    the item's overall engagement score. Multiple signals stack. The
    final priority comes from binning the aggregate score, not from
    picking the single highest tier. ``tier`` is kept as a metadata
    label for the audit trail (``gold_signal_tier`` column).
    """
    emoji: str
    description: str
    tier: str
    score_delta: float


# Tier labels are retained purely for the audit trail (``gold_signal_tier``
# in the golden CSV) — they no longer drive label assignment. Score deltas
# do that.
TIER_ORDER: tuple[str, ...] = (
    "strong_negative",
    "boring",
    "strong_positive",
    "high_positive",
    "medium_positive",
    "critical_engagement",
    "light_engagement",
    "meta",
)


# Catalogue. score_delta is the additive contribution to the engagement
# score (baseline 3.0 = neutral). Bounds: dont_read at ≤ 2.0, could_read
# 2.0–3.5, should_read 3.5–4.5, must_read ≥ 4.5.
SIGNALS: tuple[EmojiSignal, ...] = (
    # STRONG POSITIVE — single emoji already lifts to must_read.
    EmojiSignal("🧠", "distilled",        "strong_positive", +2.0),
    EmojiSignal("✅", "tried/applied",    "strong_positive", +2.0),
    EmojiSignal("🗝",  "key insight",     "strong_positive", +2.0),
    # HIGH POSITIVE — single emoji lands in should_read; combined with
    # 1-2 annotations easily crosses into must_read.
    EmojiSignal("👍", "agree/endorse",    "high_positive",   +1.5),
    EmojiSignal("💡", "idea generated",   "high_positive",   +1.5),
    # MEDIUM POSITIVE — single emoji = lower-bound should_read.
    EmojiSignal("👀", "skimmed",          "medium_positive", +1.0),
    EmojiSignal("🧪", "method extracted", "medium_positive", +1.0),
    EmojiSignal("🧮", "statistical method", "medium_positive", +1.0),
    # CRITICAL ENGAGEMENT — same weight as medium_positive (active reading
    # produces these). User-confirmed 2026-05-14.
    EmojiSignal("❓", "question raised",  "critical_engagement", +1.0),
    EmojiSignal("🧱", "limitation found", "critical_engagement", +1.0),
    EmojiSignal("⚡", "challenged claim", "critical_engagement", +1.0),
    # NEGATIVE — strong veto signals (Schnabel-aligned 6:1 vs +0.5
    # neutral-ignore, parity with OUTCOME_WEIGHT[deleted_all] = -3.0).
    EmojiSignal("👎", "thumbs down",      "strong_negative", -3.0),
    EmojiSignal("❌", "rejected",         "strong_negative", -3.0),
    # 🥱 is a HARD VETO (Phase 1.15, user-confirmed 2026-05-14) — handled
    # by the trash-equivalent short-circuit in `goldenset._infer_label`,
    # not by additive scoring. The score_delta here is informational only.
    EmojiSignal("🥱", "boring/off-topic", "boring",          -2.0),
    # META — no quality signal, 0 contribution.
    EmojiSignal("🤖", "AI-generated marker", "meta",          0.0),
    EmojiSignal("🔮", "vision/forecast",  "meta",             0.0),
    EmojiSignal("⚪", "neutral",          "meta",             0.0),
    EmojiSignal("🗣",  "recommended by",  "meta",             0.0),
)


_BY_EMOJI: dict[str, EmojiSignal] = {s.emoji: s for s in SIGNALS}


def detect_signals(tags: list[str]) -> set[EmojiSignal]:
    """Return all signals whose emoji appears in any of the tags."""
    out: set[EmojiSignal] = set()
    for tag in tags:
        if not tag:
            continue
        for emoji, signal in _BY_EMOJI.items():
            if emoji in tag:
                out.add(signal)
    return out


# Hard-veto emojis (Phase 1.15, user-confirmed 2026-05-14). Behaviour
# matches `in_trash`: any one of these on an item forces dont_read,
# bypassing additive scoring entirely. Use for explicit, irrevocable
# user verdicts. All three explicit negative emojis are hard vetoes;
# their numeric `score_delta` below is kept for documentation but
# never actually contributes (the short-circuit fires first).
HARD_VETO_EMOJIS: frozenset[str] = frozenset({"🥱", "👎", "❌"})


def has_hard_veto(tags: list[str]) -> bool:
    """True iff any tag contains a hard-veto emoji."""
    for tag in tags:
        if not tag:
            continue
        for emoji in HARD_VETO_EMOJIS:
            if emoji in tag:
                return True
    return False


# Time-decay (Phase 1.15, user-confirmed 2026-05-14: pure exponent from
# day 1). Engagement signals lose half their weight every
# DECAY_HALF_LIFE_DAYS. Applied to emoji + annotation + note
# contributions BUT NOT to the neutral baseline or trash/hard-veto
# short-circuits.
DECAY_HALF_LIFE_DAYS: float = 180.0


def decay_weight(days_since_added: int | float) -> float:
    """Engagement-signal half-life decay. Clamped to [0.0, 1.0]."""
    days = max(0.0, float(days_since_added))
    return 0.5 ** (days / DECAY_HALF_LIFE_DAYS)


# Additive scoring (Phase 1.14, user-confirmed 2026-05-14).
NEUTRAL_SCORE: float = 3.0
SCORE_DONT_READ_UPPER: float = 2.0      # < 2.0 → dont_read
SCORE_COULD_READ_UPPER: float = 3.5     # 2.0..3.5 → could_read
SCORE_SHOULD_READ_UPPER: float = 4.5    # 3.5..4.5 → should_read; ≥ 4.5 → must_read

# Annotation increment: small positive contribution per annotation, capped.
# 1 annotation alone is just a highlight (no priority bump above neutral).
# 5 annotations + a medium_positive emoji → should_read; 10+ pushes to must_read.
ANNOTATION_SCORE_DELTA: float = 0.25
ANNOTATION_SCORE_CAP: float = 2.0   # diminishing return at 8+ annotations

# Note increment: a written note is heavier than a single highlight (the user
# actually composed text). Capped lower because note count is rarely large.
NOTE_SCORE_DELTA: float = 0.5
NOTE_SCORE_CAP: float = 1.5


def score_signals(signals: set[EmojiSignal]) -> float:
    """Sum the score deltas of a set of emoji signals. Returns ``0.0`` for
    an empty set (caller adds the neutral baseline)."""
    return float(sum(s.score_delta for s in signals))


def score_annotations(count: int) -> float:
    """Capped additive contribution from PDF annotation count."""
    if count <= 0:
        return 0.0
    return min(count * ANNOTATION_SCORE_DELTA, ANNOTATION_SCORE_CAP)


def score_notes(count: int) -> float:
    """Capped additive contribution from manual-note count."""
    if count <= 0:
        return 0.0
    return min(count * NOTE_SCORE_DELTA, NOTE_SCORE_CAP)


def priority_for_score(score: float) -> str:
    """Bin an aggregate engagement score into the 4-class reading priority."""
    if score < SCORE_DONT_READ_UPPER:
        return "dont_read"
    if score < SCORE_COULD_READ_UPPER:
        return "could_read"
    if score < SCORE_SHOULD_READ_UPPER:
        return "should_read"
    return "must_read"


def strength_for_score(score: float, num_signals: int) -> str:
    """Confidence label (high / medium / low) derived from how many distinct
    signals agreed and how far the aggregate score is from neutral."""
    delta = abs(score - NEUTRAL_SCORE)
    if num_signals >= 2 or delta >= 1.5:
        return "high"
    if num_signals >= 1 or delta >= 0.75:
        return "medium"
    return "low"


# All emoji characters we recognise — used to build SQL LIKE clauses.
ALL_EMOJIS: tuple[str, ...] = tuple(s.emoji for s in SIGNALS)
