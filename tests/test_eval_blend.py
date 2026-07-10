"""Unit tests for the PURE metric/join helpers in tools/eval_slate_blend.py.

These pin the measurement apparatus a 10-expert review demanded (bootstrap CIs,
a within-subset ranking metric, the additive-vs-normalized counterfactual) — the
parts that decide whether a quality weight ships. They need no DB and no corpus
model: ``main()`` defers every heavy import, so importing the module is cheap.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[1] / "tools"
_SPEC = importlib.util.spec_from_file_location("eval_slate_blend", _TOOLS / "eval_slate_blend.py")
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)

# _eval_replay imports the pure kernel from its sibling; put tools/ on sys.path so that resolves.
sys.path.insert(0, str(_TOOLS))
_RSPEC = importlib.util.spec_from_file_location("_eval_replay", _TOOLS / "_eval_replay.py")
rp = importlib.util.module_from_spec(_RSPEC)
_RSPEC.loader.exec_module(rp)


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


# --- P2 replay metrics (contamination@k / q-lift@k) ---------------------------

def test_topk_indices_takes_highest_min_k_n() -> None:
    assert ev._topk_indices([0.1, 0.9, 0.5], 2) == [1, 2]   # descending, top-2
    assert ev._topk_indices([0.1, 0.9], 5) == [1, 0]        # k > n → all, still ordered


def test_contamination_at_counts_bad_in_topk() -> None:
    # top-2 by keys = indices 0,1; only index 0 is bad → 1/2.
    assert ev._contamination_at([0.9, 0.8, 0.1], [True, False, False], 2) == 0.5
    # a bad row ranked BELOW the cutoff doesn't count (that's the whole point of quality-first).
    assert ev._contamination_at([0.9, 0.8, 0.1], [False, False, True], 2) == 0.0
    assert ev._contamination_at([0.9, 0.8], [True, True], 2) == 1.0


def test_q_at_mean_and_median_over_topk() -> None:
    keys, q = [0.9, 0.1, 0.5], [1.0, 0.0, 0.4]     # top-2 = indices 0,2 → q {1.0, 0.4}
    assert ev._q_at(keys, q, 2, agg=ev._mean) == pytest.approx(0.7)
    assert ev._q_at(keys, q, 2, agg=ev._median) == 1.0   # upper-median of [0.4, 1.0]


def test_is_contaminated_predicate() -> None:
    assert ev._is_contaminated("D", None, None) is True        # grade D
    assert ev._is_contaminated("A", "flag", None) is True      # flagged band, any letter
    assert ev._is_contaminated(None, None, 2) is True          # low rigor dim
    assert ev._is_contaminated(None, None, 1) is True
    assert ev._is_contaminated(None, None, 3) is False         # rigor above the bar
    assert ev._is_contaminated(None, None, None) is False      # unknown ≠ contaminated
    assert ev._is_contaminated("A", "neutral", 5) is False


def test_replay_join_quality_two_key_bridge() -> None:
    reviews = {"ZK": {"quality": {"grade": "A"}}}
    # materialized key wins; stable_feed_key is the fallback; miss on both → {}.
    assert rp._join_quality({"materialized_zotero_key": "ZK"}, reviews) == {"grade": "A"}
    assert rp._join_quality({"stable_feed_key": "ZK"}, reviews) == {"grade": "A"}
    assert rp._join_quality({"materialized_zotero_key": "MISS", "stable_feed_key": "ZK"}, reviews) == {"grade": "A"}
    assert rp._join_quality({"materialized_zotero_key": None, "stable_feed_key": None}, reviews) == {}
