"""OOF, temporal evaluation and production use the same fitted-model recipe."""
import csv
import json
from types import SimpleNamespace

import lightgbm as lgb
import numpy as np
import pytest

from zotero_summarizer.services.model import classifier_embed, classifier_inputs
from zotero_summarizer.services.model import classifier_training as training
from test_classifier_persistence import _fake_embeddings_batch, _write_golden_csv


@pytest.mark.parametrize("pca", [None, 2])
@pytest.mark.parametrize("replace_tuning", [False, True])
def test_all_fits_share_tuned_parameters_and_pca(tmp_path, monkeypatch, pca, replace_tuning):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden, n_pos=90, n_neg=90)
    with golden.open() as source:
        rows = list(csv.DictReader(source))
    for i, row in enumerate(rows):
        row["days_since_added"] = str(i + 1)
    with golden.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    tuned_path = tmp_path / "tuning.json"
    tuned_path.write_text(json.dumps({"lgbm_params": {"n_estimators": 3, "num_leaves": 4},
                                      "pca_specter_dim": pca}))
    from zotero_summarizer.services.model import tune
    monkeypatch.setattr(tune, "settings", lambda: SimpleNamespace(tuned_params_path=tuned_path))
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    fits = []
    original_fit = lgb.LGBMRegressor.fit

    def capture(self, X, y, *args, **kwargs):
        fits.append((self.get_params(), X.shape, np.asarray(kwargs["sample_weight"]).copy()))
        if replace_tuning and len(fits) == 1:
            tuned_path.write_text(json.dumps({"lgbm_params": {"n_estimators": 7}, "pca_specter_dim": 4}))
        return original_fit(self, X, y, *args, **kwargs)

    monkeypatch.setattr(lgb.LGBMRegressor, "fit", capture)
    trained = training.train_and_save(
        golden, classifier_name="lightgbm", corpus_db_path=tmp_path / "corpus.db",
        goals_config=None, output_dir=tmp_path / "models", n_folds=3,
    )
    assert len(fits) == 5  # three OOF folds, temporal holdout, final fit
    for params, shape, weights in fits:
        assert params["n_estimators"] == 3
        assert params["num_leaves"] == 4
        assert shape[1] == (780 if pca is None else 14)
        assert len(weights) == shape[0]
    assert fits[-1][1][0] == 180
    assert all(shape[0] < 180 for _, shape, _ in fits[:-1])
    loaded = training.load_training_inputs(
        golden, classifier_name="lightgbm", corpus_db_path=tmp_path / "corpus.db",
        goals_config=None, n_folds=3, pca_dim=100,
    )
    assert (trained.training_metadata["training_input_sha256"] != loaded.sha256) is replace_tuning
    assert trained.training_metadata["fit_options"] == {
        "lgbm_params": {"n_estimators": 3, "num_leaves": 4}, "pca_specter_dim": pca,
    }


def test_input_snapshot_reads_tuning_once(tmp_path, monkeypatch):
    golden = tmp_path / "golden.csv"
    golden.write_text("item_key,title\nA,T\n")
    calls = []

    def read_tuning():
        calls.append(True)
        return {"n_estimators": len(calls)}, 2

    monkeypatch.setattr(classifier_inputs, "load_tuned_params", read_tuning)
    inputs = classifier_inputs.load_training_inputs(
        golden, classifier_name="lightgbm", corpus_db_path=tmp_path / "corpus.db",
        goals_config=None, n_folds=3, pca_dim=100,
    )
    assert len(calls) == 1
    assert inputs.lgbm_params == {"n_estimators": 1}
    assert inputs.pca_specter_dim == 2
