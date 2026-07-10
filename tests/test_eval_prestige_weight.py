"""Unit tests for the PURE ``ablate_prestige_weight`` sweep in
tools/eval_prestige_weight.py.

These need no DB and no corpus model: ``main()``/``_load_rows`` defer every heavy
import, and ``ablate_prestige_weight`` only imports the pure ``rank_blend`` blend.
The synthetic cohort is constructed so prestige is the ONLY signal that separates
user-kept from user-trashed rows — base and goal_sim are deliberately
anti-correlated with the label — so raising the prestige weight must monotonically
improve AUC/NDCG, and a strictly-positive weight must be NDCG-best.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_prestige_weight",
    Path(__file__).resolve().parents[1] / "tools" / "eval_prestige_weight.py",
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


def _prestige_decides_cohort() -> list[dict]:
    """A cohort where ONLY prestige separates kept from trashed.

    ``base`` is identical for every row (a degenerate, uninformative relevance
    axis → 0.5 for all; both signal weights normalize raw spread away, so a base
    *anti*-signal whose weight is always ``1 - w ≥ 0.7`` would be unbeatable
    inside the realistic ``w ≤ 0.3`` sweep — hence base must be neutral, not
    adversarial). ``goal_sim`` is absent. Kept rows carry HIGH prestige, trashed
    LOW. Kept rows sit at the HIGH indices so the stable tie-order at ``w == 0``
    buries them below rank 10 — making P@10/NDCG@10 (not just AUC) discriminate:
    at ``w == 0`` they are floored; any ``w > 0`` lets prestige lift kept to the
    top, perfectly separating the classes.
    """
    n = 40
    rows: list[dict] = []
    for i in range(n):
        kept = i >= n // 2
        rows.append({
            "base": 5.0,                       # degenerate → uninformative (0.5 all)
            "goal_sim": None,                  # no goal axis
            "prestige": 2.0 if kept else 1.0,  # the ONLY separating signal
            "label": 1 if kept else 0,
        })
    return rows


def _by_weight(results: list[dict]) -> dict[float, dict]:
    return {r["weight"]: r for r in results}


def test_returns_one_record_per_weight_with_full_schema() -> None:
    rows = _prestige_decides_cohort()
    weights = [0.0, 0.15, 0.30]
    out = ev.ablate_prestige_weight(rows, weights)
    assert [r["weight"] for r in out] == weights
    for r in out:
        assert set(r) == {"weight", "auc", "p_at_10", "ndcg_at_10", "n"}
        assert r["n"] == len(rows)
        assert 0.0 <= r["auc"] <= 1.0
        assert 0.0 <= r["ndcg_at_10"] <= 1.0


def test_extremes_prestige_off_floors_metrics_on_present_lifts_them() -> None:
    # w=0 (prestige OFF) → base is uninformative and the tie-order buries kept
    # rows below rank 10 → AUC at chance, P@10/NDCG@10 floored. Any w>0 → prestige
    # perfectly separates → every metric saturates. This is the monotone-at-extremes
    # behavior the knob is supposed to exhibit when prestige is the true signal.
    rows = _prestige_decides_cohort()
    weights = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]
    res = _by_weight(ev.ablate_prestige_weight(rows, weights))

    # Extreme low: prestige off → no separation.
    assert res[0.0]["auc"] == pytest.approx(0.5)
    assert res[0.0]["p_at_10"] == 0.0
    assert res[0.0]["ndcg_at_10"] == 0.0
    # Any present weight: prestige separates perfectly.
    for w in weights[1:]:
        assert res[w]["auc"] == pytest.approx(1.0)
        assert res[w]["p_at_10"] == pytest.approx(1.0)
        assert res[w]["ndcg_at_10"] == pytest.approx(1.0)
    # Monotone non-decreasing across the sweep (off ≤ on, never a regression).
    for m in ("auc", "p_at_10", "ndcg_at_10"):
        vals = [res[w][m] for w in weights]
        assert vals == sorted(vals)


def test_a_positive_prestige_weight_is_ndcg_best_when_prestige_is_the_signal() -> None:
    # The NDCG-best weight is a STRICTLY POSITIVE one (prestige is the only
    # separator) and it strictly beats the prestige-off baseline — the lever
    # genuinely helps. (Once prestige separates perfectly, larger weights tie at
    # the ceiling, so the smallest sufficient weight is reported best — correct:
    # use the least weight that achieves the lift, don't over-weight.)
    rows = _prestige_decides_cohort()
    results = ev.ablate_prestige_weight(rows, list(ev.PRESTIGE_WEIGHTS))
    best = max(results, key=lambda r: r["ndcg_at_10"])
    res = _by_weight(results)
    assert best["weight"] > 0.0
    assert best["ndcg_at_10"] > res[0.0]["ndcg_at_10"]


def test_prestige_anti_signal_makes_weight_zero_strictly_optimal() -> None:
    # Mirror cohort: base uninformative, no goal axis, and prestige is an
    # ANTI-signal — kept rows carry LOWER prestige than trashed. Kept rows sit at
    # LOW indices so the stable tie-order at w=0 ranks them on top (NDCG@10=1.0);
    # any w>0 lets the anti-signal actively bury them (NDCG@10→0). So w=0 is
    # STRICTLY NDCG-best — proving the sweep isn't hard-wired to prefer positive
    # weights; when prestige hurts, the data drives the optimum to 0.
    n = 40
    rows = [
        {
            "base": 5.0,                                 # uninformative
            "goal_sim": None,                            # no goal axis
            "prestige": (1.0 if i < n // 2 else 2.0),    # anti-signal: kept LOWER
            "label": 1 if i < n // 2 else 0,
        }
        for i in range(n)
    ]
    results = ev.ablate_prestige_weight(rows, list(ev.PRESTIGE_WEIGHTS))
    res = _by_weight(results)
    best = max(results, key=lambda r: r["ndcg_at_10"])
    assert best["weight"] == 0.0
    # strictly best, not a tie-order fluke: every positive weight is worse.
    for w in ev.PRESTIGE_WEIGHTS:
        if w > 0.0:
            assert res[0.0]["ndcg_at_10"] > res[w]["ndcg_at_10"]


def test_citation_percentile_extractor_contract() -> None:
    # Present → float; absent aux/percentile/empty/None → None (absent-signal
    # contract, never a relevance fallback).
    import json
    payload = json.dumps({"aux_context": {"citation_percentile": 0.87}})
    assert ev._citation_percentile(payload) == pytest.approx(0.87)
    assert ev._citation_percentile(json.dumps({"aux_context": {}})) is None
    assert ev._citation_percentile(json.dumps({})) is None
    assert ev._citation_percentile("") is None
    assert ev._citation_percentile(None) is None


def test_empty_rows_and_bad_labels_raise() -> None:
    with pytest.raises(ValueError):
        ev.ablate_prestige_weight([], [0.15])
    bad = [{"base": 1.0, "goal_sim": 0.5, "prestige": 0.5, "label": 2}]
    with pytest.raises(ValueError):
        ev.ablate_prestige_weight(bad, [0.15])
