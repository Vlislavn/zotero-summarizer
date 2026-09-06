"""Optuna must fit the weighted recipe without splitting paper twins."""

from unittest.mock import Mock

import numpy as np
import pytest

from zotero_summarizer.services.model import classifier, tune
from zotero_summarizer.services.model.eval_baseline import _featurize


def _twins():
    rows = [
        {
            "item_key": f"feed:{i}" if i % 2 else f"ZOTERO{i}",
            "doi": f"10.1234/paper{i // 2}" if i < 10 else "",
            "title": f"Paper {i // 2}" if i % 2 else f" PAPER  {i // 2} ",
            "authors": f"Author{i // 2}",
            "gold_signal_tier": "strong_positive",
            "days_since_added": "10",
        }
        for i in range(20)
    ]
    X = np.zeros((20, classifier.FEATURE_DIM), dtype=np.float32)
    X[np.arange(20), np.arange(20) // 2] = 1
    X[:, classifier.EMBEDDING_DIM] = np.arange(20)
    X[:, classifier.EMBEDDING_DIM + 7:classifier.EMBEDDING_DIM + 12] = 1
    return _featurize.FeaturizedGolden(
        X=X, y_binary=np.ones(20), y_continuous=1 + np.arange(20) / 5,
        y_priority=["should_read"] * 20,
        item_keys=[row["item_key"] for row in rows], n_features=X.shape[1],
        sample_weights=0.1 + (np.arange(20) % 5) * 0.2, selected_rows=rows,
    )


@pytest.mark.parametrize("probe", ["groups", "weights", "library"])
def test_tuning_uses_fold_truth(tmp_path, monkeypatch, probe):
    feat = _twins()
    original = feat.X.copy()
    featurize = Mock(return_value=feat)
    monkeypatch.setattr(_featurize, "featurize_golden", featurize)
    held_out = []

    def fit(name, X_train, y_train, X_val, **kwargs):
        tr = X_train[:, classifier.EMBEDDING_DIM].astype(int)
        vl = X_val[:, classifier.EMBEDDING_DIM].astype(int)
        held_out.extend(vl)
        assert name == "lightgbm" and kwargs["objective"] == "regression"
        np.testing.assert_array_equal(y_train, feat.y_continuous[tr])
        if probe == "groups":
            assert set(tr // 2).isdisjoint(vl // 2)
        elif probe == "weights":
            np.testing.assert_array_equal(kwargs.get("sample_weight"), feat.sample_weights[tr])
        else:
            # Orthogonal papers and unique authors: a held-out twin's entire
            # library block must be zero, including nearest cosine and authors.
            lo = classifier.EMBEDDING_DIM + 7
            np.testing.assert_array_equal(X_val[:, lo:lo + 5], 0)
        return None, feat.y_continuous[vl]

    monkeypatch.setattr(classifier, "_fit_predict", fit)
    # The featurizer drops this row: grouping must use aligned selected_rows.
    rows = [{"item_key": "ineligible"}, *feat.selected_rows]
    output = tmp_path / "best.json"
    result = tune.tune_lightgbm(
        rows, corpus_db_path=tmp_path / "corpus.db", goals_config=None,
        n_trials=2, n_folds=3, output_path=output,
    )
    featurize.assert_called_once()
    assert result.best_value == pytest.approx(1)
    assert result.n_trials_completed == 2
    assert sorted(held_out) == sorted(list(range(20)) * 2)
    assert tune.load_tuned_params(output) == (
        result.best_params, result.best_pca_specter_dim or None,
    )
    np.testing.assert_array_equal(feat.X, original)


def test_tuning_rejects_too_few_paper_groups_before_fit(tmp_path, monkeypatch):
    feat = _twins()
    for row in feat.selected_rows:
        row["doi"] = "10.1234/one-paper"
    monkeypatch.setattr(_featurize, "featurize_golden", Mock(return_value=feat))
    fit = Mock(side_effect=lambda name, X, y, val, **kw: (
        None, feat.y_continuous[val[:, classifier.EMBEDDING_DIM].astype(int)],
    ))
    monkeypatch.setattr(classifier, "_fit_predict", fit)
    output = tmp_path / "best.json"
    output.write_bytes(b"previous tuning")
    with pytest.raises(ValueError, match="number of groups"):
        tune.tune_lightgbm(
            feat.selected_rows, corpus_db_path=tmp_path / "corpus.db",
            goals_config=None, n_trials=1, n_folds=3, output_path=output,
        )
    fit.assert_not_called()
    assert output.read_bytes() == b"previous tuning"
