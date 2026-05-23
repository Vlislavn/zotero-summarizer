"""Train a classifier on the golden set and persist it (joblib + JSON twin)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np

from zotero_summarizer.services.model import classifier
from zotero_summarizer.services.model.classifier_artifact import DEFAULT_MODEL_DIR, TrainedClassifier
from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import now_iso_z

LOGGER = logging.getLogger(__name__)


def train_and_save(
    golden_csv: Path,
    *,
    classifier_name: str,
    corpus_db_path: Path,
    goals_config: Any,
    output_dir: Path | None = None,
    n_folds: int = 5,
    pca_dim: int = 100,
    progress_cb: Callable[[int, int], None] | None = None,
    triage_db_path: Path | None = None,
) -> TrainedClassifier:
    """Train regressor on `gold_inferred_relevance` and persist to disk.

    Sprint-1 redesign (May 2026). The model now predicts a continuous
    relevance score in [1, 5]; the legacy isotonic-calibrator + Youden's-J
    + quantile-bin stack has been removed. Threshold mapping at predict
    time uses the constants in :mod:`zotero_summarizer.domain`.

    Writes ``{output_dir}/{classifier_name}.joblib`` + ``.json`` (FAIR
    persistence).

    Phase 1.18 Step 2: when ``triage_db_path`` is provided, user verdicts
    in ``label_verdicts`` overlay derived ``gold_priority_final`` values
    before training. This is the closed loop — labels typed in the
    Annotate UI become ground truth for the next retrain.
    """
    import csv as _csv
    from scipy.stats import spearmanr
    from sklearn.model_selection import GroupKFold
    from zotero_summarizer.services.golden import hybrid_gt

    if classifier_name not in ("tabpfn", "lightgbm", "logreg"):
        raise ValueError(f"unsupported classifier_name {classifier_name!r}")

    # 1. Load + filter training rows.
    with golden_csv.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(_csv.DictReader(f))
    if triage_db_path is not None:
        all_rows = hybrid_gt.apply_hybrid(all_rows, triage_db_path)

    # Hygiene cut: F5 (in_trash) + Sprint-1 tier filter (first_glance, meta).
    from zotero_summarizer.domain import is_training_eligible, paper_group_id

    keys, titles, abstracts, y_cont, train_rows = [], [], [], [], []
    for r in all_rows:
        if not is_training_eligible(r):
            continue
        gold = (r.get("gold_priority_final") or "").strip()
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        rel_str = (r.get("gold_inferred_relevance") or "").strip()
        if not gold or not title or not abstract or not rel_str:
            continue
        rel = float(rel_str)
        keys.append(r.get("item_key", ""))
        titles.append(title)
        abstracts.append(abstract)
        y_cont.append(rel)
        train_rows.append(r)
    if len(y_cont) < n_folds * 2:
        raise ValueError(
            f"need at least {n_folds * 2} labeled rows for {n_folds}-fold CV; got {len(y_cont)}"
        )

    # 2. Featurise (reuses classifier helpers — no authors/venue now).
    embed_cache, openalex_client = classifier._build_aux_providers(corpus_db_path, goals_config)
    from zotero_summarizer.services.model.library_features import (
        compute_library_features,
        load_positive_library_from_rows,
    )
    library = load_positive_library_from_rows(all_rows, corpus_db_path)
    n_train = len(y_cont)
    X_train = np.zeros((n_train, classifier.FEATURE_DIM), dtype=np.float32)
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        emb = classifier.get_or_compute_embedding(corpus_db_path, k, t, a)
        X_train[i, :classifier.EMBEDDING_DIM] = emb
        year_str = (train_rows[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (train_rows[i].get("doi") or "").strip()
        affinity, prestige = classifier._compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
        )
        authors_str = (train_rows[i].get("authors") or "").strip()
        nearest, centroid, recent, drift, authors_overlap = compute_library_features(
            emb, library, candidate_authors=authors_str, exclude_item_key=k,
        )
        X_train[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
            train_rows[i], t, a,
            corpus_affinity=affinity, prestige_score=prestige,
            nearest_kept_cosine=nearest, positive_centroid_cosine=centroid,
            recent_centroid_cosine=recent, topic_drift=drift,
            author_overlap_count=authors_overlap,
        )
        if progress_cb is not None and (i + 1) % 50 == 0:
            progress_cb(i + 1, n_train)
    y_train = np.asarray(y_cont, dtype=np.float64)

    # 3. K-fold OOF predictions → diagnostic Spearman ρ. No held-out
    #    threshold-tuning step any more (no thresholds to tune).
    from zotero_summarizer.services.model.label_weights import compute_row_weights
    sw_all = compute_row_weights(train_rows)

    preds_oof = np.zeros(n_train, dtype=np.float64)
    groups = [paper_group_id(r) for r in train_rows]
    kf = GroupKFold(n_splits=n_folds)
    for fold_idx, (tr, vl) in enumerate(kf.split(X_train, groups=groups), start=1):
        _, p_vl = classifier._fit_predict(
            classifier_name, X_train[tr], y_train[tr], X_train[vl],
            pca_dim=pca_dim, return_train_probs=False,
            objective="regression",
            sample_weight=sw_all[tr],
        )
        preds_oof[vl] = p_vl
        LOGGER.info("train_and_save: fold %d/%d done", fold_idx, n_folds)
    oof_rho = float(spearmanr(y_train, preds_oof).statistic) if n_train > 2 else 0.0

    # 4. Final fit on FULL training set.
    pca_object = None
    fitted_model = None
    if classifier_name == "tabpfn":
        from sklearn.decomposition import PCA

        actual_dim = min(pca_dim, n_train, classifier.EMBEDDING_DIM)
        pca_object = PCA(n_components=actual_dim, random_state=42)
        pca_object.fit(X_train[:, :classifier.EMBEDDING_DIM])
        # No persistent fitted_model — TabPFN re-fits per predict via in-context learning.
    elif classifier_name == "lightgbm":
        import lightgbm as lgb
        from zotero_summarizer.services.model.tune import load_tuned_params

        # Sprint-3c: pick up Optuna-tuned params if a `optuna-best-params.json`
        # is present in the cache dir. Missing file ⇒ empty overrides
        # ⇒ Sprint-1/2 default hyperparameters apply.
        tuned_params, tuned_pca = load_tuned_params()
        defaults = {
            "objective": "regression",
            "n_estimators": 200, "num_leaves": 15, "max_depth": 4,
            "learning_rate": 0.05, "min_child_samples": 10, "reg_lambda": 1.0,
            "verbose": -1, "random_state": 42, "n_jobs": 1, "num_threads": 1,
        }
        defaults.update(tuned_params)
        if tuned_pca is not None and tuned_pca > 0:
            # Sprint-3b: PCA reduction baked into the production model.
            # We materialise the reduced matrix once and store the PCA
            # object alongside the model so predict-time can transform new
            # items the same way.
            from sklearn.decomposition import PCA

            actual_dim = min(tuned_pca, X_train.shape[0], classifier.EMBEDDING_DIM)
            pca_object = PCA(n_components=actual_dim, random_state=42)
            pca_object.fit(X_train[:, :classifier.EMBEDDING_DIM])
            emb_red = pca_object.transform(X_train[:, :classifier.EMBEDDING_DIM])
            X_train_used = np.concatenate(
                [emb_red, X_train[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
        else:
            X_train_used = X_train

        fitted_model = lgb.LGBMRegressor(**defaults)
        fitted_model.fit(X_train_used, y_train, sample_weight=sw_all)
    elif classifier_name == "logreg":
        from sklearn.linear_model import Ridge

        fitted_model = Ridge(alpha=1.0, random_state=42)
        fitted_model.fit(X_train, y_train)
    else:
        raise ValueError(classifier_name)

    # 5. Build the artefact.
    sha256 = run_log.file_sha256(golden_csv, prefix_len=64)
    trained = TrainedClassifier(
        classifier_name=classifier_name,
        golden_csv_sha256=sha256,
        feature_dim=classifier.FEATURE_DIM,
        pca_dim=pca_dim,
        X_train=X_train,
        y_train=y_train,
        pca_object=pca_object,
        fitted_model=fitted_model,
        calibrator=None,
        t_keep=0.0,
        t_must=0.0,
        t_could=0.0,
        library_embeddings=library.embeddings if library.n_rows > 0 else None,
        library_centroid=library.centroid if library.n_rows > 0 else None,
        library_recent_centroid=library.recent_centroid if library.n_rows > 0 else None,
        library_authors_lower=library.authors_lower if library.n_rows > 0 else None,
        training_metadata={
            "n_train": n_train,
            "n_positive_library": library.n_rows,
            "objective": "regression",
            "oof_spearman": round(oof_rho, 4),
            "trained_at": now_iso_z(),
            "git_commit": run_log.short_git_commit(),
        },
    )

    # 6. Persist artefacts.
    output_dir = output_dir or DEFAULT_MODEL_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    save_trained(trained, output_dir)
    LOGGER.info(
        "trained regressor %s saved to %s (n_train=%d, OOF Spearman ρ=%.3f)",
        classifier_name, output_dir, n_train, oof_rho,
    )
    return trained


def save_trained(trained: TrainedClassifier, output_dir: Path) -> tuple[Path, Path]:
    """Write the joblib payload + JSON metadata mirror."""
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib_path = output_dir / f"{trained.classifier_name}.joblib"
    json_path = output_dir / f"{trained.classifier_name}.json"
    joblib.dump(trained, joblib_path)
    json_path.write_text(
        json.dumps(_serialisable_metadata(trained), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return joblib_path, json_path


def _serialisable_metadata(trained: TrainedClassifier) -> dict[str, Any]:
    """Plain-JSON projection of the artefact for inspection without joblib."""
    return {
        "classifier_name": trained.classifier_name,
        "golden_csv_sha256": trained.golden_csv_sha256,
        "feature_dim": trained.feature_dim,
        "pca_dim": trained.pca_dim,
        "thresholds": {
            "keep": round(trained.t_keep, 4),
            "must": round(trained.t_must, 4),
            "could": round(trained.t_could, 4),
        },
        **trained.training_metadata,
    }

