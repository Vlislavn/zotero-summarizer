"""Phase 2.0: SPECTER2 + logistic-regression classifier (pivot from LLM scoring)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from zotero_summarizer.services import classifier


# ---------------------------------------------------------------------------
# Embedding cache
# ---------------------------------------------------------------------------


def _fake_embedding(title: str, abstract: str, *, authors: str = "", venue: str = "") -> np.ndarray:
    """Deterministic stand-in for SPECTER2 — content-dependent so cache works.

    Includes authors+venue so the test mirrors the production signature.
    """
    text = "|".join([title, abstract, authors, venue])
    rng = np.random.default_rng(hash(text) % (2**31))
    return rng.standard_normal(classifier.EMBEDDING_DIM).astype(np.float32)


def test_cache_hit_returns_identical_array(tmp_path: Path):
    db = tmp_path / "cache.db"
    with patch.object(classifier, "compute_embedding", side_effect=_fake_embedding) as mock:
        first = classifier.get_or_compute_embedding(db, "KEY1", "Title A", "Abstract A")
        second = classifier.get_or_compute_embedding(db, "KEY1", "Title A", "Abstract A")
    assert mock.call_count == 1, "second call should hit the SQLite cache"
    np.testing.assert_array_equal(first, second)


def test_cache_invalidated_on_content_change(tmp_path: Path):
    db = tmp_path / "cache.db"
    with patch.object(classifier, "compute_embedding", side_effect=_fake_embedding) as mock:
        classifier.get_or_compute_embedding(db, "KEY1", "Title A", "Abstract A")
        classifier.get_or_compute_embedding(db, "KEY1", "Title A", "Abstract A NEW")
    assert mock.call_count == 2, "abstract change should trigger recompute"


def test_specter2_table_created_idempotently(tmp_path: Path):
    db = tmp_path / "cache.db"
    classifier._ensure_schema(db)
    classifier._ensure_schema(db)  # second call must not error
    conn = sqlite3.connect(str(db))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        conn.close()
    assert "specter2_embeddings" in names


# ---------------------------------------------------------------------------
# Cross-validation
# ---------------------------------------------------------------------------


def _golden_row(key: str, title: str, abstract: str, priority: str) -> dict[str, str]:
    return {
        "item_key": key,
        "title": title,
        "abstract": abstract,
        "gold_priority_final": priority,
    }


def test_cv_produces_one_probability_per_eligible_row(tmp_path: Path):
    db = tmp_path / "cache.db"
    # 20 rows, ~50/50 split — enough for 5-fold stratified
    rows = []
    for i in range(10):
        rows.append(_golden_row(f"P{i}", f"Positive paper {i}", "abstract " * 30, "must_read"))
    for i in range(10):
        rows.append(_golden_row(f"N{i}", f"Negative paper {i}", "abstract " * 30, "dont_read"))

    with patch.object(classifier, "compute_embedding", side_effect=_fake_embedding):
        report = classifier.cross_validate(
            rows, corpus_db_path=db, n_folds=5, holdout_fraction=0.0,
        )

    assert report.n_rows == 20
    assert report.n_positive == 10
    assert len(report.cv_probabilities) == 20
    assert len(report.cv_predictions) == 20
    assert len(report.item_keys) == 20
    assert 0.0 <= report.auc <= 1.0
    assert all(0.0 <= p <= 1.0 for p in report.cv_probabilities)
    # Threshold-tuning fields populated.
    assert 0.0 <= report.optimal_threshold <= 1.0
    assert report.must_threshold >= report.optimal_threshold
    assert report.could_threshold <= report.optimal_threshold
    # Held-out disabled in this fixture.
    assert report.holdout_n_rows == 0


def test_cv_with_holdout_returns_both_splits(tmp_path: Path):
    """holdout_fraction>0 should populate held-out fields parallel to CV."""
    db = tmp_path / "cache.db"
    rows = []
    for i in range(20):
        rows.append(_golden_row(f"P{i}", f"Pos {i}", "abstract " * 30, "must_read"))
    for i in range(20):
        rows.append(_golden_row(f"N{i}", f"Neg {i}", "abstract " * 30, "dont_read"))

    with patch.object(classifier, "compute_embedding", side_effect=_fake_embedding):
        report = classifier.cross_validate(
            rows, corpus_db_path=db, n_folds=4, holdout_fraction=0.25,
        )

    # 40 rows total → 30 CV + 10 holdout (stratified).
    assert report.n_rows == 30
    assert report.holdout_n_rows == 10
    assert len(report.holdout_predictions) == 10
    assert len(report.holdout_item_keys) == 10
    # CV and held-out keys are disjoint.
    assert set(report.item_keys).isdisjoint(set(report.holdout_item_keys))


def test_cv_skips_rows_with_missing_fields(tmp_path: Path):
    db = tmp_path / "cache.db"
    rows = [
        _golden_row(f"P{i}", f"Positive {i}", "abstract", "must_read")
        for i in range(8)
    ] + [
        _golden_row(f"N{i}", f"Negative {i}", "abstract", "dont_read")
        for i in range(8)
    ] + [
        {"item_key": "X", "title": "", "abstract": "abs", "gold_priority_final": "must_read"},
        {"item_key": "Y", "title": "Title", "abstract": "", "gold_priority_final": "must_read"},
        {"item_key": "Z", "title": "Title", "abstract": "abs", "gold_priority_final": ""},
    ]
    with patch.object(classifier, "compute_embedding", side_effect=_fake_embedding):
        report = classifier.cross_validate(
            rows, corpus_db_path=db, n_folds=4, holdout_fraction=0.0,
        )
    assert report.n_rows == 16, "X, Y, Z all missing one required field — should be skipped"


def test_cv_raises_when_dataset_too_small(tmp_path: Path):
    db = tmp_path / "cache.db"
    rows = [_golden_row("A", "Title", "abs", "must_read")]
    with patch.object(classifier, "compute_embedding", side_effect=_fake_embedding):
        with pytest.raises(ValueError, match="at least"):
            classifier.cross_validate(
                rows, corpus_db_path=db, n_folds=5, holdout_fraction=0.0,
            )


# ---------------------------------------------------------------------------
# Threshold → priority mapping
# ---------------------------------------------------------------------------


def test_adaptive_priority_with_tuned_thresholds():
    thresholds = dict(t_keep=0.40, t_must=0.65, t_could=0.20)
    assert classifier._prob_to_priority_adaptive(0.90, **thresholds) == "must_read"
    assert classifier._prob_to_priority_adaptive(0.50, **thresholds) == "should_read"
    assert classifier._prob_to_priority_adaptive(0.30, **thresholds) == "could_read"
    assert classifier._prob_to_priority_adaptive(0.10, **thresholds) == "dont_read"
    # Boundaries are closed on the left (>=).
    assert classifier._prob_to_priority_adaptive(0.65, **thresholds) == "must_read"
    assert classifier._prob_to_priority_adaptive(0.40, **thresholds) == "should_read"
    assert classifier._prob_to_priority_adaptive(0.20, **thresholds) == "could_read"


def test_find_optimal_threshold_pulls_away_from_05_for_imbalanced_data():
    """When positives are rare, Youden-J should NOT just return 0.5."""
    # 100 negatives clustered low, 20 positives clustered high.
    rng = np.random.default_rng(42)
    p_neg = rng.uniform(0.0, 0.4, size=100)
    p_pos = rng.uniform(0.3, 0.9, size=20)
    p = np.concatenate([p_neg, p_pos])
    y = np.concatenate([np.zeros(100), np.ones(20)]).astype(int)
    t = classifier._find_optimal_threshold(y, p, strategy="youden")
    # The best Youden cut should land somewhere in the overlap, not at 0.5 by default.
    assert 0.0 < t < 1.0


def test_adaptive_cutoffs_split_keep_and_skip_groups():
    """Quantile-based 4-class cutoffs respect the keep threshold."""
    probs = np.array([0.05, 0.15, 0.25, 0.30, 0.55, 0.62, 0.80, 0.95])
    must_t, could_t = classifier._adaptive_4class_cutoffs(probs, t_keep=0.50)
    assert must_t >= 0.50, "must threshold must not fall below the keep cutoff"
    assert could_t <= 0.50, "could threshold must not exceed the keep cutoff"
    # 75th percentile of keep group [0.55, 0.62, 0.80, 0.95] is between 0.80 and 0.95.
    assert 0.78 <= must_t <= 0.96


def test_adaptive_cutoffs_avoid_zero_floor_for_degenerate_negatives():
    """When skip group clusters near zero, could_t must not collapse to 0."""
    # Negatives all near 0, positives spread above the keep threshold.
    probs = np.array([0.0, 0.001, 0.002, 0.003, 0.6, 0.7, 0.8, 0.9])
    _, could_t = classifier._adaptive_4class_cutoffs(probs, t_keep=0.5)
    assert could_t > 0.0, "dont_read bucket would be unreachable if could_t==0"


# ---------------------------------------------------------------------------
# 7-extras feature layout (Phase 1.11)
# ---------------------------------------------------------------------------


def test_extra_features_includes_corpus_affinity_and_prestige_with_defaults():
    """Defaults: 0.0 affinity, 3.0 prestige, all library features 0.0."""
    row = {"doi": "10.1/x", "venue": "Nature", "year": "2024"}
    out = classifier._extra_features(row, "title goes here", "abstract goes here")
    assert out.shape == (classifier.N_EXTRA_FEATURES,)
    assert classifier.N_EXTRA_FEATURES == 12
    # 5,6 = corpus_affinity, prestige_score; 7..11 = library features.
    assert out[5] == pytest.approx(0.0)
    assert out[6] == pytest.approx(3.0)
    assert out[7] == pytest.approx(0.0)   # nearest_kept_cosine
    assert out[8] == pytest.approx(0.0)   # positive_centroid_cosine
    assert out[9] == pytest.approx(0.0)   # recent_centroid_cosine
    assert out[10] == pytest.approx(0.0)  # topic_drift
    assert out[11] == pytest.approx(0.0)  # author_overlap_count


def test_extra_features_passes_aux_through():
    """All explicit aux values land in their assigned indices."""
    row = {"doi": "", "venue": "", "year": ""}
    out = classifier._extra_features(
        row, "t", "a",
        corpus_affinity=0.42, prestige_score=4.7,
        nearest_kept_cosine=0.81, positive_centroid_cosine=0.33,
        recent_centroid_cosine=0.55, topic_drift=0.22,
        author_overlap_count=3.0,
    )
    assert out[5] == pytest.approx(0.42)
    assert out[6] == pytest.approx(4.7)
    assert out[7] == pytest.approx(0.81)
    assert out[8] == pytest.approx(0.33)
    assert out[9] == pytest.approx(0.55)
    assert out[10] == pytest.approx(0.22)
    assert out[11] == pytest.approx(3.0)


def test_compute_aux_falls_back_to_neutral_when_no_providers():
    """No EmbeddingCache, no OpenAlex → defaults (0.0, 3.0)."""
    aff, pres = classifier._compute_aux(
        embed_cache=None, openalex_client=None,
        title="t", abstract="a", doi="", year=None,
    )
    assert aff == 0.0
    assert pres == 3.0


# ---------------------------------------------------------------------------
# Per-classifier CSV columns (FAIR persistence)
# ---------------------------------------------------------------------------


def test_write_predictions_uses_per_classifier_column_names(tmp_path: Path):
    """Two different classifier names must not collide in the CSV."""
    import csv as _csv

    csv_path = tmp_path / "golden.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=["item_key", "title", "gold_priority_final"])
        writer.writeheader()
        writer.writerow({"item_key": "A", "title": "p", "gold_priority_final": "must_read"})
        writer.writerow({"item_key": "B", "title": "q", "gold_priority_final": "dont_read"})

    # Fake report containing 2 CV predictions and no holdout.
    report = classifier.ClassifierReport(
        n_rows=2, n_positive=1,
        embeddings_computed=0, embeddings_cached=0,
        auc=0.9, elapsed_seconds=1.0,
        cv_probabilities=[0.8, 0.1],
        cv_predictions=["must_read", "dont_read"],
        item_keys=["A", "B"],
    )

    classifier.write_predictions_to_csv(csv_path, report, classifier_name="tabpfn")
    rows = list(_csv.DictReader(csv_path.open()))
    assert "cls_tabpfn_priority" in rows[0]
    assert "cls_tabpfn_score" in rows[0]
    assert "cls_tabpfn_split" in rows[0]
    assert rows[0]["cls_tabpfn_priority"] == "must_read"

    # Write a second classifier — both columns should coexist.
    report2 = classifier.ClassifierReport(
        n_rows=2, n_positive=1,
        embeddings_computed=0, embeddings_cached=0,
        auc=0.7, elapsed_seconds=1.0,
        cv_probabilities=[0.6, 0.2],
        cv_predictions=["should_read", "could_read"],
        item_keys=["A", "B"],
    )
    classifier.write_predictions_to_csv(csv_path, report2, classifier_name="lightgbm")
    rows = list(_csv.DictReader(csv_path.open()))
    assert rows[0]["cls_tabpfn_priority"] == "must_read", "first classifier must survive"
    assert rows[0]["cls_lightgbm_priority"] == "should_read"
    assert rows[1]["cls_tabpfn_priority"] == "dont_read"
    assert rows[1]["cls_lightgbm_priority"] == "could_read"


def test_write_predictions_rejects_invalid_classifier_name(tmp_path: Path):
    """Slashes / spaces / empty names would produce broken CSV column names."""
    csv_path = tmp_path / "golden.csv"
    csv_path.write_text("item_key,title,gold_priority_final\nA,p,must_read\n", encoding="utf-8")
    report = classifier.ClassifierReport(
        n_rows=1, n_positive=1,
        embeddings_computed=0, embeddings_cached=0,
        auc=0.5, elapsed_seconds=0.1,
        cv_probabilities=[0.8],
        cv_predictions=["must_read"],
        item_keys=["A"],
    )
    for bad in ("", "with space", "with/slash"):
        with pytest.raises(ValueError, match="invalid classifier_name"):
            classifier.write_predictions_to_csv(csv_path, report, classifier_name=bad)


def test_compute_metrics_auto_resolves_split_column(tmp_path: Path):
    """When split is set and priority_column ends in _priority, split_column
    is derived as <prefix>_split automatically."""
    import csv as _csv

    csv_path = tmp_path / "golden.csv"
    fields = [
        "item_key", "gold_priority_final", "gold_signal_strength",
        "cls_tabpfn_priority", "cls_tabpfn_split",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"item_key": "A", "gold_priority_final": "must_read",
                    "gold_signal_strength": "high",
                    "cls_tabpfn_priority": "must_read", "cls_tabpfn_split": "cv"})
        w.writerow({"item_key": "B", "gold_priority_final": "must_read",
                    "gold_signal_strength": "high",
                    "cls_tabpfn_priority": "dont_read", "cls_tabpfn_split": "holdout"})

    m_cv = classifier.compute_metrics_against_gold(
        csv_path, split="cv", priority_column="cls_tabpfn_priority",
    )
    m_ho = classifier.compute_metrics_against_gold(
        csv_path, split="holdout", priority_column="cls_tabpfn_priority",
    )
    assert m_cv["total"] == 1
    assert m_ho["total"] == 1
    assert m_cv["per_class"]["must_read"]["true_positive"] == 1
    assert m_ho["per_class"]["must_read"]["false_negative"] == 1


def test_calibrator_isotonic_compresses_extreme_raw_scores():
    """Isotonic should remap [0,1] monotonically to match the training label rate."""
    rng = np.random.default_rng(0)
    p_train = rng.uniform(0, 1, size=200)
    # Make the actual rate 50% regardless of raw score → calibrator should flatten.
    y_train = rng.integers(0, 2, size=200)
    cal = classifier._fit_calibrator(p_train, y_train, method="isotonic")
    p_test = np.array([0.01, 0.5, 0.99])
    out = classifier._apply_calibrator(cal, p_test)
    assert np.all((out >= 0.0) & (out <= 1.0))
    # Monotonicity preserved.
    assert out[0] <= out[1] <= out[2] + 1e-9


def test_calibrator_none_returns_input_unchanged():
    p = np.array([0.1, 0.4, 0.9])
    out = classifier._apply_calibrator(None, p)
    np.testing.assert_allclose(out, p)
