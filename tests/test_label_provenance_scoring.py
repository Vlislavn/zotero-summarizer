"""Phase 1.18 Step 1 — tests for the label-provenance SCORING math.

Verifies that ``services.label_provenance.compute_provenance`` reproduces
``services.goldenset._infer_label`` and exposes the breakdown correctly.

Covers: hard short-circuits (trash, hard-veto), additive scoring, decay,
caps, clamping, derived flags, mirror equivalence with the source-of-truth
function. CSV-row + I/O tests live in ``test_label_provenance_io.py``.
"""

from __future__ import annotations

import pytest

from zotero_summarizer.services import emoji_signals, label_provenance as lp


# ---------------------------------------------------------------------------
# Hard short-circuits
# ---------------------------------------------------------------------------


def test_in_trash_short_circuit_to_dont_read():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠"],
        in_trash=True,
        annotation_count=5, user_note_count=2, days_since_added=10,
    )
    assert p.derived_priority == "dont_read"
    assert p.derived_score == 1.0
    assert p.in_trash_override is True
    assert p.hard_veto_emojis == []
    assert p.emoji_contributions == []


def test_hard_veto_short_circuit_to_dont_read():
    for veto in ("🥱", "👎", "❌"):
        p = lp.compute_provenance(
            item_key="K1", title="T",
            tags=["🧠", veto],
            in_trash=False,
            annotation_count=3, user_note_count=1, days_since_added=20,
        )
        assert p.derived_priority == "dont_read", f"emoji {veto} failed"
        assert veto in p.hard_veto_emojis
        assert p.in_trash_override is False


# ---------------------------------------------------------------------------
# Additive scoring (baseline, emojis, decay, caps, clamps)
# ---------------------------------------------------------------------------


def test_neutral_no_signals_gives_could_read_at_baseline():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=[], in_trash=False,
        annotation_count=0, user_note_count=0, days_since_added=0,
    )
    assert p.derived_priority == "could_read"
    assert p.derived_score == 3.0
    assert p.engagement_sum_raw == 0.0


def test_brain_emoji_single_signal_lifts_to_must_read_at_day_zero():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠"], in_trash=False,
        annotation_count=0, user_note_count=0, days_since_added=0,
    )
    assert p.derived_priority == "must_read"
    assert p.derived_score == 5.0
    assert len(p.emoji_contributions) == 1
    assert p.emoji_contributions[0].emoji == "🧠"
    assert p.emoji_contributions[0].raw_delta == 2.0
    assert p.emoji_contributions[0].decayed_delta == 2.0


def test_decay_at_180_days_halves_engagement_contribution():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠"], in_trash=False,
        annotation_count=0, user_note_count=0, days_since_added=180,
    )
    assert p.derived_score == 4.0
    assert p.derived_priority == "should_read"
    assert p.decay_factor == pytest.approx(0.5, abs=1e-3)


def test_annotation_score_capped():
    p_eight = lp.compute_provenance(
        item_key="K1", title="T",
        tags=[], in_trash=False,
        annotation_count=8, user_note_count=0, days_since_added=0,
    )
    p_twenty = lp.compute_provenance(
        item_key="K2", title="T",
        tags=[], in_trash=False,
        annotation_count=20, user_note_count=0, days_since_added=0,
    )
    assert p_eight.annotation_score_capped == 2.0
    assert p_twenty.annotation_score_capped == 2.0
    assert p_twenty.annotation_score_raw == 5.0
    assert p_eight.derived_score == p_twenty.derived_score


def test_note_score_capped():
    p3 = lp.compute_provenance(
        item_key="K1", title="T",
        tags=[], in_trash=False,
        annotation_count=0, user_note_count=3, days_since_added=0,
    )
    p10 = lp.compute_provenance(
        item_key="K2", title="T",
        tags=[], in_trash=False,
        annotation_count=0, user_note_count=10, days_since_added=0,
    )
    assert p3.user_note_score_capped == 1.5
    assert p10.user_note_score_capped == 1.5
    assert p3.derived_score == p10.derived_score


def test_score_clamped_at_5_when_engagement_overshoots():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠", "✅", "🗝"],
        in_trash=False,
        annotation_count=20, user_note_count=20, days_since_added=0,
    )
    assert p.derived_score == 5.0
    assert p.derived_priority == "must_read"


def test_score_clamped_at_1_via_trash_path():
    """The lower clamp is reachable only via trash (hard vetoes also force 1.0)."""
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=[], in_trash=True,
        annotation_count=0, user_note_count=0, days_since_added=0,
    )
    assert p.derived_score == 1.0
    assert p.derived_priority == "dont_read"


# ---------------------------------------------------------------------------
# Derived flags
# ---------------------------------------------------------------------------


def test_weak_must_read_flag_for_single_signal():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠"], in_trash=False,
        annotation_count=0, user_note_count=0, days_since_added=0,
    )
    assert p.derived_priority == "must_read"
    assert "weak_must_read" in p.flags


def test_strong_must_read_not_flagged_as_weak():
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠", "✅"], in_trash=False,
        annotation_count=5, user_note_count=2, days_since_added=0,
    )
    assert p.derived_priority == "must_read"
    assert "weak_must_read" not in p.flags


def test_near_must_read_flag_at_should_read_boundary():
    """should_read within 0.2 of the must_read threshold → near_must_read."""
    # 🧠 (+2.0) decayed ~0.70 = +1.40 → score 4.40 should_read in (4.3, 4.5)
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠"], in_trash=False,
        annotation_count=0, user_note_count=0, days_since_added=93,
    )
    assert 4.3 <= p.derived_score < 4.5
    assert p.derived_priority == "should_read"
    assert "near_must_read" in p.flags


def test_heavily_decayed_must_read_flag():
    """must_read with decay < 0.3 → flagged. Need strong raw to clear 4.5 even after decay."""
    p = lp.compute_provenance(
        item_key="K1", title="T",
        tags=["🧠", "✅", "🗝"], in_trash=False,
        annotation_count=20, user_note_count=20, days_since_added=320,
    )
    assert p.derived_score >= 4.5
    assert p.derived_priority == "must_read"
    assert p.decay_factor < 0.3
    assert "heavily_decayed" in p.flags


# ---------------------------------------------------------------------------
# Mirror equivalence with _infer_label (THE CORE SAFETY TEST)
# ---------------------------------------------------------------------------


def test_compute_provenance_matches_infer_label_for_variety_of_inputs():
    """For every input combo we test, the derived priority MUST equal
    what ``goldenset._infer_label`` produces. If the source-of-truth code
    drifts, our mirror must follow.
    """
    from zotero_summarizer.services import goldenset

    test_cases = [
        ([], False, 0, 0, 0),
        (["🧠"], False, 0, 0, 0),
        (["🧠"], False, 0, 0, 180),
        (["🧠", "✅"], False, 3, 1, 50),
        (["👀"], False, 2, 0, 30),
        (["🧪"], False, 8, 3, 0),
        ([], True, 0, 0, 0),
        (["🥱"], False, 0, 0, 0),
        (["👎"], False, 5, 5, 0),
        (["💡", "❓"], False, 1, 0, 90),
        (["🔮"], False, 10, 10, 0),
    ]
    for tags, trash, ann, notes, days in test_cases:
        prov = lp.compute_provenance(
            item_key="K", title="T",
            tags=tags, in_trash=trash,
            annotation_count=ann, user_note_count=notes,
            days_since_added=days,
        )
        expected_prio, _, _, _ = goldenset._infer_label(
            tags=tags, in_trash=trash,
            note_count=notes, annotation_count=ann,
            days_since_added=days,
        )
        assert prov.derived_priority == expected_prio, (
            f"mismatch for tags={tags!r} trash={trash} ann={ann} notes={notes} "
            f"days={days}: provenance says {prov.derived_priority}, "
            f"_infer_label says {expected_prio}"
        )
