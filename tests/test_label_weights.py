"""Per-row training weights, incl. the soft 'Add to library' interest tier."""
from __future__ import annotations

import pytest

from zotero_summarizer.services.model import label_weights as lw


def test_feed_interest_is_low_weight():
    assert lw.WEIGHT_INTEREST == 0.3
    assert lw._tier_weight("feed_interest", 0, 0) == lw.WEIGHT_INTEREST


def test_feed_user_label_unchanged():
    # A deliberate Feed-Review relabel keeps the higher review weight.
    assert lw._tier_weight("feed_user_label", 0, 0) == lw.WEIGHT_REVIEW == 0.5


def test_explicit_user_label_is_full_weight():
    # An explicit label:<priority> verdict is the user's deliberate ground truth:
    # it must weigh at the top tier, never the 0.7 medium fall-through it used to
    # get (which under-counted the cleanest signal we have).
    assert lw._tier_weight("user_label", 0, 0) == lw.WEIGHT_HIGH == 1.0
    assert lw._tier_weight("user_label", 0, 0) > lw.WEIGHT_MED


def test_interest_is_dominated_by_real_engagement():
    # The whole point: a stronger real signal outweighs the soft interest one.
    assert lw._tier_weight("strong_positive", 0, 0) == lw.WEIGHT_HIGH
    assert lw._tier_weight("feed_interest", 0, 0) < lw._tier_weight("feed_user_label", 0, 0)
    assert lw._tier_weight("feed_interest", 0, 0) < lw._tier_weight("strong_positive", 3, 0)


def test_compute_row_weights_mixed():
    rows = [
        {"gold_signal_tier": "feed_interest"},
        {"gold_signal_tier": "feed_user_label"},
        {"gold_signal_tier": "strong_positive", "annotation_count": "3"},
        {"gold_signal_tier": "hard_veto"},
    ]
    weights = lw.compute_row_weights(rows)
    assert list(weights) == pytest.approx([0.3, 0.5, 1.0, 1.0])


# ---------------------------------------------------------------------------
# Compound tiers + outcome segments (June 2026).
# ---------------------------------------------------------------------------


def test_outcome_segment_upgrades_to_review_weight():
    # A resolved 7-day observation is as informative as a deliberate Review
    # click — regardless of which outcome resolved.
    assert lw._tier_weight("feed_interest|outcome_kept_inbox", 0, 0) == lw.WEIGHT_REVIEW
    assert lw._tier_weight("feed_interest|outcome_trashed", 0, 0) == lw.WEIGHT_REVIEW
    assert lw._tier_weight("feed_interest|outcome_some_future_name", 0, 0) == lw.WEIGHT_REVIEW


def test_unknown_suffix_inherits_base_tier_weight():
    # A suffixed tier must inherit its base weight, never fall through to the
    # 0.7 legacy default (which would UP-weight a soft signal).
    assert lw._tier_weight("feed_interest|whatever_new", 0, 0) == lw.WEIGHT_INTEREST
    assert lw._tier_weight("first_glance|whatever_new", 0, 0) == lw.WEIGHT_GLANCE


def test_existing_compound_engagement_tiers_unchanged():
    assert lw._tier_weight("medium_positive|notes=1", 0, 1) == lw.WEIGHT_MED
    assert lw._tier_weight("critical_engagement|medium_positive", 0, 0) == lw.WEIGHT_HIGH
