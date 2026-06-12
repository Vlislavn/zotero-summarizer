"""Per-row training weights (Sprint-3+ wiring, May 2026).

The user pointed out that mass UI auto-rejects (tier=first_glance) are
NOT zero-information — they at least mean "I skimmed the title and
abstract enough to decide no". Dropping them entirely from training
discards weak-but-real signal. Instead, this module assigns each
training row a continuous weight in [0, 1] reflecting how confident
the supervisor (Zotero engagement → user) is.

Weights are mapped from the `gold_signal_tier` audit column produced
by :func:`goldenset._format_tier_audit` plus a small number of side-
channel fields (annotation_count, note_count). The mapping is
intentionally chunked, not continuous — every chunk has a clear
operational meaning so a future label refactor stays auditable.

LightGBM accepts `sample_weight=...` natively; sklearn Ridge accepts
the same kwarg. The regression objective stays unchanged — only the
gradient contribution per row scales.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


# Chunked tier → weight mapping. Higher = stronger supervision signal.
WEIGHT_HIGH = 1.0   # explicit positive engagement: 🧠/annotations≥3/notes≥2
WEIGHT_MED = 0.7    # any positive engagement: notes=1 / ann=1-2 / soft emoji
WEIGHT_REVIEW = 0.5  # deliberate UI relabel (must/should/could/dont via Feed Review)
WEIGHT_INTEREST = 0.3  # soft pre-read "Add to library" interest signal (Today)
WEIGHT_GLANCE = 0.2  # UI batch auto-reject (skimmed title+abstract only)
WEIGHT_VETO = 1.0   # explicit hard veto: 👎/🥱/❌


_HIGH_TIER_SUBSTRINGS = ("strong_positive", "critical_engagement")
_MED_TIER_SUBSTRINGS = ("high_positive", "medium_positive", "notes=", "ann=")


def _tier_weight(tier: str, ann_count: int, note_count: int) -> float:
    """Derive a single weight from the audit string + counts.

    The audit string is pipe-joined segments (``goldenset._format_tier_audit``
    convention, e.g. ``"medium_positive|notes=1"``). Exact-match tiers are
    checked against the FIRST segment, so a suffixed tier inherits its base
    weight instead of falling through to the 0.7 legacy default.

    Precedence (first match wins):
      * any ``outcome_*`` segment → WEIGHT_REVIEW (the 7-day materialization
        outcome resolved — an observed behaviour is as informative as a
        deliberate Review click; see ``services.golden.hybrid_gt``)
      * user_label     → WEIGHT_HIGH (explicit ``label:<priority>`` verdict —
                          your deliberate, decay-immune ground truth; it must
                          weigh at least as much as any engagement signal, never
                          the 0.7 fall-through it used to get)
      * hard_veto      → WEIGHT_VETO
      * feed_user_label → WEIGHT_REVIEW
      * feed_interest  → WEIGHT_INTEREST (soft "Add to library" pre-read signal)
      * first_glance   → WEIGHT_GLANCE
      * trash / meta   → WEIGHT_GLANCE (kept for back-compat; usually
                          already filtered by `is_training_eligible`)
      * `ann_count >= 3` OR `note_count >= 2` OR strong_positive/critical → WEIGHT_HIGH
      * any other positive engagement marker → WEIGHT_MED
      * empty tier (legacy CSV row) → WEIGHT_MED
    """
    parts = [p for p in tier.split("|") if p]
    if any(p.startswith("outcome_") for p in parts):
        return WEIGHT_REVIEW
    base = parts[0] if parts else ""
    if base == "user_label":
        return WEIGHT_HIGH
    if base == "hard_veto":
        return WEIGHT_VETO
    if base == "feed_user_label":
        return WEIGHT_REVIEW
    if base == "feed_interest":
        return WEIGHT_INTEREST
    if base in ("first_glance", "meta", "trash"):
        return WEIGHT_GLANCE
    if ann_count >= 3 or note_count >= 2:
        return WEIGHT_HIGH
    if any(s in tier for s in _HIGH_TIER_SUBSTRINGS):
        return WEIGHT_HIGH
    if any(s in tier for s in _MED_TIER_SUBSTRINGS):
        return WEIGHT_MED
    return WEIGHT_MED


def _safe_int(s: object) -> int:
    """Tolerant int parse for CSV strings; non-numeric returns 0."""
    if isinstance(s, (int, float)):
        return int(s)
    if not isinstance(s, str):
        return 0
    s = s.strip()
    if not s or not s.lstrip("-").isdigit():
        return 0
    return int(s)


def compute_row_weights(
    rows: Iterable[dict[str, str]],
) -> np.ndarray:
    """Return a float32 array of per-row weights in the order rows arrive."""
    out: list[float] = []
    for r in rows:
        tier = (r.get("gold_signal_tier") or "").strip()
        ann = _safe_int(r.get("annotation_count"))
        notes = _safe_int(r.get("note_count"))
        out.append(_tier_weight(tier, ann, notes))
    return np.asarray(out, dtype=np.float32)


# Band balance was TRIED here (June 2026) and deliberately NOT shipped:
# inverse-sqrt band-frequency multipliers (sklearn class_weight='balanced'
# smoothed à la Mahajan 2018, alpha=0.5, cap 4.0, fold-train counts) did NOT
# move OOF must_read recall (stuck at ~2% — with ~53 rows the regressor simply
# never emits >4.5) and CUT dont_read recall 0.80 → 0.72, i.e. ~9 pts more
# junk through the live gate. Measured on the full golden CSV, 5-fold grouped
# OOF, 2026-06-12. The top band is label-starved, not gradient-starved — the
# lever is gathering more explicit must_read verdicts, not loss reweighting.
