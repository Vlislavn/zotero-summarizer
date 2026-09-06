"""Production OOF/forward diagnostics cannot consult held-out engagement."""

import csv

import numpy as np
import pytest

from zotero_summarizer.services.model import classifier, classifier_embed, classifier_store
from zotero_summarizer.services.model import classifier_training as training
from zotero_summarizer.services.model.label_weights import compute_row_weights
from zotero_summarizer.settings import Settings


def _expected_library(groups, reference_groups):
    expected = []
    for group in groups:
        kept = set(reference_groups) - {group}
        if not kept:
            expected.append([0, 0, 0, 0, 0])
            continue
        n_recent = sum(g < 90 for g in kept) or len(kept)
        # e_group + 0.5*e_common: other-paper cosine=.2. Whole twins give
        # centroid = mean(unique one-hots) + .5*e_common, never the candidate.
        centroid = 0.25 / np.sqrt(1.25 * (0.25 + 1 / len(kept)))
        recent = 0.25 / np.sqrt(1.25 * (0.25 + 1 / n_recent))
        expected.append([0.2, centroid, recent, recent - centroid, 0])
    return np.asarray(expected)


@pytest.mark.parametrize("route", ["train", "temporal", "predict"])
def test_diagnostics_rebuild_positive_library_per_fold(tmp_path, monkeypatch, route):
    rows = [
        {
            "item_key": f"K{i}", "title": f"Paper {i // 2}", "abstract": "Evidence",
            "doi": f"10.1234/paper{i // 2}", "authors": f"Author{i // 2}",
            "gold_priority_final": "should_read", "gold_inferred_relevance": str(1 + i // 2 % 5),
            "gold_signal_tier": "strong_positive" if i % 2 else "high_positive",
            "gold_signal_strength": "high", "days_since_added": str(1 + i // 2),
        }
        for i in range(200)
    ]
    settings = Settings.load(project_root=tmp_path)
    settings.golden_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.golden_csv_path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    before = settings.golden_csv_path.read_bytes()

    def embedding(title, abstract, **kwargs):
        result = np.zeros(classifier.EMBEDDING_DIM, dtype=np.float32)
        result[int(title.split()[-1])] = 1
        result[-1] = 0.5
        return result

    monkeypatch.setattr(classifier_embed, "compute_embedding", embedding)
    monkeypatch.setattr(classifier_embed, "compute_embeddings_batch", lambda items, **kw: [
        embedding(title, abstract) for title, abstract in items
    ])
    calls = []

    def fit(name, X, y, val, **kwargs):
        train_groups = np.argmax(X[:, :classifier.EMBEDDING_DIM], axis=1)
        val_groups = np.argmax(val[:, :classifier.EMBEDDING_DIM], axis=1)
        assert set(train_groups).isdisjoint(val_groups)
        lo = classifier.EMBEDDING_DIM + 7
        # Shared background detects OTHER held-out papers leaking into P,
        # even after the candidate's own twins have been excluded correctly.
        if route != "temporal" or len(val) == 40:
            np.testing.assert_allclose(
                val[:, lo:lo + 5], _expected_library(val_groups, train_groups), atol=1e-6,
            )
        np.testing.assert_array_equal(y, 1 + train_groups % 5)
        if route != "temporal":
            np.testing.assert_allclose(
                X[:, lo:lo + 5], _expected_library(train_groups, train_groups), atol=1e-6,
            )
        expected_weights = compute_row_weights([
            row for row in rows if int(row["title"].split()[-1]) in set(train_groups)
        ])
        np.testing.assert_array_equal(kwargs["sample_weight"], expected_weights)
        calls.append(val_groups)
        return None, 1 + val_groups % 5

    monkeypatch.setattr(classifier, "_fit_predict", fit)
    if route != "predict":
        trained = training.train_and_save(
            settings.golden_csv_path, classifier_name="logreg", goals_config=None,
            corpus_db_path=settings.corpus_db_path, output_dir=settings.model_dir, n_folds=3,
        )
        loaded = classifier_store.load_trained(settings.model_dir / "logreg.zip")
        assert loaded.training_metadata == trained.training_metadata
        assert loaded.training_metadata["temporal_holdout_n"] == 40
        assert loaded.training_metadata["temporal_spearman"] == 1
        np.testing.assert_allclose(
            loaded.X_train[:, classifier.EMBEDDING_DIM + 7:classifier.EMBEDDING_DIM + 12],
            _expected_library(np.arange(200) // 2, range(100)), atol=1e-6,
        )
        from zotero_summarizer.services.model.library_features import compute_library_features
        library = loaded._build_predict_library()
        # Existing item and an unseen feed alias of the same paper must both
        # exclude the whole group, with author exclusion surviving ZIP reload.
        for key in ("K0", "feed:unseen"):
            candidate = {**rows[0], "item_key": key}
            np.testing.assert_allclose(compute_library_features(
                embedding(candidate["title"], candidate["abstract"]), library,
                candidate_row=candidate,
            ), _expected_library([0], range(100))[0], atol=1e-6)
        def predict(X):
            lo = classifier.EMBEDDING_DIM + 7
            np.testing.assert_allclose(
                X[:, lo:lo + 5], _expected_library([0, 0], range(100)), atol=1e-6,
            )
            return np.full(len(X), 3.0)

        monkeypatch.setattr(loaded, "_raw_predict", predict)
        rescored = loaded.predict(
            [rows[0], candidate], corpus_db_path=settings.corpus_db_path, goals_config=None,
        )
        assert [p.item_key for p in rescored] == ["K0", "feed:unseen"]
        assert len(calls[-1]) == 40
    else:
        predictions, diagnostics = classifier.predict_new_items(
            rows, [{"item_key": "NEW", "title": "Paper 110", "abstract": "New evidence"}],
            corpus_db_path=settings.corpus_db_path, classifier_name="logreg", n_folds=3,
        )
        assert diagnostics["oof_spearman"] == pytest.approx(1)
        assert [p.item_key for p in predictions] == ["NEW"]
        assert predictions[0].raw_score == 1
        assert calls[-1].tolist() == [110]
    assert len(calls) == 4
    assert sorted(np.concatenate(calls[:3])) == sorted(list(range(100)) * 2)
    assert settings.golden_csv_path.read_bytes() == before
