"""Sprint-3c (May 2026) — Optuna hyperparameter tuning for the LightGBM regressor.

Searches the (LightGBM × PCA-dim) joint space using paper-group folds and
row weights, matching deployed OOF fitting. Objective is the median per-fold
Spearman ρ on `gold_inferred_relevance`. Results land in
``data/optuna-best-params.json`` in the selected project and are picked up by
:func:`classifier_persistence.train_and_save` on the next ``--force`` retrain.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import optuna

from zotero_summarizer.services._common import settings

LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class TuneResult:
    best_params: dict[str, Any]
    best_pca_specter_dim: int
    best_value: float
    n_trials_completed: int


def _objective_factory(
    rows: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    goals_config: Any,
    n_folds: int,
):
    """Build the Optuna objective closure with featurization done ONCE.

    Featurizing 524 rows takes ~10 min and is independent of LightGBM
    hyperparameters; we hoist it out of the trial loop.
    """
    from scipy.stats import spearmanr
    from sklearn.model_selection import GroupKFold

    from zotero_summarizer.domain import paper_group_id
    from zotero_summarizer.services.model import classifier
    from zotero_summarizer.services.model.eval_baseline._featurize import featurize_golden
    from zotero_summarizer.services.model.library_features import recompute_engagement_columns

    feat = featurize_golden(
        rows,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
    )
    LOGGER.info("tune: featurised n=%d rows for Optuna", feat.X.shape[0])
    groups = np.asarray([paper_group_id(row) for row in feat.selected_rows])
    folds = list(GroupKFold(n_splits=n_folds).split(feat.X, groups=groups))

    def objective(trial: optuna.Trial) -> float:
        pca_dim = trial.suggest_categorical("pca_specter_dim", [None, 128, 256, 384])
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 100, 800),
            "num_leaves": trial.suggest_int("num_leaves", 7, 63),
            "max_depth": trial.suggest_int("max_depth", 3, 10),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 3, 30),
            "reg_lambda": trial.suggest_float("reg_lambda", 0.1, 10.0, log=True),
            "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 5.0),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        }
        rhos: list[float] = []
        for tr, vl in folds:
            # ponytail: rebuild P per trial to retain one matrix; cache only if profiling warrants.
            X_fold = recompute_engagement_columns(feat.X, feat.selected_rows, tr, feat.corpus_affinity)
            _, p_vl = classifier._fit_predict(
                "lightgbm", X_fold[tr], feat.y_continuous[tr], X_fold[vl],
                return_train_probs=False,
                objective="regression",
                pca_specter_dim=pca_dim,
                lgbm_params=params,
                sample_weight=feat.sample_weights[tr],
            )
            p_vl = np.clip(np.asarray(p_vl, dtype=np.float64), 1.0, 5.0)
            rho = spearmanr(feat.y_continuous[vl], p_vl).statistic
            if rho is None or np.isnan(rho):
                continue
            rhos.append(float(rho))
        if not rhos:
            raise RuntimeError("zero non-degenerate folds in Optuna trial")
        return float(np.median(rhos))

    return objective


def tune_lightgbm(
    rows: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    goals_config: Any,
    n_trials: int = 50,
    n_folds: int = 5,
    seed: int = 42,
    output_path: Path | None = None,
) -> TuneResult:
    """Run an Optuna sweep over LightGBM regression hyperparameters.

    Returns the best trial and writes a JSON dump at
    ``output_path`` (defaults to ``Settings.tuned_params_path``). The
    train pipeline auto-loads this file when present.
    """
    output_path = output_path or settings().tuned_params_path
    objective = _objective_factory(
        rows,
        corpus_db_path=corpus_db_path,
        goals_config=goals_config,
        n_folds=n_folds,
    )
    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best = study.best_trial
    pca_dim = best.params.get("pca_specter_dim")
    lgbm_params = {k: v for k, v in best.params.items() if k != "pca_specter_dim"}
    result = TuneResult(
        best_params=lgbm_params,
        best_pca_specter_dim=pca_dim if pca_dim is not None else 0,
        best_value=float(best.value),
        n_trials_completed=len(study.trials),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({
        "best_value_spearman_median": result.best_value,
        "n_trials": result.n_trials_completed,
        "pca_specter_dim": pca_dim,
        "lgbm_params": lgbm_params,
    }, indent=2), encoding="utf-8")
    LOGGER.info(
        "tune: best median Spearman ρ = %.4f over %d trials, saved to %s",
        result.best_value, result.n_trials_completed, output_path,
    )
    return result


def load_tuned_params(
    path: Path | None = None,
) -> tuple[dict[str, Any], int | None]:
    """Return ``(lgbm_params, pca_specter_dim)`` from disk, or empty tuple.

    The pca_specter_dim is None if the JSON has it as None or 0.
    """
    path = path or settings().tuned_params_path
    if not path.exists():
        return {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    pca = payload.get("pca_specter_dim")
    if pca == 0:
        pca = None
    return dict(payload.get("lgbm_params") or {}), pca
