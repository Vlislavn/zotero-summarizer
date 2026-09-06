"""Calendar rollover and even-cohort imputation must not silently freeze ranking."""
import csv
import sqlite3
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from zotero_summarizer.services.model import classifier, classifier_embed, classifier_features as features
from zotero_summarizer.services.model import classifier_inputs, classifier_persistence as persistence
from zotero_summarizer.services.model import classifier_training as training
from zotero_summarizer.services.model.rank_blend import blend_scores
from zotero_summarizer.models.config import GoalsConfig
from zotero_summarizer.storage import repositories
from test_classifier_persistence import _fake_embeddings_batch, _write_golden_csv


@pytest.mark.parametrize("known,expected", [
    ([0.1, 0.9], 0.5), ([0.0, 0.2, 0.4, 1.0], 0.3),
    ([0.0, 0.2, 1.0], 0.2), ([0.0], 0.5), ([], 0.5),
])
def test_unknown_prestige_gets_actual_median(known, expected):
    n = len(known) + 1
    scores = blend_scores([3.0] * n, [None] * n, [*known, None], prestige_weight=1.0)
    assert scores[-1] == pytest.approx(expected)


def test_library_and_slate_do_not_tie_unknown_with_best_known():
    from zotero_summarizer.services.library._ranking import _blended_sort
    from zotero_summarizer.services.triage.daily_select._candidate import attach_rank_scores

    rows = [{"item_key": key, "relevance_score": 3.0, "composite_score": 3.0,
             "goal_sim": None, "prestige_known": prestige is not None,
             "prestige_score": prestige, "citation_percentile": prestige,
             "date_added": date} for key, prestige, date in (
                 ("low", 0.1, "2026-01-01"), ("high", 0.9, "2026-01-01"),
                 ("unknown", None, "2026-02-01"),
             )]
    attach_rank_scores(rows)
    assert rows[0]["rank_score"] < rows[2]["rank_score"] < rows[1]["rank_score"]
    _blended_sort(rows)
    assert [row["item_key"] for row in rows] == ["high", "unknown", "low"]


def test_recency_uses_current_utc_year_without_reimport(monkeypatch):
    clock = Mock()
    monkeypatch.setattr(features, "datetime", clock, raising=False)
    for reference_year in (2027, 2030):
        clock.now.return_value = SimpleNamespace(year=reference_year)
        assert classifier._extra_features({"year": "2024"}, "Title", "Abstract")[2] == reference_year - 2024
    clock.now.assert_called_with(timezone.utc)
    for year, expected in (("", 20), ("unknown", 20), ("1900", 20), ("2040", 0), ("2030-01-02", 0)):
        assert classifier._extra_features({"year": year}, "Title", "Abstract")[2] == expected


def test_year_rollover_invalidates_saved_model_and_daemon(tmp_path, monkeypatch):
    from zotero_summarizer.services.triage.feeds import _gate
    from zotero_summarizer.settings import Settings

    clock = Mock()
    clock.now.return_value = SimpleNamespace(year=2030)
    monkeypatch.setattr(features, "datetime", clock, raising=False)
    settings = Settings.load(project_root=tmp_path)
    settings.golden_csv_path.parent.mkdir(parents=True, exist_ok=True)
    _write_golden_csv(settings.golden_csv_path)
    with sqlite3.connect(settings.triage_db_path) as conn:
        repositories.apply_schema(conn)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    config = GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "test", "refine_model": "test", "api_base": "http://localhost", "api_key_env": "TEST_KEY",
    })
    config.corpus.enabled = config.prestige.enabled = False
    config.classifier_gate.model_name = "logreg"
    config.classifier_gate.n_folds = 3
    args = dict(classifier_name="logreg", corpus_db_path=settings.corpus_db_path,
                goals_config=config, output_dir=settings.model_dir, n_folds=3,
                triage_db_path=settings.triage_db_path)
    first = persistence.load_or_train(settings.golden_csv_path, **args)
    state = SimpleNamespace(classifier_gate=first, classifier_gate_training=False,
                            app_state=SimpleNamespace(config=config))
    monkeypatch.setattr(_gate, "get_state", lambda: state)
    monkeypatch.setattr(_gate, "get_settings", lambda: settings)
    thread = Mock()
    monkeypatch.setattr("threading.Thread", thread)
    retrain = Mock(return_value=object())
    monkeypatch.setattr(persistence, "train_and_save", retrain)
    assert persistence.load_or_train(settings.golden_csv_path, **args).model_sha256 == first.model_sha256
    assert _gate.schedule_gate_retrain_async("test") is False
    clock.now.return_value = SimpleNamespace(year=2031)
    assert persistence.load_or_train(settings.golden_csv_path, **args) is retrain.return_value
    retrain.assert_called_once()
    assert _gate.schedule_gate_retrain_async("test") is True
    thread.return_value.start.assert_called_once()


def test_training_pins_year_before_featurizing_across_new_year(tmp_path, monkeypatch):
    clock = Mock()
    clock.now.return_value = SimpleNamespace(year=2030)
    monkeypatch.setattr(features, "datetime", clock, raising=False)
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", _fake_embeddings_batch)
    featurize = training._featurize_training_matrix

    def rollover(*args, **kwargs):
        clock.now.return_value = SimpleNamespace(year=2031)
        return featurize(*args, **kwargs)

    monkeypatch.setattr(training, "_featurize_training_matrix", rollover)
    trained = training.train_and_save(
        golden, classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db",
        goals_config=None, output_dir=tmp_path / "models", n_folds=3,
    )
    assert trained.training_metadata["feature_reference_year"] == 2030
    with golden.open() as source:
        years = [int(row["year"]) for row in csv.DictReader(source)]
    np.testing.assert_array_equal(trained.X_train[:, classifier.EMBEDDING_DIM + 2], [2030 - y for y in years])
    current = classifier_inputs.load_training_inputs(
        golden, classifier_name="logreg", corpus_db_path=tmp_path / "corpus.db", goals_config=None, n_folds=3, pca_dim=100,
    )
    assert current.sha256 != trained.training_metadata["training_input_sha256"]
