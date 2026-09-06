"""Load cached classifiers + lazy retrain for the daemon gate.

The artefact lives in ``classifier_artifact``; training in ``classifier_training``.
This module owns the on-disk location, load, and load-or-retrain logic, and
re-exports the artefact/training API for back-compat (``classifier_persistence.X``)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import settings
from zotero_summarizer.services.model.classifier_inputs import load_training_inputs
from zotero_summarizer.services.model.classifier_store import load_trained, model_path  # noqa: F401
from zotero_summarizer.services.model.classifier_artifact import (  # noqa: F401  (re-export)
    TrainedClassifier,
    _EXTRA_FEATURE_NAMES,
    _format_shap,
)
from zotero_summarizer.services.model.classifier_training import (  # noqa: F401  (re-export)
    save_trained,
    train_and_save,
)

LOGGER = logging.getLogger(__name__)


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
    triage_db_path: Path | None = None,
) -> TrainedClassifier:
    """Load if the recorded training-input identity matches; otherwise train.

    Loading errors propagate; a broken archive must not be silently replaced.

    ``triage_db_path`` is threaded into the retrain so the daemon path applies
    the same ``hybrid_gt`` verdict overlay as the UI ``/admin/retrain`` route —
    without it the two paths trained on DIFFERENT labels (raw CSV vs. overlaid),
    e.g. a daemon retrain would not apply the unchecked-add downgrade.
    """
    output_dir = output_dir or settings().model_dir
    path = model_path(output_dir, classifier_name)
    if not force_retrain and path.exists():
        trained = load_trained(path)
        inputs = load_training_inputs(
            golden_csv, classifier_name=classifier_name, corpus_db_path=corpus_db_path,
            goals_config=goals_config, n_folds=n_folds, pca_dim=pca_dim,
            triage_db_path=triage_db_path,
        )
        if trained.training_metadata.get("training_input_sha256") == inputs.sha256:
            LOGGER.info("loaded classifier %s from %s", classifier_name, path)
            return trained

    return train_and_save(
        golden_csv,
        classifier_name=classifier_name,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
        output_dir=output_dir,
        n_folds=n_folds,
        pca_dim=pca_dim,
        triage_db_path=triage_db_path,
    )
