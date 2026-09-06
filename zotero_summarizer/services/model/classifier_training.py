"""Train a classifier on the golden set and persist it (one ZIP: joblib + metadata)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple

import numpy as np

from zotero_summarizer.services.model import classifier
from zotero_summarizer.services.model.classifier_artifact import TrainedClassifier
from zotero_summarizer.services.model.classifier_temporal import (
    _NO_DATE_SENTINEL,
    _row_days,
    _temporal_holdout_metrics,
)
from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import now_iso_z, settings
from zotero_summarizer.services.model.classifier_store import write_archive
from zotero_summarizer.services.model.classifier_inputs import load_training_inputs
from zotero_summarizer.services.model.golden_metrics import spearman_correlation
from zotero_summarizer.storage.corpus_types import CorpusAffinity

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class _TrainMatrix:
    """Aligned training arrays and the transient corpus context used by folds."""

    X: "np.ndarray"
    y: "np.ndarray"
    sample_weight: "np.ndarray"
    corpus_affinity: CorpusAffinity | None = None

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
    reference_year: int | None = None,
) -> _TrainMatrix:
    """Batched-embedding feature matrix for the labelled training set."""
    from zotero_summarizer.services.model.library_features import compute_library_features
    from zotero_summarizer.services.model.label_weights import compute_row_weights

    keys, titles, abstracts, y_cont, train_rows = data
    embed_cache, openalex_client, cold_start_policy = classifier._build_aux_providers(
        corpus_db_path, goals_config
    )
    n_train = len(keys)
    corpus = embed_cache.corpus_affinity(train_rows) if embed_cache is not None else None
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
            None, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
            cold_start_policy=cold_start_policy,
        )
        nearest, centroid, recent, drift, authors_overlap = compute_library_features(
            emb, library, candidate_row=train_rows[i],
        )
        X_train[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
            train_rows[i], t, a,
            reference_year=reference_year,
            corpus_affinity=affinity, prestige_score=prestige,
            nearest_kept_cosine=nearest, positive_centroid_cosine=centroid,
            recent_centroid_cosine=recent, topic_drift=drift,
            author_overlap_count=authors_overlap,
        )
        if progress_cb is not None and (i + 1) % 50 == 0:
            progress_cb(i + 1, n_train)
    if corpus is not None:
        X_train[:, classifier.EMBEDDING_DIM + 5] = corpus.scores()
    return _TrainMatrix(X_train, np.asarray(y_cont, dtype=np.float64), compute_row_weights(train_rows), corpus)


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
    lgbm_params: dict[str, Any] | None = None,
    pca_specter_dim: int | None = None,
) -> tuple[Any, Any]:
    """Fit the production regressor on the full training set; return (model, pca)."""
    pca_object = None
    fitted_model = None
    if classifier_name == "tabpfn":
        from sklearn.decomposition import PCA

        actual_dim = min(pca_dim, X_train.shape[0], classifier.EMBEDDING_DIM)
        pca_object = PCA(n_components=actual_dim, random_state=42)
        pca_object.fit(X_train[:, :classifier.EMBEDDING_DIM])
        # No persistent fitted_model — TabPFN re-fits per predict (in-context).
    elif classifier_name == "lightgbm":
        from zotero_summarizer.services.model.classifier_fit import _fit_lightgbm_regressor

        if pca_specter_dim is not None:
            # Sprint-3b: PCA reduction baked into the production model; store the
            # PCA object so predict-time transforms new items the same way.
            from sklearn.decomposition import PCA

            actual_dim = min(pca_specter_dim, X_train.shape[0], classifier.EMBEDDING_DIM)
            pca_object = PCA(n_components=actual_dim, random_state=42)
            pca_object.fit(X_train[:, :classifier.EMBEDDING_DIM])
            emb_red = pca_object.transform(X_train[:, :classifier.EMBEDDING_DIM])
            X_train_used = np.concatenate(
                [emb_red, X_train[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
        else:
            X_train_used = X_train

        fitted_model = _fit_lightgbm_regressor(
            X_train_used, y_train, lgbm_params=lgbm_params, sample_weight=sw_all,
        )
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
    train_rows: list[dict[str, str]],
    *,
    n_folds: int,
    pca_dim: int,
    lgbm_params: dict[str, Any] | None = None,
    pca_specter_dim: int | None = None,
) -> tuple["np.ndarray", float | None]:
    """Group OOF predictions with a train-fold positive library, plus Spearman."""
    from sklearn.model_selection import GroupKFold
    from zotero_summarizer.domain import paper_group_id
    from zotero_summarizer.services.model.library_features import recompute_engagement_columns

    preds_oof = np.zeros(matrix.n, dtype=np.float64)
    groups = [paper_group_id(row) for row in train_rows]
    kf = GroupKFold(n_splits=n_folds)
    for fold_idx, (tr, vl) in enumerate(kf.split(matrix.X, groups=groups), start=1):
        X_fold = recompute_engagement_columns(matrix.X, train_rows, tr, matrix.corpus_affinity)
        _, p_vl = classifier._fit_predict(
            classifier_name, X_fold[tr], matrix.y[tr], X_fold[vl],
            pca_dim=pca_dim, return_train_probs=False,
            objective="regression", sample_weight=matrix.sample_weight[tr],
            lgbm_params=lgbm_params, pca_specter_dim=pca_specter_dim,
        )
        preds_oof[vl] = p_vl
        LOGGER.info("train_and_save: fold %d/%d done", fold_idx, n_folds)
    return preds_oof, spearman_correlation(matrix.y, preds_oof)


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
    or has constant labels or predictions.
    """
    dated = np.asarray([_row_days(r) < _NO_DATE_SENTINEL for r in train_rows], dtype=bool)
    n_dated = int(dated.sum())
    return spearman_correlation(y_train[dated], preds_oof[dated]), n_dated


class _OofDiag(NamedTuple):
    """The four out-of-fold diagnostics that travel together into ``training_metadata``:
    aggregate Spearman ρ, the dated-subset ρ (+ its row count), and per-class metrics."""

    rho: float | None
    rho_verified: float | None
    n_verified: int
    metrics: dict[str, Any]


def _training_metadata(
    library: Any,
    temporal: dict[str, Any] | None,
    *,
    n_train: int,
    oof: _OofDiag,
    cal_diag: Any,
) -> dict[str, Any]:
    """The JSON-able ``training_metadata`` block stored on the artefact."""
    return {
        "n_train": n_train,
        "n_positive_library": library.n_rows,
        "objective": "regression",
        "oof_spearman": None if oof.rho is None else round(oof.rho, 4),
        # Honest split: oof_spearman above is the aggregate (inflated by ~72%
        # undated feed:* rows the gate trivially rejects); this is the SAME OOF
        # restricted to dated reading-decisions — the gate's real ranking ability.
        # None = subset too small / constant labels or predictions.
        "oof_spearman_verified": None if oof.rho_verified is None else round(oof.rho_verified, 4),
        "n_verified": oof.n_verified,
        # None = holdout too small / constant labels or predictions —
        # the ModelCard renders an em-dash then, never a fake number.
        "temporal_spearman": None if temporal is None else temporal["temporal_spearman"],
        "temporal_holdout_n": 0 if temporal is None else temporal["temporal_holdout_n"],
        "oof_metrics_vs_gold": oof.metrics,
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
        positive_library=library,
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

    Writes ``{output_dir}/{classifier_name}.zip`` (FAIR
    persistence).

    Phase 1.18 Step 2: when ``triage_db_path`` is provided, user verdicts
    in ``label_verdicts`` overlay derived ``gold_priority_final`` values
    before training. This is the closed loop — labels typed in the
    Annotate UI become ground truth for the next retrain.
    """
    if classifier_name not in ("tabpfn", "lightgbm", "logreg"):
        raise ValueError(f"unsupported classifier_name {classifier_name!r}")

    # 1. Load + filter training rows.
    inputs = load_training_inputs(
        golden_csv, classifier_name=classifier_name, corpus_db_path=corpus_db_path,
        goals_config=goals_config, n_folds=n_folds, pca_dim=pca_dim,
        triage_db_path=triage_db_path,
    )
    all_rows = inputs.rows
    fit_options = {"lgbm_params": inputs.lgbm_params, "pca_specter_dim": inputs.pca_specter_dim}

    # Hygiene cut: F5 (in_trash) + Sprint-1 tier filter (first_glance, meta).
    from zotero_summarizer.domain import paper_group_id
    from zotero_summarizer.services.model.library_features import load_positive_library_from_rows

    data = classifier._filter_train_rows(all_rows, n_folds=n_folds)
    keys, titles, abstracts, y_cont, train_rows = data

    # 2. Featurise (reuses classifier helpers — no authors/venue now).
    library = load_positive_library_from_rows(all_rows, corpus_db_path)
    n_train = len(y_cont)
    matrix = _featurize_training_matrix(
        data, library,
        corpus_db_path=corpus_db_path, goals_config=goals_config, progress_cb=progress_cb,
        reference_year=inputs.feature_reference_year,
    )
    X_train, y_train, sw_all = matrix.X, matrix.y, matrix.sample_weight

    # 3. K-fold OOF predictions → diagnostic Spearman ρ. No held-out
    #    threshold-tuning step any more (no thresholds to tune).
    gold_labels = [(r.get("gold_priority_final") or "").strip() for r in train_rows]
    groups = [paper_group_id(r) for r in train_rows]
    preds_oof, oof_rho = _oof_predictions(
        classifier_name, matrix, train_rows, n_folds=n_folds, pca_dim=pca_dim, **fit_options,
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
    # Compare with the predecessor artifact before save_trained replaces it.
    from zotero_summarizer.services.model.classifier_drift import log_label_drift

    gold_labels = [r.get("gold_priority_final") or "" for r in train_rows]
    output_dir = output_dir or settings().model_dir
    label_drift = log_label_drift(
        gold_labels, n_train, classifier_name=classifier_name,
        model_dir=output_dir,
    )

    # 3c. Forward-looking Spearman: train on the oldest 80%, score the newest
    # 20% — the number production actually delivers (the shuffled OOF above
    # overstates it; see the module comment on _temporal_holdout_metrics).
    temporal = _temporal_holdout_metrics(
        classifier_name, matrix, train_rows, groups, pca_dim=pca_dim, **fit_options,
    )

    # 4. Final fit on FULL training set.
    fitted_model, pca_object = _fit_final_model(
        classifier_name, X_train, y_train, sw_all, pca_dim=pca_dim, **fit_options,
    )

    # 5. Build the artefact.
    sha256 = inputs.csv_sha256
    trained = _build_artifact(
        matrix, library, fitted_model, pca_object, calibrator,
        classifier_name=classifier_name, sha256=sha256, pca_dim=pca_dim,
        metadata=_training_metadata(
            library, temporal,
            n_train=n_train,
            oof=_OofDiag(oof_rho, oof_rho_verified, n_verified, oof_metrics),
            cal_diag=cal_diag,
        ),
    )

    trained.training_metadata["training_input_sha256"] = inputs.sha256
    trained.training_metadata["feature_reference_year"] = inputs.feature_reference_year
    trained.training_metadata["fit_options"] = fit_options
    # 6. Persist artefacts.
    save_trained(trained, output_dir)
    # Retain typed training metrics for historical drift and offline audits.
    if runs_log_path is not None:
        run_log.append_run(runs_log_path, {
            "run_id": run_log.make_run_id(classifier_name),
            "timestamp": now_iso_z(),
            "git_commit": run_log.short_git_commit(),
            "classifier": classifier_name,
            "type": "train_artifact",
            "cv": {"n_rows": n_train, "auc": None, "metrics_vs_gold": oof_metrics},
            "label_drift": label_drift,
            "input_csv_sha256_prefix": sha256[:12],
        })
    LOGGER.info(
        "trained regressor %s saved to %s (n_train=%d, OOF Spearman ρ=%s, forward ρ=%s)",
        classifier_name, output_dir, n_train, "n/a" if oof_rho is None else f"{oof_rho:.3f}",
        "n/a" if temporal is None else f"{temporal['temporal_spearman']:.3f}",
    )
    return trained


def save_trained(trained: TrainedClassifier, output_dir: Path) -> Path:
    """Publish one ZIP containing both the model and its metadata atomically.

    Before overwriting, the prior model is snapshotted to a versioned history dir
    (``classifier_backup.snapshot_current``) so a retrain that later looks wrong has
    a rollback target. A failed snapshot RAISES — the invariant is *never overwrite
    the live model without a successful backup* (``--force`` is reversible).
    """
    from zotero_summarizer.services.model.classifier_backup import snapshot_current

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{trained.classifier_name}.zip"
    snapshot_current(output_dir, trained.classifier_name)
    write_archive(trained, path)
    return path
