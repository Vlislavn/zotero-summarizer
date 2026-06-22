"""Train a classifier on the golden set and persist it (joblib + JSON twin)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np

from zotero_summarizer.services.model import classifier
from zotero_summarizer.services.model.classifier_artifact import DEFAULT_MODEL_DIR, TrainedClassifier
from zotero_summarizer.services.model.classifier_temporal import (
    _NO_DATE_SENTINEL,
    _row_days,
    _temporal_holdout_metrics,
)
from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import atomic_write, now_iso_z

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class _TrainMatrix:
    """The three per-row training arrays that always travel together: feature
    matrix ``X``, continuous target ``y``, and ``sample_weight`` (all length n)."""

    X: "np.ndarray"
    y: "np.ndarray"
    sample_weight: "np.ndarray"

    @property
    def n(self) -> int:
        return int(self.X.shape[0])


def _featurize_training_matrix(
    data: tuple,
    library: Any,
    *,
    corpus_db_path: Path,
    goals_config: Any,
    progress_cb: Callable[[int, int], None] | None = None,
) -> "np.ndarray":
    """Batched-embedding feature matrix for the labelled training set."""
    from zotero_summarizer.services.model.library_features import compute_library_features

    keys, titles, abstracts, _y_cont, train_rows = data
    embed_cache, openalex_client, cold_start_policy = classifier._build_aux_providers(
        corpus_db_path, goals_config
    )
    n_train = len(keys)
    X_train = np.zeros((n_train, classifier.FEATURE_DIM), dtype=np.float32)
    # Embed the whole training set in batched (GPU) passes, not one-at-a-time.
    embeddings = classifier.get_or_compute_embeddings_batch(
        corpus_db_path,
        [{"item_key": k, "title": t, "abstract": a} for k, t, a in zip(keys, titles, abstracts)],
    )
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        emb = embeddings[i]
        X_train[i, :classifier.EMBEDDING_DIM] = emb
        year_str = (train_rows[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (train_rows[i].get("doi") or "").strip()
        affinity, prestige = classifier._compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
            cold_start_policy=cold_start_policy,
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
    return X_train


def _oof_quality_metrics(train_rows: list[dict], preds_oof: "np.ndarray") -> dict[str, Any]:
    """Out-of-fold per-class precision/recall/F1 + confusion vs gold labels."""
    from zotero_summarizer.domain import score_to_priority
    from zotero_summarizer.services.model import golden_metrics as gm

    gold_labels = [(r.get("gold_priority_final") or "").strip() for r in train_rows]
    pred_labels = [score_to_priority(float(p)) for p in preds_oof]
    return {
        "total": len(gold_labels),
        "accuracy": round(gm.accuracy(gold_labels, pred_labels), 4),
        "per_class": {k: v.as_dict() for k, v in gm.compute_per_class(gold_labels, pred_labels).items()},
        "binary": gm.compute_binary(gold_labels, pred_labels).as_dict(),
        "confusion": gm.compute_confusion(gold_labels, pred_labels),
    }


def _fit_final_model(
    classifier_name: str,
    X_train: "np.ndarray",
    y_train: "np.ndarray",
    sw_all: "np.ndarray",
    *,
    pca_dim: int,
    n_train: int,
) -> tuple[Any, Any]:
    """Fit the production regressor on the full training set; return (model, pca)."""
    pca_object = None
    fitted_model = None
    if classifier_name == "tabpfn":
        from sklearn.decomposition import PCA

        actual_dim = min(pca_dim, n_train, classifier.EMBEDDING_DIM)
        pca_object = PCA(n_components=actual_dim, random_state=42)
        pca_object.fit(X_train[:, :classifier.EMBEDDING_DIM])
        # No persistent fitted_model — TabPFN re-fits per predict (in-context).
    elif classifier_name == "lightgbm":
        import lightgbm as lgb
        from zotero_summarizer.services.model.tune import load_tuned_params

        # Sprint-3c: pick up Optuna-tuned params if present; missing file ⇒
        # empty overrides ⇒ Sprint-1/2 default hyperparameters apply.
        tuned_params, tuned_pca = load_tuned_params()
        defaults = {
            "objective": "regression",
            "n_estimators": 200, "num_leaves": 15, "max_depth": 4,
            "learning_rate": 0.05, "min_child_samples": 10, "reg_lambda": 1.0,
            "verbose": -1, "random_state": 42, "n_jobs": 1, "num_threads": 1,
        }
        defaults.update(tuned_params)
        if tuned_pca is not None and tuned_pca > 0:
            # Sprint-3b: PCA reduction baked into the production model; store the
            # PCA object so predict-time transforms new items the same way.
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
    return fitted_model, pca_object


def _oof_predictions(
    classifier_name: str,
    matrix: _TrainMatrix,
    groups: list[str],
    *,
    n_folds: int,
    pca_dim: int,
) -> tuple["np.ndarray", float]:
    """K-fold out-of-fold predictions + diagnostic Spearman ρ (honest: every
    row is scored by a fold that never trained on it)."""
    from scipy.stats import spearmanr
    from sklearn.model_selection import GroupKFold

    preds_oof = np.zeros(matrix.n, dtype=np.float64)
    kf = GroupKFold(n_splits=n_folds)
    for fold_idx, (tr, vl) in enumerate(kf.split(matrix.X, groups=groups), start=1):
        _, p_vl = classifier._fit_predict(
            classifier_name, matrix.X[tr], matrix.y[tr], matrix.X[vl],
            pca_dim=pca_dim, return_train_probs=False,
            objective="regression", sample_weight=matrix.sample_weight[tr],
        )
        preds_oof[vl] = p_vl
        LOGGER.info("train_and_save: fold %d/%d done", fold_idx, n_folds)
    oof_rho = float(spearmanr(matrix.y, preds_oof).statistic) if matrix.n > 2 else 0.0
    return preds_oof, oof_rho


def _dated_oof_spearman(
    train_rows: list[dict[str, Any]], y_train: "np.ndarray", preds_oof: "np.ndarray"
) -> tuple[float | None, int]:
    """In-distribution OOF Spearman on the DATED (verified-engagement) rows only.

    The aggregate ``oof_spearman`` is dominated by undated feed:* rows (~72% of
    the set: mass auto-rejects + provisional adds), which the gate separates
    easily and which inflate the headline. This restricts the same OOF
    predictions to genuinely-dated Zotero-engagement rows — the reading decisions
    the gate is actually weak at ranking — so the card can report both honestly.
    Returns ``(rho | None, n_dated)``; ``None`` when the dated subset is too small
    or has a constant label.
    """
    from scipy.stats import spearmanr

    dated = np.asarray([_row_days(r) < _NO_DATE_SENTINEL for r in train_rows])
    n_dated = int(dated.sum())
    if n_dated <= 2 or len(set(y_train[dated].tolist())) < 2:
        return None, n_dated
    return float(spearmanr(y_train[dated], preds_oof[dated]).statistic), n_dated


def _training_metadata(
    library: Any,
    temporal: dict[str, Any] | None,
    *,
    n_train: int,
    oof_rho: float,
    oof_rho_verified: float | None,
    n_verified: int,
    oof_metrics: dict[str, Any],
    cal_diag: Any,
) -> dict[str, Any]:
    """The JSON-able ``training_metadata`` block stored on the artefact."""
    return {
        "n_train": n_train,
        "n_positive_library": library.n_rows,
        "objective": "regression",
        "oof_spearman": round(oof_rho, 4),
        # Honest split: oof_spearman above is the aggregate (inflated by ~72%
        # undated feed:* rows the gate trivially rejects); this is the SAME OOF
        # restricted to dated reading-decisions — the gate's real ranking ability.
        # None = subset too small / constant label (tiny fixtures).
        "oof_spearman_verified": None if oof_rho_verified is None else round(oof_rho_verified, 4),
        "n_verified": n_verified,
        # None = holdout too small / constant labels (tiny fixtures) —
        # the ModelCard renders an em-dash then, never a fake number.
        "temporal_spearman": None if temporal is None else temporal["temporal_spearman"],
        "temporal_holdout_n": 0 if temporal is None else temporal["temporal_holdout_n"],
        "oof_metrics_vs_gold": oof_metrics,
        "band_calibration": cal_diag,
        "trained_at": now_iso_z(),
        "git_commit": run_log.short_git_commit(),
    }


def _build_artifact(
    matrix: _TrainMatrix,
    library: Any,
    fitted_model: Any,
    pca_object: Any,
    calibrator: Any,
    *,
    classifier_name: str,
    sha256: str,
    pca_dim: int,
    metadata: dict[str, Any],
) -> TrainedClassifier:
    """Assemble the persisted artefact from the already-trained pieces."""
    return TrainedClassifier(
        classifier_name=classifier_name,
        golden_csv_sha256=sha256,
        feature_dim=classifier.FEATURE_DIM,
        pca_dim=pca_dim,
        X_train=matrix.X,
        y_train=matrix.y,
        pca_object=pca_object,
        fitted_model=fitted_model,
        calibrator=calibrator,
        t_keep=0.0,
        t_must=0.0,
        t_could=0.0,
        library_embeddings=library.embeddings if library.n_rows > 0 else None,
        library_centroid=library.centroid if library.n_rows > 0 else None,
        library_recent_centroid=library.recent_centroid if library.n_rows > 0 else None,
        library_authors_lower=library.authors_lower if library.n_rows > 0 else None,
        training_metadata=metadata,
    )


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
    runs_log_path: Path | None = None,
) -> TrainedClassifier:
    """Train regressor on `gold_inferred_relevance` and persist to disk.

    Sprint-1 redesign (May 2026). The model predicts a continuous relevance
    score in [1, 5]; the legacy Youden's-J + quantile-bin stack was removed and
    band thresholds are the constants in :mod:`zotero_summarizer.domain`. A
    lightweight, OOF-fit MONOTONE band calibrator (``band_calibration``) is then
    layered on the BAND ONLY — it makes the compressed top reachable
    (``must_read`` recall) without touching the scores used for ranking, and is
    kept only when it improves OOF must+should F1.

    Writes ``{output_dir}/{classifier_name}.joblib`` + ``.json`` (FAIR
    persistence).

    Phase 1.18 Step 2: when ``triage_db_path`` is provided, user verdicts
    in ``label_verdicts`` overlay derived ``gold_priority_final`` values
    before training. This is the closed loop — labels typed in the
    Annotate UI become ground truth for the next retrain.
    """
    import csv as _csv
    from zotero_summarizer.services.golden import hybrid_gt

    if classifier_name not in ("tabpfn", "lightgbm", "logreg"):
        raise ValueError(f"unsupported classifier_name {classifier_name!r}")

    # 1. Load + filter training rows.
    with golden_csv.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(_csv.DictReader(f))
    if triage_db_path is not None:
        all_rows = hybrid_gt.apply_hybrid(all_rows, triage_db_path)

    # Hygiene cut: F5 (in_trash) + Sprint-1 tier filter (first_glance, meta).
    from zotero_summarizer.domain import paper_group_id
    from zotero_summarizer.services.model.library_features import load_positive_library_from_rows

    data = classifier._filter_train_rows(all_rows, n_folds=n_folds)
    keys, titles, abstracts, y_cont, train_rows = data

    # 2. Featurise (reuses classifier helpers — no authors/venue now).
    library = load_positive_library_from_rows(all_rows, corpus_db_path)
    n_train = len(y_cont)
    X_train = _featurize_training_matrix(
        data, library,
        corpus_db_path=corpus_db_path, goals_config=goals_config, progress_cb=progress_cb,
    )
    y_train = np.asarray(y_cont, dtype=np.float64)

    # 3. K-fold OOF predictions → diagnostic Spearman ρ. No held-out
    #    threshold-tuning step any more (no thresholds to tune).
    from zotero_summarizer.services.model.label_weights import compute_row_weights
    sw_all = compute_row_weights(train_rows)
    gold_labels = [(r.get("gold_priority_final") or "").strip() for r in train_rows]
    groups = [paper_group_id(r) for r in train_rows]
    matrix = _TrainMatrix(X_train, y_train, sw_all)
    preds_oof, oof_rho = _oof_predictions(
        classifier_name, matrix, groups, n_folds=n_folds, pca_dim=pca_dim,
    )
    oof_rho_verified, n_verified = _dated_oof_spearman(train_rows, y_train, preds_oof)

    # 3b. Top-band calibration: fit a MONOTONE raw→relevance map on the OOF
    # predictions so the compressed top is reachable (must_read recall collapses
    # otherwise). Monotone ⇒ ranking untouched; applied to the BAND only. Kept
    # only if it lifts OOF must+should F1 (else identity), so it can never make
    # the banding worse and won't manufacture false must_reads when great papers
    # are genuinely scarce.
    from zotero_summarizer.services.model import band_calibration

    calibrator, cal_diag = band_calibration.fit_band_calibrator(preds_oof, y_train, gold_labels)
    eff_oof = band_calibration.apply_band_calibration(calibrator, preds_oof)

    # Out-of-fold per-class quality (honest — predictions never saw their own
    # fold), on the EFFECTIVE (post-calibration) bins the shipped gate will assign.
    oof_metrics = _oof_quality_metrics(train_rows, eff_oof)

    # 3c. Forward-looking Spearman: train on the oldest 80%, score the newest
    # 20% — the number production actually delivers (the shuffled OOF above
    # overstates it; see the module comment on _temporal_holdout_metrics).
    temporal = _temporal_holdout_metrics(
        classifier_name, matrix, train_rows, groups, pca_dim=pca_dim,
    )

    # 4. Final fit on FULL training set.
    fitted_model, pca_object = _fit_final_model(
        classifier_name, X_train, y_train, sw_all, pca_dim=pca_dim, n_train=n_train,
    )

    # 5. Build the artefact.
    sha256 = run_log.file_sha256(golden_csv, prefix_len=64)
    trained = _build_artifact(
        matrix, library, fitted_model, pca_object, calibrator,
        classifier_name=classifier_name, sha256=sha256, pca_dim=pca_dim,
        metadata=_training_metadata(
            library, temporal,
            n_train=n_train, oof_rho=oof_rho,
            oof_rho_verified=oof_rho_verified, n_verified=n_verified,
            oof_metrics=oof_metrics, cal_diag=cal_diag,
        ),
    )

    # 6. Persist artefacts.
    output_dir = output_dir or DEFAULT_MODEL_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    save_trained(trained, output_dir)
    # FAIR run-log entry so the Settings ModelCard renders the OOF per-class
    # table after a retrain (it reads runlog.cv.metrics_vs_gold.per_class). The
    # path is provided by the API retrain worker; CLI/gate callers pass None.
    if runs_log_path is not None:
        run_log.append_run(runs_log_path, {
            "run_id": run_log.make_run_id(classifier_name),
            "timestamp": now_iso_z(),
            "git_commit": run_log.short_git_commit(),
            "classifier": classifier_name,
            "type": "train_artifact",
            "cv": {"n_rows": n_train, "auc": None, "metrics_vs_gold": oof_metrics},
            "input_csv_sha256_prefix": sha256[:12],
        })
    LOGGER.info(
        "trained regressor %s saved to %s (n_train=%d, OOF Spearman ρ=%.3f, forward ρ=%s)",
        classifier_name, output_dir, n_train, oof_rho,
        "n/a" if temporal is None else f"{temporal['temporal_spearman']:.3f}",
    )
    return trained


def save_trained(trained: TrainedClassifier, output_dir: Path) -> tuple[Path, Path]:
    """Write the joblib payload + JSON metadata mirror (atomically)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib_path = output_dir / f"{trained.classifier_name}.joblib"
    json_path = output_dir / f"{trained.classifier_name}.json"
    # tmp + os.replace: a crash mid-dump must not leave a truncated .joblib that
    # then fails to unpickle and bricks the gate on the next startup.
    atomic_write(joblib_path, lambda target: joblib.dump(trained, target))
    atomic_write(
        json_path,
        lambda target: target.write_text(
            json.dumps(_serialisable_metadata(trained), indent=2, ensure_ascii=False),
            encoding="utf-8",
        ),
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

