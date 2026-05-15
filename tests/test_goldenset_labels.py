"""goldenset._infer_label: additive engagement scoring (Phase 1.14).

Replaces the Phase 1.10 highest-tier-wins semantics with a sum-of-deltas
model — see :mod:`zotero_summarizer.services.emoji_signals`.

Score model (baseline 3.0, clamped to [1.0, 5.0]):

  emoji        score_delta        per-emoji audit
  -----        -----------        ----
  🧠 ✅ 🗝       +2.0              strong_positive
  👍 💡          +1.5              high_positive
  👀 🧪 🧮 ❓ 🧱 ⚡  +1.0          medium_positive | critical_engagement
  🥱             -1.5              boring
  👎 ❌          -2.5              strong_negative
  🤖 🔮 ⚪ 🗣    0.0               meta

  annotations  +0.25 each (cap +2.0)
  notes        +0.5 each (cap +1.5)
  in_trash     short-circuit → dont_read (1.0)

  bins         < 2.0  → dont_read
               2.0–3.5 → could_read
               3.5–4.5 → should_read
               ≥ 4.5  → must_read
"""

from __future__ import annotations

from zotero_summarizer.services.goldenset import _infer_label


def _call(tags: list[str], *, in_trash: bool = False, note_count: int = 0,
          annotation_count: int = 0) -> tuple[str, str, float, str]:
    return _infer_label(
        tags=tags,
        in_trash=in_trash,
        note_count=note_count,
        annotation_count=annotation_count,
    )


# ---------------------------------------------------------------------------
# Strong-positive emojis: single occurrence already crosses the must_read bin.
# ---------------------------------------------------------------------------


def test_brain_tag_alone_lands_must_read():
    """🧠 (+2.0) on baseline 3.0 → 5.0 → must_read."""
    priority, strength, rel, tier = _call(["d:🧠distille:"])
    assert priority == "must_read"
    assert strength == "high"
    assert rel == 5.0
    assert "strong_positive" in tier


def test_tried_tag_alone_lands_must_read():
    priority, _, rel, tier = _call(["d:✅trie:"])
    assert priority == "must_read"
    assert rel == 5.0
    assert "strong_positive" in tier


def test_key_insight_lands_must_read():
    """🗝️ has a variation-selector; we match on the base char 🗝."""
    priority, _, _, tier = _call(["y:🗝️ke:"])
    assert priority == "must_read"
    assert "strong_positive" in tier


# ---------------------------------------------------------------------------
# Single high/medium-positive lands in should_read.
# ---------------------------------------------------------------------------


def test_idea_tag_alone_lands_should_read():
    """💡 (+1.5) → 4.5 → just barely must_read at the boundary.

    The bin function uses strict less-than for the upper, so 4.5 → must_read.
    """
    priority, _, rel, tier = _call(["a:💡ide:"])
    assert priority == "must_read"
    assert rel == 4.5
    assert "high_positive" in tier


def test_eyes_tag_alone_lands_should_read():
    """👀 (+1.0) → 4.0 → should_read."""
    priority, strength, rel, tier = _call(["d:👀skimme:"])
    assert priority == "should_read"
    assert strength == "medium"   # single signal, |delta|<1.5
    assert rel == 4.0
    assert "medium_positive" in tier


def test_method_tag_alone_lands_should_read():
    priority, _, _, tier = _call(["d:🧪metho:"])
    assert priority == "should_read"
    assert "medium_positive" in tier


def test_statistical_method_tag_lands_should_read():
    priority, _, _, tier = _call(["s:🧮statistical_method:"])
    assert priority == "should_read"
    assert "medium_positive" in tier


# ---------------------------------------------------------------------------
# Critical-engagement emojis (❓/🧱/⚡): now should_read too.
# ---------------------------------------------------------------------------


def test_question_tag_alone_lands_should_read():
    """❓ (+1.0) → 4.0 → should_read (was could_read pre-1.14)."""
    priority, _, rel, tier = _call(["n:❓questio:"])
    assert priority == "should_read"
    assert rel == 4.0
    assert "critical_engagement" in tier


def test_limitation_tag_alone_lands_should_read():
    priority, _, _, tier = _call(["n:🧱limitatio:"])
    assert priority == "should_read"
    assert "critical_engagement" in tier


def test_challenge_tag_alone_lands_should_read():
    priority, _, _, tier = _call(["e:⚡️challeng:"])
    assert priority == "should_read"
    assert "critical_engagement" in tier


# ---------------------------------------------------------------------------
# Annotation/note increments — additive, capped, never reaches must alone.
# ---------------------------------------------------------------------------


def test_one_annotation_alone_stays_could_read():
    """1 annotation → +0.25 → 3.25 → could_read."""
    priority, _, rel, _ = _call([], annotation_count=1)
    assert priority == "could_read"
    assert rel == 3.25


def test_two_annotations_alone_still_could_read():
    """2 annotations → +0.50 → 3.50; ≥ 3.5 → should_read (boundary)."""
    priority, _, rel, _ = _call([], annotation_count=2)
    assert priority == "should_read"
    assert rel == 3.5


def test_five_annotations_alone_lands_should_read():
    """5 annotations → +1.25 → 4.25 → should_read.

    Strength is "medium" — |score-3.0|=1.25 < 1.5 high-threshold and
    no emoji signals fired, so confidence stays at the middle band.
    """
    priority, strength, rel, _ = _call([], annotation_count=5)
    assert priority == "should_read"
    assert strength == "medium"
    assert rel == 4.25


def test_ten_plus_annotations_caps_at_must_read():
    """10 annotations → capped at +2.0 → 5.0 → must_read."""
    priority, _, rel, _ = _call([], annotation_count=10)
    assert priority == "must_read"
    assert rel == 5.0


def test_one_note_alone_stays_could_read():
    """1 note → +0.5 → 3.5 → should_read at boundary."""
    priority, _, rel, _ = _call([], note_count=1)
    assert priority == "should_read"
    assert rel == 3.5


def test_one_annotation_one_note_combine():
    """0.25 + 0.5 = +0.75 → 3.75 → should_read."""
    priority, _, rel, _ = _call([], annotation_count=1, note_count=1)
    assert priority == "should_read"
    assert rel == 3.75


# ---------------------------------------------------------------------------
# Negatives.
# ---------------------------------------------------------------------------


def test_boring_alone_is_hard_veto():
    """🥱 is a hard veto (Phase 1.15, user-confirmed 2026-05-14): short-circuit
    to dont_read with high strength, relevance 1.0 — no additive scoring."""
    priority, strength, rel, tier = _call(["g:🥱borin:"])
    assert priority == "dont_read"
    assert strength == "high"
    assert rel == 1.0
    assert tier == "hard_veto"


def test_thumbs_down_alone_is_hard_veto():
    """👎 → hard veto → dont_read with strength=high, rel=1.0."""
    priority, strength, rel, tier = _call(["👎"])
    assert priority == "dont_read"
    assert strength == "high"
    assert rel == 1.0
    assert tier == "hard_veto"


def test_reject_x_is_hard_veto():
    """❌ → hard veto."""
    priority, _, _, tier = _call(["❌reject"])
    assert priority == "dont_read"
    assert tier == "hard_veto"


def test_trash_short_circuits_to_dont_read():
    """Trash overrides every positive signal, no scoring done."""
    priority, _, rel, _ = _call(
        ["d:🧠distille:", "👀skimmed"], in_trash=True
    )
    assert priority == "dont_read"
    assert rel == 1.0


# ---------------------------------------------------------------------------
# Multi-signal aggregation: deltas STACK (the whole point of Phase 1.14).
# ---------------------------------------------------------------------------


def test_thumbs_down_overrides_all_positive_signals():
    """Phase 1.15 (user-confirmed 2026-05-14): 👎 is a hard veto, just like
    🥱 and ❌. No amount of positive stacking can override it — that's
    the user's explicit verdict."""
    priority, _, rel, tier = _call(
        ["👎", "d:🧠distille:", "a:💡ide:"]
    )
    assert priority == "dont_read"
    assert rel == 1.0
    assert tier == "hard_veto"


def test_brain_plus_eyes_stack_to_must_read_strength_high():
    """🧠 (+2.0) + 👀 (+1.0) = +3.0 → 5.0 (clamped) → must_read."""
    priority, strength, rel, tier = _call(["d:🧠distille:", "d:👀skimme:"])
    assert priority == "must_read"
    assert strength == "high"
    assert rel == 5.0
    # Audit trail records both tiers
    assert "strong_positive" in tier
    assert "medium_positive" in tier


def test_eyes_plus_question_stack_to_must_read():
    """👀 (+1.0) + ❓ (+1.0) = +2.0 → 5.0 → must_read.

    Two medium-tier signals together imply a deeper read than either
    alone, so we lift to must_read.
    """
    priority, _, rel, _ = _call(["d:👀skimme:", "n:❓questio:"])
    assert priority == "must_read"
    assert rel == 5.0


def test_boring_with_one_annotation_still_hard_veto():
    """🥱 is a hard veto — annotations cannot rescue it (Phase 1.15)."""
    priority, _, rel, tier = _call(["🥱"], annotation_count=1)
    assert priority == "dont_read"
    assert rel == 1.0
    assert tier == "hard_veto"


def test_boring_with_many_annotations_still_hard_veto():
    """Even 10 annotations don't override an explicit 🥱 verdict."""
    priority, _, rel, tier = _call(["🥱"], annotation_count=10)
    assert priority == "dont_read"
    assert rel == 1.0
    assert tier == "hard_veto"


def test_bare_emoji_without_prefix_still_matches():
    """Old-school tags like '🧠distilled' (no prefix) must still match."""
    priority, _, _, tier = _call(["🧠distilled"])
    assert priority == "must_read"
    assert "strong_positive" in tier


# ---------------------------------------------------------------------------
# Phase 1.15 time-decay (180-day half-life).
# ---------------------------------------------------------------------------


def test_decay_at_day_0_full_weight():
    """Today's 🧠 still adds the full +2.0 → must_read."""
    priority, _, rel, _ = _call(["🧠"], annotation_count=0)
    assert priority == "must_read"
    assert rel == 5.0


def test_decay_at_180d_halves_engagement():
    """🧠 at exactly 180 days: weight 0.5 × 2.0 = 1.0 → score 4.0 → should_read."""
    priority, _, rel, _ = _call(["🧠"], annotation_count=0)
    # No way to pass days_since_added through the public _call(); test the
    # underlying _infer_label directly.
    from zotero_summarizer.services.goldenset import _infer_label
    p, _, r, _ = _infer_label(
        tags=["🧠"], in_trash=False, note_count=0, annotation_count=0,
        days_since_added=180,
    )
    assert p == "should_read"
    assert r == 4.0


def test_decay_at_360d_quarters_engagement():
    """🧠 at ~360 days: weight 0.25 × 2.0 = 0.5 → score 3.5 → should_read."""
    from zotero_summarizer.services.goldenset import _infer_label
    p, _, r, _ = _infer_label(
        tags=["🧠"], in_trash=False, note_count=0, annotation_count=0,
        days_since_added=360,
    )
    assert p == "should_read"
    assert r == 3.5


def test_decay_at_730d_pushes_to_could_read():
    """🧠 at ~2 years: weight ≈0.063 × 2.0 ≈ 0.125 → score ≈ 3.125 → could_read."""
    from zotero_summarizer.services.goldenset import _infer_label
    p, _, r, _ = _infer_label(
        tags=["🧠"], in_trash=False, note_count=0, annotation_count=0,
        days_since_added=730,
    )
    assert p == "could_read"
    assert 3.1 <= r <= 3.2


def test_decay_does_not_apply_to_neutral_baseline():
    """An item with no engagement signals at all stays at 3.0 → could_read,
    regardless of how old it is — decay multiplies the *delta*, not the baseline."""
    from zotero_summarizer.services.goldenset import _infer_label
    p, _, r, _ = _infer_label(
        tags=[], in_trash=False, note_count=0, annotation_count=0,
        days_since_added=10000,
    )
    assert p == "could_read"
    assert r == 3.0


def test_decay_does_not_apply_to_hard_veto():
    """🥱 short-circuits before decay is applied: still hard veto at year 5."""
    from zotero_summarizer.services.goldenset import _infer_label
    p, _, r, t = _infer_label(
        tags=["🥱"], in_trash=False, note_count=0, annotation_count=0,
        days_since_added=1825,
    )
    assert p == "dont_read"
    assert r == 1.0
    assert t == "hard_veto"
