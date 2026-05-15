"""Tests for the Phase 1.16 Step 0 baseline + learning-curve framework.

Strategy: avoid touching SPECTER2 / OpenAlex by monkey-patching the
``classifier`` helpers in the runner. The CV loop, bootstrap, and metric
math stand alone and can be tested with synthetic inputs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from zotero_summarizer.services.eval_baseline import (
    PRIORITY_BIN_EDGES,
    PRIORITY_NAMES,
    BaselineReport,
    LearningCurveReport,
    MetricCI,
    learning_curve_to_dict,
    priority_from_continuous,
    report_to_dict,
    run_baseline,
    run_learning_curve,
)
from zotero_summarizer.services.eval_baseline._bootstrap import bca_ci
from zotero_summarizer.services.eval_baseline._featurize import (
    FeaturizedGolden,
    load_golden_rows,
)
from zotero_summarizer.services.eval_baseline._metrics import (
    FoldMetrics,
    compute_fold_metrics,
)


# ---------------------------------------------------------------------------
# Bootstrap (BCa)
# ---------------------------------------------------------------------------


def test_bca_ci_returns_point_inside_interval():
    rng = np.random.default_rng(0)
    vals = rng.normal(loc=0.5, scale=0.1, size=25)
    point, lo, hi = bca_ci(vals, n_bootstrap=500, seed=42)
    assert lo <= point <= hi
    assert abs(point - float(np.mean(vals))) < 1e-12


def test_bca_ci_narrows_with_more_data():
    rng = np.random.default_rng(1)
    small = rng.normal(loc=0.5, scale=0.1, size=10)
    large = rng.normal(loc=0.5, scale=0.1, size=200)
    _, lo_s, hi_s = bca_ci(small, n_bootstrap=500, seed=42)
    _, lo_l, hi_l = bca_ci(large, n_bootstrap=500, seed=42)
    assert (hi_l - lo_l) < (hi_s - lo_s)


def test_bca_ci_empty_input_raises():
    with pytest.raises(ValueError, match="empty"):
        bca_ci(np.array([], dtype=np.float64), n_bootstrap=100, seed=42)


def test_bca_ci_deterministic_with_seed():
    vals = np.array([0.3, 0.5, 0.4, 0.6, 0.5, 0.45], dtype=np.float64)
    a1 = bca_ci(vals, n_bootstrap=200, seed=7)
    a2 = bca_ci(vals, n_bootstrap=200, seed=7)
    assert a1 == a2


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_priority_from_continuous_bins():
    assert priority_from_continuous(5.0) == "must_read"
    assert priority_from_continuous(4.5) == "must_read"
    assert priority_from_continuous(4.4999) == "should_read"
    assert priority_from_continuous(3.5) == "should_read"
    assert priority_from_continuous(3.49) == "could_read"
    assert priority_from_continuous(2.0) == "could_read"
    assert priority_from_continuous(1.99) == "dont_read"
    assert priority_from_continuous(1.0) == "dont_read"


def test_priority_bin_edges_match_priority_names_length():
    assert len(PRIORITY_BIN_EDGES) == len(PRIORITY_NAMES) - 1


def test_compute_fold_metrics_perfect_prediction():
    y_cont = np.array([1.5, 3.0, 4.0, 4.8, 2.5, 3.7], dtype=np.float64)
    y_bin = np.array([0, 0, 1, 1, 0, 1], dtype=np.int32)
    y_prio = ["dont_read", "could_read", "should_read", "must_read", "could_read", "should_read"]
    # Predicted probs perfectly ordered with the continuous label.
    proba = np.array([0.05, 0.30, 0.65, 0.95, 0.20, 0.55], dtype=np.float64)
    fm = compute_fold_metrics(y_cont, y_bin, y_prio, proba)
    assert fm.spearman_rho == pytest.approx(1.0, abs=1e-6)
    assert fm.auc == pytest.approx(1.0, abs=1e-6)
    assert 0.95 <= fm.ndcg_at_10 <= 1.0
    assert fm.cohen_kappa > 0.0
    assert fm.n_rows == 6
    assert fm.n_positive == 3


def test_compute_fold_metrics_random_prediction_near_zero_spearman():
    rng = np.random.default_rng(0)
    n = 50
    y_cont = rng.uniform(1.0, 5.0, size=n)
    y_bin = (y_cont >= 3.5).astype(np.int32)
    y_prio = [priority_from_continuous(float(s)) for s in y_cont]
    proba = rng.uniform(0, 1, size=n)
    fm = compute_fold_metrics(y_cont, y_bin, y_prio, proba)
    assert abs(fm.spearman_rho) < 0.5
    assert 0.3 <= fm.auc <= 0.7


def test_compute_fold_metrics_single_class_binary_raises():
    y_cont = np.array([3.0, 3.5, 4.0], dtype=np.float64)
    y_bin = np.array([1, 1, 1], dtype=np.int32)
    y_prio = ["could_read", "should_read", "should_read"]
    proba = np.array([0.5, 0.6, 0.7], dtype=np.float64)
    with pytest.raises(ValueError, match="single-class"):
        compute_fold_metrics(y_cont, y_bin, y_prio, proba)


def test_compute_fold_metrics_constant_continuous_raises():
    y_cont = np.array([3.0, 3.0, 3.0, 3.0], dtype=np.float64)
    y_bin = np.array([0, 0, 1, 1], dtype=np.int32)
    y_prio = ["could_read"] * 4
    proba = np.array([0.2, 0.4, 0.6, 0.8], dtype=np.float64)
    with pytest.raises(ValueError, match="constant"):
        compute_fold_metrics(y_cont, y_bin, y_prio, proba)


# ---------------------------------------------------------------------------
# run_baseline (with featurization mocked)
# ---------------------------------------------------------------------------


def _make_synthetic_feat(n: int = 200, seed: int = 0) -> FeaturizedGolden:
    """A FeaturizedGolden with random features but a meaningful signal:
    extra feature [0] is highly correlated with the continuous label."""
    rng = np.random.default_rng(seed)
    # 768-d SPECTER2 mock + 7 extras (mocked) = 775 features
    n_features = 775
    X = rng.normal(size=(n, n_features)).astype(np.float32)
    y_cont = rng.uniform(1.0, 5.0, size=n)
    # Inject signal: shove the continuous label into feature 0 (rescaled).
    X[:, 0] = (y_cont - 3.0).astype(np.float32) + rng.normal(scale=0.5, size=n).astype(np.float32)
    y_bin = (y_cont >= 3.5).astype(np.int32)
    y_prio = [priority_from_continuous(float(s)) for s in y_cont]
    return FeaturizedGolden(
        X=X,
        y_binary=y_bin,
        y_continuous=y_cont,
        y_priority=y_prio,
        item_keys=[f"k{i}" for i in range(n)],
        n_features=n_features,
    )


def test_run_baseline_returns_fold_count_and_cis():
    """End-to-end with featurization mocked. Use logreg (cheap, deterministic)."""
    feat = _make_synthetic_feat(n=200, seed=0)
    with patch(
        "zotero_summarizer.services.eval_baseline._runners.featurize_golden",
        return_value=feat,
    ):
        # Pass an arbitrary path & rows — the mock replaces it.
        report = run_baseline(
            rows=[{"item_key": "dummy"}],
            corpus_db_path=Path("/tmp/dummy.db"),
            goals_config=None,
            classifier_name="logreg",
            n_repeats=2,
            n_folds=3,
            n_bootstrap=200,
        )
    assert isinstance(report, BaselineReport)
    assert report.n_rows_total == 200
    assert len(report.folds) == 2 * 3
    # Each metric has a CI containing the point estimate.
    for ci in (
        report.spearman_rho,
        report.kendall_tau,
        report.auc,
        report.ndcg_at_10,
        report.mae,
        report.cohen_kappa,
    ):
        assert isinstance(ci, MetricCI)
        assert ci.ci_low <= ci.point <= ci.ci_high
        assert ci.n_bootstrap == 200


def test_run_baseline_detects_signal_in_synthetic_data():
    """With the seeded signal in feature 0, Spearman should be clearly positive."""
    feat = _make_synthetic_feat(n=300, seed=0)
    with patch(
        "zotero_summarizer.services.eval_baseline._runners.featurize_golden",
        return_value=feat,
    ):
        report = run_baseline(
            rows=[{"item_key": "dummy"}],
            corpus_db_path=Path("/tmp/dummy.db"),
            goals_config=None,
            classifier_name="logreg",
            n_repeats=2,
            n_folds=3,
            n_bootstrap=200,
        )
    # The signal-to-noise ratio (corr ≈ 0.6 → 0.85 depending on seed) should
    # produce Spearman well above zero on held-out.
    assert report.spearman_rho.point > 0.3, (
        f"expected Spearman > 0.3 with injected signal, got {report.spearman_rho.point:.3f}"
    )


# ---------------------------------------------------------------------------
# run_learning_curve
# ---------------------------------------------------------------------------


def test_run_learning_curve_emits_one_point_per_fraction():
    feat = _make_synthetic_feat(n=300, seed=0)
    with patch(
        "zotero_summarizer.services.eval_baseline._runners.featurize_golden",
        return_value=feat,
    ):
        report = run_learning_curve(
            rows=[{"item_key": "dummy"}],
            corpus_db_path=Path("/tmp/dummy.db"),
            goals_config=None,
            classifier_name="logreg",
            fractions=(0.3, 0.6, 1.0),
            n_repeats=2,
            n_folds=3,
            n_bootstrap=100,
        )
    assert isinstance(report, LearningCurveReport)
    assert len(report.points) == 3
    # n_train should increase with fraction.
    n_trains = [p.n_train for p in report.points]
    assert n_trains == sorted(n_trains)
    for p in report.points:
        assert p.spearman.ci_low <= p.spearman.point <= p.spearman.ci_high


def test_run_learning_curve_rejects_invalid_fractions():
    feat = _make_synthetic_feat(n=200, seed=0)
    with patch(
        "zotero_summarizer.services.eval_baseline._runners.featurize_golden",
        return_value=feat,
    ):
        with pytest.raises(ValueError, match="must be in"):
            run_learning_curve(
                rows=[{"item_key": "dummy"}],
                corpus_db_path=Path("/tmp/dummy.db"),
                goals_config=None,
                classifier_name="logreg",
                fractions=(1.5,),
            )
        with pytest.raises(ValueError, match="ascending"):
            run_learning_curve(
                rows=[{"item_key": "dummy"}],
                corpus_db_path=Path("/tmp/dummy.db"),
                goals_config=None,
                classifier_name="logreg",
                fractions=(0.6, 0.3),
            )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_report_to_dict_round_trip_via_json():
    import json

    feat = _make_synthetic_feat(n=200, seed=0)
    with patch(
        "zotero_summarizer.services.eval_baseline._runners.featurize_golden",
        return_value=feat,
    ):
        report = run_baseline(
            rows=[{"item_key": "dummy"}],
            corpus_db_path=Path("/tmp/dummy.db"),
            goals_config=None,
            classifier_name="logreg",
            n_repeats=1,
            n_folds=3,
            n_bootstrap=100,
        )
    d = report_to_dict(report)
    # JSON-serializable (no numpy ints/floats).
    text = json.dumps(d)
    parsed = json.loads(text)
    assert parsed["type"] == "baseline_report"
    assert parsed["n_rows_total"] == 200
    assert "spearman_rho" in parsed["metrics"]
    assert parsed["metrics"]["spearman_rho"]["ci_low"] <= parsed["metrics"]["spearman_rho"]["point"]


def test_learning_curve_to_dict_round_trip_via_json():
    import json

    feat = _make_synthetic_feat(n=200, seed=0)
    with patch(
        "zotero_summarizer.services.eval_baseline._runners.featurize_golden",
        return_value=feat,
    ):
        report = run_learning_curve(
            rows=[{"item_key": "dummy"}],
            corpus_db_path=Path("/tmp/dummy.db"),
            goals_config=None,
            classifier_name="logreg",
            fractions=(0.5, 1.0),
            n_repeats=1,
            n_folds=3,
            n_bootstrap=100,
        )
    d = learning_curve_to_dict(report)
    parsed = json.loads(json.dumps(d))
    assert parsed["type"] == "learning_curve_report"
    assert len(parsed["points"]) == 2


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def test_load_golden_rows_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_golden_rows(tmp_path / "does_not_exist.csv")


def test_load_golden_rows_reads_csv(tmp_path):
    csv_path = tmp_path / "golden.csv"
    csv_path.write_text(
        "item_key,title,gold_priority_final,gold_inferred_relevance\n"
        "K1,Paper A,must_read,5.0\n"
        "K2,Paper B,dont_read,1.0\n",
        encoding="utf-8",
    )
    rows = load_golden_rows(csv_path)
    assert len(rows) == 2
    assert rows[0]["item_key"] == "K1"
    assert rows[1]["gold_inferred_relevance"] == "1.0"
