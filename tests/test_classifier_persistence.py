"""Phase 1.13: persistence of trained classifier artefacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from zotero_summarizer.services.model import classifier, classifier_embed, classifier_persistence


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fake_embedding(title: str, abstract: str, *, authors: str = "", venue: str = "") -> np.ndarray:
    """Deterministic embedding so the same content produces the same vector."""
    text = "|".join([title, abstract, authors, venue])
    rng = np.random.default_rng(hash(text) % (2**31))
    return rng.standard_normal(classifier.EMBEDDING_DIM).astype(np.float32)


def _write_golden_csv(path: Path, n_pos: int = 12, n_neg: int = 12) -> None:
    fields = [
        "item_key", "title", "authors", "year", "venue", "doi", "url", "abstract",
        "gold_priority_final", "gold_signal_strength", "gold_inferred_relevance",
        "gold_signal_tier",
    ]
    rows = []
    # Map priority → canonical continuous relevance (must=5, should=4, could=3, dont=1).
    rel_map = {"must_read": 5.0, "should_read": 4.0, "could_read": 3.0, "dont_read": 1.0}
    for i in range(n_pos):
        priority = "must_read" if i % 2 == 0 else "should_read"
        rows.append({
            "item_key": f"P{i}",
            "title": f"Positive paper {i}",
            "authors": f"Author {i}",
            "year": "2024",
            "venue": "Nature",
            "doi": f"10.1/p{i}",
            "url": "",
            "abstract": "positive abstract " * 20,
            "gold_priority_final": priority,
            "gold_signal_strength": "high",
            "gold_inferred_relevance": str(rel_map[priority]),
            "gold_signal_tier": "strong_positive",
        })
    for i in range(n_neg):
        priority = "dont_read" if i % 2 == 0 else "could_read"
        rows.append({
            "item_key": f"N{i}",
            "title": f"Negative paper {i}",
            "authors": f"Other {i}",
            "year": "2024",
            "venue": "",
            "doi": "",
            "url": "",
            "abstract": "off-topic abstract " * 20,
            "gold_priority_final": priority,
            "gold_signal_strength": "low",
            "gold_inferred_relevance": str(rel_map[priority]),
            "gold_signal_tier": "hard_veto",
        })
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ---------------------------------------------------------------------------
# train_and_save → load_trained → predict roundtrip
# ---------------------------------------------------------------------------


def test_train_and_save_writes_joblib_and_json(tmp_path: Path):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    output_dir = tmp_path / "models"
    with patch.object(classifier_embed, "compute_embedding", side_effect=_fake_embedding):
        trained = classifier_persistence.train_and_save(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )

    assert trained.classifier_name == "lightgbm"
    assert trained.fitted_model is not None
    assert (output_dir / "lightgbm.joblib").exists()
    assert (output_dir / "lightgbm.json").exists()

    meta = json.loads((output_dir / "lightgbm.json").read_text())
    assert meta["classifier_name"] == "lightgbm"
    assert meta["golden_csv_sha256"] == trained.golden_csv_sha256
    assert meta["thresholds"]["keep"] == round(trained.t_keep, 4)


def test_predict_after_load_matches_in_memory_predict(tmp_path: Path):
    """Save → reload → predict yields the same priorities as in-memory."""
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    output_dir = tmp_path / "models"
    new_items = [
        {"item_key": "X1", "title": "A new positive-ish paper", "abstract": "positive abstract " * 20},
        {"item_key": "X2", "title": "An off-topic paper", "abstract": "off-topic abstract " * 20},
    ]

    with patch.object(classifier_embed, "compute_embedding", side_effect=_fake_embedding):
        trained = classifier_persistence.train_and_save(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )
        pred_in_memory = trained.predict(
            new_items,
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
        )
        reloaded = classifier_persistence.load_trained(output_dir / "lightgbm.joblib")
        pred_reloaded = reloaded.predict(
            new_items,
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
        )

    assert len(pred_in_memory) == len(pred_reloaded) == 2
    for a, b in zip(pred_in_memory, pred_reloaded):
        assert a.predicted_priority == b.predicted_priority
        assert pytest.approx(a.calibrated_score) == b.calibrated_score


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


def test_load_or_train_loads_when_sha_matches(tmp_path: Path):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    output_dir = tmp_path / "models"

    with patch.object(classifier_embed, "compute_embedding", side_effect=_fake_embedding):
        first = classifier_persistence.load_or_train(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )
        # Second call: file unchanged → should reuse the saved artefact.
        second = classifier_persistence.load_or_train(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )

    assert first.golden_csv_sha256 == second.golden_csv_sha256
    assert first.training_metadata["trained_at"] == second.training_metadata["trained_at"], \
        "loaded artefact should have the same trained_at timestamp"


def test_load_or_train_retrains_when_sha_changes(tmp_path: Path):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    output_dir = tmp_path / "models"

    with patch.object(classifier_embed, "compute_embedding", side_effect=_fake_embedding):
        first = classifier_persistence.load_or_train(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )
        # Mutate the golden CSV — sha changes.
        with golden.open("a", encoding="utf-8") as f:
            f.write("\n")  # extra blank — content differs → sha changes.
        second = classifier_persistence.load_or_train(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )

    assert first.golden_csv_sha256 != second.golden_csv_sha256


def test_load_or_train_force_retrain_overrides_cache(tmp_path: Path):
    """force_retrain=True must call train_and_save even when sha matches."""
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    output_dir = tmp_path / "models"

    real_train = classifier_persistence.train_and_save
    call_count = [0]

    def counting_train(*args, **kwargs):
        call_count[0] += 1
        return real_train(*args, **kwargs)

    with patch.object(classifier_embed, "compute_embedding", side_effect=_fake_embedding), \
         patch.object(classifier_persistence, "train_and_save", side_effect=counting_train):
        first = classifier_persistence.load_or_train(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )
        assert call_count[0] == 1, "first call should always train"

        # Second call WITHOUT force: must reuse cache (no extra train call).
        classifier_persistence.load_or_train(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            n_folds=4,
        )
        assert call_count[0] == 1, "second call without force should hit cache"

        # Third call WITH force: must train again.
        second = classifier_persistence.load_or_train(
            golden,
            classifier_name="lightgbm",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=output_dir,
            force_retrain=True,
            n_folds=4,
        )
        assert call_count[0] == 2, "force_retrain must bypass the cache"

    assert first.golden_csv_sha256 == second.golden_csv_sha256


def test_train_rejects_unknown_classifier_name(tmp_path: Path):
    golden = tmp_path / "golden.csv"
    _write_golden_csv(golden)
    with pytest.raises(ValueError, match="unsupported"):
        classifier_persistence.train_and_save(
            golden,
            classifier_name="rf",
            corpus_db_path=tmp_path / "corpus.db",
            goals_config=None,
            output_dir=tmp_path / "models",
        )


def test_load_trained_raises_when_file_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        classifier_persistence.load_trained(tmp_path / "absent.joblib")
