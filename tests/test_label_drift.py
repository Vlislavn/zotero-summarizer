"""Drift compares training with its predecessor artifact, not an evaluation log."""
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.model.classifier_drift import label_distribution, log_label_drift
from zotero_summarizer.services.model.classifier_store import write_archive


def _prior(model_dir, n_train):
    write_archive(SimpleNamespace(
        classifier_name="lightgbm", golden_csv_sha256="abc", feature_dim=1,
        pca_dim=0, t_keep=0.4, t_must=0.7, t_could=0.5,
        training_metadata={"n_train": n_train},
    ), model_dir / "lightgbm.zip")


def test_label_distribution_canonical_order():
    assert label_distribution(["must_read", "must_read", "should_read", "could_read",
                               "dont_read", "dont_read", ""]) == {
        "must_read": 2, "should_read": 1, "could_read": 1, "dont_read": 2, "": 1,
    }


def test_drift_first_train_has_no_delta(tmp_path):
    # Even a training log row is not evidence of a predecessor in this output dir.
    (tmp_path / "runs.jsonl").write_text(
        '{"type":"train_artifact","classifier":"lightgbm","cv":{"n_rows":100}}'
    )
    result = log_label_drift(["must_read", "dont_read"], 2,
                             classifier_name="lightgbm", model_dir=tmp_path)
    assert result["prior_n_train"] is None
    assert result["n_train_delta"] is None


def test_drift_uses_artifact_despite_newer_evaluation_log(tmp_path):
    _prior(tmp_path, 2061)
    (tmp_path / "runs.jsonl").write_text(
        '{"classifier":"lightgbm","cv":{"n_rows":12}}'
    )
    result = log_label_drift(["must_read"] * 83 + ["dont_read"] * 2000, 2083,
                             classifier_name="lightgbm", model_dir=tmp_path)
    assert result["prior_n_train"] == 2061
    assert result["n_train_delta"] == 22
    assert result["distribution"]["must_read"] == 83


def test_drift_uses_restored_predecessor_not_latest_historical_training(tmp_path):
    _prior(tmp_path, 100)
    (tmp_path / "runs.jsonl").write_text(
        '{"type":"train_artifact","classifier":"lightgbm","cv":{"n_rows":200}}'
    )
    result = log_label_drift(["dont_read"], 110,
                             classifier_name="lightgbm", model_dir=tmp_path)
    assert result["prior_n_train"] == 100
    assert result["n_train_delta"] == 10


@pytest.mark.parametrize("n_train", [-1, 1.5, True, "12"])
def test_malformed_predecessor_size_fails_loudly(tmp_path, n_train):
    _prior(tmp_path, n_train)
    with pytest.raises(ValueError, match="n_train"):
        log_label_drift([], 10, classifier_name="lightgbm", model_dir=tmp_path)


@pytest.mark.parametrize("n_train", [None, 0])
def test_unknown_and_zero_predecessor_sizes_stay_distinct(tmp_path, n_train):
    _prior(tmp_path, n_train)
    result = log_label_drift([], 10, classifier_name="lightgbm", model_dir=tmp_path)
    assert result["prior_n_train"] == n_train
    assert result["n_train_delta"] == (None if n_train is None else 10)
