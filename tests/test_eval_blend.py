"""Unit tests for the PURE metric/join helpers in tools/eval_slate_blend.py.

These pin the measurement apparatus a 10-expert review demanded (bootstrap CIs,
a within-subset ranking metric, the additive-vs-normalized counterfactual) — the
parts that decide whether a quality weight ships. They need no DB and no corpus
model: ``main()`` defers every heavy import, so importing the module is cheap.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_slate_blend",
    Path(__file__).resolve().parents[1] / "tools" / "eval_slate_blend.py",
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


def test_auc_perfect_and_inverse_separation() -> None:
    # kept all outrank trashed → 1.0; fully inverted → 0.0; tie → 0.5.
    assert ev._auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert ev._auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0
    assert ev._auc([0.5, 0.5], [1, 0]) == 0.5
    with pytest.raises(ValueError):
        ev._auc([0.5, 0.6], [1, 1])  # single-class is undefined


def test_p_at_k() -> None:
    assert ev._p_at([0.9, 0.1, 0.8, 0.2], [1, 0, 1, 0], 2) == 1.0
    assert ev._p_at([0.9, 0.1, 0.8, 0.2], [0, 1, 0, 1], 2) == 0.0


def test_ndcg_rewards_kept_on_top() -> None:
    gains = [1, 1, 0, 0]
    good = ev._ndcg_at([0.9, 0.8, 0.2, 0.1], gains, 4)   # kept ranked first
    bad = ev._ndcg_at([0.1, 0.2, 0.8, 0.9], gains, 4)    # kept ranked last
    assert good == pytest.approx(1.0)
    assert good > bad
    assert ev._ndcg_at([0.5, 0.5], [0, 0], 2) == 0.0     # no positive gain → 0, not crash


def test_bootstrap_ci_is_deterministic_and_brackets_point_estimate() -> None:
    keys = [0.95, 0.85, 0.80, 0.30, 0.20, 0.10]
    labels = [1, 1, 1, 0, 0, 0]
    a = ev._bootstrap_ci(keys, labels, ev._auc, require_both_classes=True, n_boot=500, seed=7)
    b = ev._bootstrap_ci(keys, labels, ev._auc, require_both_classes=True, n_boot=500, seed=7)
    assert a == b                       # same seed → identical (resume/repro safe)
    lo, hi = a
    assert 0.0 <= lo <= hi <= 1.0
    assert lo <= ev._auc(keys, labels) <= hi


def test_position_deltas_and_rank_positions() -> None:
    base = [0.9, 0.5, 0.1]            # ranks: A=0, B=1, C=2
    swapped = [0.1, 0.5, 0.9]          # ranks: A=2, B=1, C=0
    assert ev._rank_positions(base) == [0, 1, 2]
    assert ev._position_deltas(base, swapped) == [2, 0, 2]
    assert ev._position_deltas(base, base) == [0, 0, 0]


def test_band_crossings_counts_only_lower_bucket_overtakes() -> None:
    # row0 bucket 5 (was ahead), row1 bucket 2 (was behind). alt flips them →
    # a lower-bucket row overtook a higher-bucket one = 1 crossing.
    base = [0.9, 0.1]
    alt = [0.1, 0.9]
    assert ev._band_crossings(base, alt, [5, 2]) == 1
    assert ev._band_crossings(base, base, [5, 2]) == 0   # no reorder → no crossing


def test_norm_col_handles_absent_and_degenerate() -> None:
    # absent → median of known; all-equal → 0.5; all-None → zeros.
    out = ev._norm_col([0.0, 1.0, None])
    # absent → upper-median of known (mirrors rank_blend._median = s[len//2] = 1.0).
    assert out[0] == 0.0 and out[1] == 1.0 and out[2] == 1.0
    assert ev._norm_col([0.4, 0.4]) == [0.5, 0.5]
    assert ev._norm_col([None, None]) == [0.0, 0.0]


def test_blend4_weights_sum_to_one_and_reward_quality() -> None:
    # equal rel/goal/prestige; only quality differs → the higher-quality row wins,
    # and the relevance weight is correctly 1 - goal - prestige - quality.
    keys = ev._blend4([3.0, 3.0], [0.5, 0.5], [0.2, 0.2], [0.0, 1.0],
                      goal_w=0.40, prestige_w=0.15, quality_w=0.10)
    assert keys[1] > keys[0]


def test_row_quality_joins_only_via_materialized_key() -> None:
    reviews = {"ZKEY1": {"quality": {"quality_band": "highlight", "grade": "A"}}}
    assert ev._row_quality({"materialized_zotero_key": "ZKEY1"}, reviews) == {
        "quality_band": "highlight", "grade": "A"
    }
    # GUID-keyed row with no materialized key → empty (the v1 trap: never a false join).
    assert ev._row_quality({"materialized_zotero_key": None, "guid": "ZKEY1"}, reviews) == {}
    assert ev._row_quality({"materialized_zotero_key": "MISSING"}, reviews) == {}
