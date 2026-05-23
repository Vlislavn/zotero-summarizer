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
