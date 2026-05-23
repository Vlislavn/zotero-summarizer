"""Load cached classifiers + lazy retrain for the daemon gate.

The artefact lives in ``classifier_artifact``; training in ``classifier_training``.
This module owns the on-disk location, load, and load-or-retrain logic, and
re-exports the artefact/training API for back-compat (``classifier_persistence.X``)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib

from zotero_summarizer.services import run_log
from zotero_summarizer.services.model.classifier_artifact import (  # noqa: F401  (re-export)
    DEFAULT_MODEL_DIR,
    TrainedClassifier,
    _EXTRA_FEATURE_NAMES,
    _format_shap,
)
from zotero_summarizer.services.model.classifier_training import (  # noqa: F401  (re-export)
    save_trained,
    train_and_save,
)

LOGGER = logging.getLogger(__name__)


def load_trained(joblib_path: Path) -> TrainedClassifier:
    """Read a previously-saved artefact. Raises if the file is unreadable."""
    if not joblib_path.exists():
        raise FileNotFoundError(joblib_path)
    return joblib.load(joblib_path)


def load_or_train(
    golden_csv: Path,
    *,
    classifier_name: str,
    corpus_db_path: Path,
    goals_config: Any,
    output_dir: Path | None = None,
    force_retrain: bool = False,
    n_folds: int = 5,
    pca_dim: int = 100,
) -> TrainedClassifier:
    """Load if the cached model's sha matches the golden CSV; otherwise retrain.

    Failures during loading (corruption, schema drift) trigger a retrain
    rather than raising — keeps the daemon robust against stale artefacts.
    """
    output_dir = output_dir or DEFAULT_MODEL_DIR
    joblib_path = output_dir / f"{classifier_name}.joblib"
    current_sha = run_log.file_sha256(golden_csv, prefix_len=64) if golden_csv.exists() else ""
    if not force_retrain and joblib_path.exists() and current_sha:
        try:
            trained = load_trained(joblib_path)
            if trained.golden_csv_sha256 == current_sha:
                LOGGER.info(
                    "loaded classifier %s from %s (golden sha %s matches)",
                    classifier_name, joblib_path, current_sha[:12],
                )
                return trained
            LOGGER.info(
                "cached model golden_sha=%s differs from current=%s; retraining",
                trained.golden_csv_sha256[:12], current_sha[:12],
            )
        except Exception as exc:
            LOGGER.warning("failed to load cached model %s; retraining: %s", joblib_path, exc)

    return train_and_save(
        golden_csv,
        classifier_name=classifier_name,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
        output_dir=output_dir,
        n_folds=n_folds,
        pca_dim=pca_dim,
    )

