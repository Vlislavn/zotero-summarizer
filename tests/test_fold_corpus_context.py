"""Held-out and unassigned corpus engagement cannot become training features."""

import csv
from collections import Counter
from unittest.mock import Mock

import numpy as np
import pytest

from zotero_summarizer.models.triage import CorpusItem
from zotero_summarizer.services.model import classifier, classifier_embed, classifier_training, tune
from zotero_summarizer.services.model.eval_baseline import _runners
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage.corpus import EmbeddingCache


def _vector(group):
    angle = group / 13
    return np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)


def _expected(groups, reference):
    result = []
    for group in groups:
        positive = [g for g in reference if g != group and g % 3]
        negative = [g for g in reference if g != group and not g % 3]
        pos = np.mean([_vector(group) @ _vector(g) for g in positive]) if positive else 0
        neg = np.mean([_vector(group) @ _vector(g) for g in negative]) if negative else 0
        result.append(round(float(np.clip(pos - neg, -1, 1)), 4))
    return result


@pytest.mark.parametrize("route", ["train", "predict", "baseline", "learning", "tune", "legacy", "tool"])
def test_every_fit_uses_only_its_training_corpus(tmp_path, monkeypatch, route):
    rows = [{
        "item_key": f"K{i}", "title": f"Paper {i // 2}", "abstract": "Evidence",
        "doi": f"10.1234/paper{i // 2}", "year": "2020",
        "gold_priority_final": "should_read" if i // 2 % 3 else "dont_read",
        "gold_inferred_relevance": str(1 + i // 2 % 5),
        "gold_signal_tier": "strong_positive" if i % 2 else "high_positive",
        "gold_signal_strength": "high", "days_since_added": str(1 + i // 2),
    } for i in range(200)]
    settings = Settings.load(project_root=tmp_path)
    settings.golden_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.golden_csv_path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    before = settings.golden_csv_path.read_bytes()
    cache = EmbeddingCache(settings.corpus_db_path, "test-encoder")
    encode = Mock(side_effect=lambda text: _vector(int(text.split()[1].rstrip("."))).tolist())
    monkeypatch.setattr(cache, "_embed", encode)
    cache.upsert_items([CorpusItem(
        item_id=f"C{g}", title=f"Renamed {g}", abstract="Evidence",
        doi=f"https://doi.org/10.1234/PAPER{g}", tags=["🧠" if g % 3 else "❌"],
    ) for g in range(101)])  # 100 is outside the labelled cohort: never fold context.
    encode.reset_mock()
    monkeypatch.setattr(classifier, "_build_aux_providers", lambda *a, **k: (cache, None, None))

    def embedding(title, abstract, **kwargs):
        result = np.zeros(classifier.EMBEDDING_DIM, dtype=np.float32)
        result[int(title.split()[-1])] = 1
        return result

    monkeypatch.setattr(classifier_embed, "compute_embedding", embedding)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", lambda items, **kw: [
        embedding(title, abstract) for title, abstract in items
    ])
    fits = []
    col = classifier.EMBEDDING_DIM + 5

    def fit(name, X, y, val, **kwargs):
        tr = np.argmax(X[:, :classifier.EMBEDDING_DIM], axis=1)
        vl = np.argmax(val[:, :classifier.EMBEDDING_DIM], axis=1)
        final = route == "predict" and vl.tolist() == [110]
        # Legacy row-wise folds can split twins: held-out identity wins over
        # a train alias. Group-aware routes must keep the papers disjoint.
        reference = set(range(101)) if final else set(tr) - set(vl)
        if route == "legacy":
            reference = {g for g, count in Counter(tr).items() if count == 2}
        if route != "legacy":
            assert set(tr).isdisjoint(vl)
        np.testing.assert_allclose(X[:, col], _expected(tr, reference), atol=1e-6)
        np.testing.assert_allclose(val[:, col], _expected(vl, reference), atol=1e-6)
        if route != "legacy":
            assert kwargs["sample_weight"] is not None
            assert len(kwargs["sample_weight"]) == len(tr)
        fits.append((tr, vl))
        scores = 1 + vl % 5
        return (X[:, 0] if kwargs.get("return_train_probs") else None), scores

    monkeypatch.setattr(classifier, "_fit_predict", fit)
    if route == "train":
        trained = classifier_training.train_and_save(
            settings.golden_csv_path, classifier_name="logreg", goals_config=None,
            corpus_db_path=settings.corpus_db_path, output_dir=settings.model_dir, n_folds=3,
        )
        np.testing.assert_allclose(trained.X_train[:, col], _expected(np.arange(200) // 2, range(101)))
        assert len(fits) == 4 and len(fits[-1][1]) == 40
        assert trained.training_metadata["temporal_spearman"] == 1
    elif route == "predict":
        predictions, _ = classifier.predict_new_items(
            rows, [{"item_key": "NEW", "title": "Paper 110", "abstract": "New evidence"}],
            corpus_db_path=settings.corpus_db_path, classifier_name="logreg", n_folds=3,
        )
        assert predictions[0].item_key == "NEW" and len(fits) == 4
    elif route in {"baseline", "learning"}:
        kwargs = {"n_repeats": 1, "n_folds": 3, "n_bootstrap": 30}
        if route == "learning":
            kwargs["fractions"] = (0.6, 1.0)
        getattr(_runners, "run_baseline" if route == "baseline" else "run_learning_curve")(
            rows, corpus_db_path=settings.corpus_db_path, goals_config=None, **kwargs,
        )
        assert len(fits) == (3 if route == "baseline" else 6)
    elif route == "tune":
        result = tune.tune_lightgbm(
            rows, corpus_db_path=settings.corpus_db_path, goals_config=None,
            n_folds=3, n_trials=2, output_path=tmp_path / "best.json",
        )
        assert result.best_value == pytest.approx(1) and len(fits) == 6
    elif route == "legacy":
        classifier.cross_validate(
            rows, corpus_db_path=settings.corpus_db_path, n_folds=3, calibration="none",
        )
        assert len(fits) == 4
    else:
        from tools import eval_temporal_objective
        from zotero_summarizer.services import _common
        from zotero_summarizer.storage import repositories

        with repositories.with_db_path(settings.triage_db_path):
            repositories.init_db()
        monkeypatch.setattr(_common, "settings", lambda: settings)
        monkeypatch.setattr(_common, "read_config", lambda path: None)
        monkeypatch.setattr(eval_temporal_objective, "_fit_ranker", lambda X, y, val, sw: (
            fit("ranker", X, y, val, sample_weight=sw)[1]
        ))
        eval_temporal_objective.main()
        assert len(fits) == 7
    assert encode.call_count == 200 + (route == "predict")
    assert settings.golden_csv_path.read_bytes() == before
    assert len(cache.list_item_ids()) == 101


def test_snapshot_reuses_weights_and_subsets_candidates_without_losing_context(tmp_path, monkeypatch):
    cache = EmbeddingCache(tmp_path / "corpus.db", "test-encoder")
    vectors = {"Alpha": [1, 0], "Beta": [0.6, 0.8], "Gamma": [0, 1], "Outside": [-1, 0], "New": [1, 0]}
    monkeypatch.setattr(cache, "_embed", lambda text: vectors[text.split(".")[0]])
    items = [
        CorpusItem(item_id="A", title="Alpha", doi="10.1234/a", tags=["🧠"]),
        CorpusItem(item_id="B", title="Beta", doi="10.1234/b", tags=["👀"]),
        CorpusItem(item_id="C", title="Gamma", doi="10.1234/c", tags=["❌"]),
        CorpusItem(item_id="D", title="Outside", manual_note_count=1),
    ]
    cache.upsert_items(items)
    rows = [{"title": name, "doi": f"10.1234/{key}"} for name, key in (
        ("Alpha", "a"), ("Beta", "b"), ("Gamma", "c"), ("New", "new"),
    )]
    snapshot = cache.corpus_affinity(rows)
    np.testing.assert_allclose(snapshot.scores(np.array([0, 1])), [.6, .6, .32, .84])
    np.testing.assert_allclose(snapshot.scores(np.array([1, 2])), [.6, -.8, .8, .6])
    np.testing.assert_array_equal(snapshot.scores(np.array([], dtype=int)), 0)
    np.testing.assert_allclose(snapshot.scores(), [
        cache.affinity_and_goals(row["title"], "", doi=row["doi"])[0] for row in rows
    ])
    full = snapshot.scores().copy()
    # Reordering/subsetting must select candidate rows, not corpus columns.
    np.testing.assert_allclose(snapshot.subset(np.array([2, 0, 3])).scores(np.array([1])), [0, 0, 1])
    items[1].tags = ["❌"]
    cache.upsert_items([items[1]])
    np.testing.assert_array_equal(snapshot.scores(), full)
    assert not np.array_equal(cache.corpus_affinity(rows).scores(), full)


@pytest.mark.parametrize("empty", [True, False])
def test_empty_or_zero_corpus_has_finite_zero_affinity(tmp_path, monkeypatch, empty):
    cache = EmbeddingCache(tmp_path / "corpus.db", "test-encoder")
    encode = Mock(return_value=[0, 0])
    monkeypatch.setattr(cache, "_embed", encode)
    if not empty:
        cache.upsert_items([CorpusItem(item_id="A", title="Alpha", tags=["🧠"])])
    encode.reset_mock()
    snapshot = cache.corpus_affinity([{"title": "Alpha"}, {"title": "New"}])
    np.testing.assert_array_equal(snapshot.scores(), [0, 0])
    np.testing.assert_array_equal(snapshot.scores(np.array([0])), [0, 0])
    assert encode.call_count == (0 if empty else 2)


def test_learning_subset_keeps_row_weights_and_corpus_aligned():
    from zotero_summarizer.services.model.eval_baseline._featurize import FeaturizedGolden
    from zotero_summarizer.storage.corpus_types import CorpusAffinity

    rows = [{"item_key": str(i)} for i in range(3)]
    corpus = CorpusAffinity(np.eye(3), np.ones(3), np.eye(3, dtype=bool))
    feat = FeaturizedGolden(
        X=np.arange(9).reshape(3, 3), y_binary=np.array([1, 0, 1]),
        y_continuous=np.array([5, 1, 4]), y_priority=["must_read", "dont_read", "should_read"],
        item_keys=["0", "1", "2"], n_features=3, selected_rows=rows,
        sample_weights=np.array([.1, .2, .3]), corpus_affinity=corpus,
    )
    subset = _runners._subset_featurized(feat, np.array([2, 0]))
    np.testing.assert_array_equal(subset.X, feat.X[[2, 0]])
    np.testing.assert_array_equal(subset.sample_weights, [.3, .1])
    np.testing.assert_array_equal(subset.corpus_affinity.similarities, corpus.similarities[[2, 0]])
    np.testing.assert_array_equal(subset.corpus_affinity.same_paper, corpus.same_paper[[2, 0]])
    np.testing.assert_array_equal(subset.y_continuous, [4, 5])
    assert subset.selected_rows == [rows[2], rows[0]]
    assert subset.item_keys == ["2", "0"]
