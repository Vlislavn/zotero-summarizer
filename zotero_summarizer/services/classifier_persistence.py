"""Persist trained classifiers for the hybrid daemon gate (Phase 1.13).

The :func:`zotero_summarizer.services.classifier.cross_validate` and
:func:`predict_new_items` paths re-do CV + calibration + threshold tuning on
every invocation. That's fine for one-shot evaluation runs but wasteful for a
daemon that wants cheap per-tick predictions.

This module wraps the existing pipeline as a serialisable
:class:`TrainedClassifier`. ``train_and_save`` runs the full pipeline once,
stores all the artefacts to disk (training matrix, calibrator, thresholds,
PCA basis for TabPFN, fitted sklearn model where applicable). ``load_or_train``
verifies the cached model's ``golden_csv_sha256`` against the live golden CSV
— if they agree, deserialise; otherwise retrain.

Storage layout under ``output_dir`` (default
``~/.cache/zotero-summarizer/models``):

  tabpfn.joblib   — pickled TrainedClassifier (full artefact)
  tabpfn.json     — metadata mirror, human-readable

The JSON twin lets you ``cat`` a model file and see its provenance (training
size, OOF AUC, golden sha, git commit, timestamp) without unpickling.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np

from zotero_summarizer.services import classifier, run_log


LOGGER = logging.getLogger(__name__)


DEFAULT_MODEL_DIR = Path.home() / ".cache" / "zotero-summarizer" / "models"


# ---------------------------------------------------------------------------
# SHAP formatting
# ---------------------------------------------------------------------------


# Human-readable names for the 7 tabular extras (order must match
# ``_extra_features`` in classifier.py).
_EXTRA_FEATURE_NAMES = (
    "has_doi", "has_venue", "year_recency",
    "title_log_len", "abstract_log_len",
    "corpus_affinity", "prestige_score",
)


def _format_shap(row: np.ndarray) -> list[dict[str, float]]:
    """Collapse a 776-dim TreeSHAP row into a UI-friendly summary.

    LightGBM's ``predict_proba(X, pred_contrib=True)`` returns a matrix of
    shape ``(n_samples, n_features + 1)`` — the last column is the bias
    (expected_value). We bucket the 768 SPECTER2 dimensions into one
    ``semantic_match_specter2`` contribution (their sum), keep the 7 extras
    individually named, surface the bias separately, and return the list
    sorted by ``|contribution|`` descending.
    """
    n_extras = len(_EXTRA_FEATURE_NAMES)
    expected_total = classifier.EMBEDDING_DIM + n_extras + 1   # +1 for bias
    if row.shape[0] != expected_total:
        raise ValueError(
            f"_format_shap expected length {expected_total}, got {row.shape[0]}"
        )
    semantic = float(row[:classifier.EMBEDDING_DIM].sum())
    bias = float(row[-1])
    out: list[dict[str, float]] = [
        {"feature": "semantic_match_specter2", "contribution": semantic},
        {"feature": "bias", "contribution": bias},
    ]
    for idx, name in enumerate(_EXTRA_FEATURE_NAMES):
        out.append({
            "feature": name,
            "contribution": float(row[classifier.EMBEDDING_DIM + idx]),
        })
    out.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class TrainedClassifier:
    """A serialisable, ready-to-predict classifier for the hybrid gate.

    For LightGBM/LogReg we store the fitted sklearn model in ``model_payload``
    and predict directly. For TabPFN — which does in-context learning — we
    store ``(X_train, y_train, pca_object)`` and re-fit at predict time
    (cheap-ish on the fitted PCA basis).
    """

    classifier_name: str           # "tabpfn" | "lightgbm" | "logreg"
    golden_csv_sha256: str          # full sha (not prefix) for invalidation
    feature_dim: int                # 775 = 768 SPECTER2 + 7 extras
    pca_dim: int                    # only meaningful for TabPFN
    # Training payload — what we need to predict
    X_train: np.ndarray             # (n_train, feature_dim) float32
    y_train: np.ndarray             # (n_train,) int32
    pca_object: Any = None          # sklearn PCA, only set for TabPFN
    fitted_model: Any = None        # sklearn LGBMClassifier / LogisticRegression
    calibrator: Any = None          # IsotonicRegression or LogisticRegression
    # Thresholds learnt during CV
    t_keep: float = 0.5
    t_must: float = 0.75
    t_could: float = 0.25
    # Training metadata
    training_metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ predict

    def predict(
        self,
        items: list[dict[str, str]],
        *,
        corpus_db_path: Path,
        goals_config: Any,
        progress_cb: Callable[[int, int], None] | None = None,
        return_shap: bool = False,
    ) -> list[classifier.FeedPrediction]:
        """Featurise + predict a batch of items.

        Returns a parallel list of FeedPrediction objects (same shape as
        ``classifier.predict_new_items``). When ``return_shap=True`` and the
        underlying model is LightGBM, ``pred.shap_contribs`` is populated via
        TreeSHAP (``predict_proba(X, pred_contrib=True)``); ``pred.aux_context``
        is populated for all model types.
        """
        valid = [
            it for it in items
            if (it.get("title") or "").strip() and (it.get("abstract") or "").strip()
        ]
        if not valid:
            return []

        # 1. Featurise — same as the prediction path in predict_new_items.
        embed_cache, openalex_client = classifier._build_aux_providers(
            corpus_db_path, goals_config,
        )
        X_new = np.zeros((len(valid), self.feature_dim), dtype=np.float32)
        aux_contexts: list[dict[str, float]] = []
        for i, it in enumerate(valid):
            title = (it.get("title") or "").strip()
            abstract = (it.get("abstract") or "").strip()
            authors = (it.get("authors") or "").strip()
            venue = (it.get("publication_title") or it.get("venue") or "").strip()
            cache_key = str(it.get("item_key") or it.get("item_id") or f"item_{i}")
            X_new[i, :classifier.EMBEDDING_DIM] = classifier.get_or_compute_embedding(
                corpus_db_path, cache_key, title, abstract,
                authors=authors, venue=venue,
            )
            doi = (it.get("doi") or "").strip()
            year_str = (it.get("publication_date") or it.get("year") or "")[:4]
            year_i = int(year_str) if year_str.isdigit() else None
            affinity, prestige, ctx = classifier._compute_aux_with_context(
                embed_cache, openalex_client,
                title=title, abstract=abstract, doi=doi, year=year_i,
            )
            aux_contexts.append(ctx)
            feature_row = {"doi": doi, "venue": venue, "year": year_str}
            X_new[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
                feature_row, title, abstract,
                corpus_affinity=affinity, prestige_score=prestige,
            )
            if progress_cb is not None and (i + 1) % 10 == 0:
                progress_cb(i + 1, len(valid))

        # 2. Raw predict — uses pre-fitted sklearn model or re-fits TabPFN.
        p_raw = self._raw_predict(X_new)

        # 2b. SHAP (optional, LightGBM only — TreeSHAP via pred_contrib=True).
        shap_per_item: list[list[dict[str, float]] | None] = [None] * len(valid)
        if return_shap and self.classifier_name == "lightgbm" and self.fitted_model is not None:
            contribs = self.fitted_model.predict_proba(X_new, pred_contrib=True)
            for i in range(len(valid)):
                shap_per_item[i] = _format_shap(contribs[i])

        # 3. Calibrate + threshold.
        p_cal = classifier._apply_calibrator(self.calibrator, p_raw)
        predictions: list[classifier.FeedPrediction] = []
        for i, (it, raw, cal) in enumerate(zip(valid, p_raw, p_cal)):
            title = (it.get("title") or "").strip()
            abstract = (it.get("abstract") or "").strip()
            preview = abstract[:200].rstrip()
            if len(abstract) > 200:
                preview += "…"
            priority = classifier._prob_to_priority_adaptive(
                float(cal),
                t_keep=self.t_keep, t_must=self.t_must, t_could=self.t_could,
            )
            predictions.append(classifier.FeedPrediction(
                item_key=str(it.get("item_key") or it.get("item_id") or ""),
                title=title,
                authors=(it.get("authors") or "").strip(),
                venue=(it.get("publication_title") or it.get("venue") or "").strip(),
                doi=(it.get("doi") or "").strip(),
                abstract_preview=preview,
                raw_score=float(raw),
                calibrated_score=float(cal),
                predicted_priority=priority,
                shap_contribs=shap_per_item[i],
                aux_context=aux_contexts[i],
            ))
        return predictions

    def _raw_predict(self, X_new: np.ndarray) -> np.ndarray:
        """Model-specific predict, returning a 1-D array of P(positive)."""
        if self.classifier_name == "tabpfn":
            from tabpfn import TabPFNClassifier

            X_train_red = self.pca_object.transform(self.X_train[:, :classifier.EMBEDDING_DIM])
            X_new_red = self.pca_object.transform(X_new[:, :classifier.EMBEDDING_DIM])
            X_train_full = np.concatenate(
                [X_train_red, self.X_train[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
            X_new_full = np.concatenate(
                [X_new_red, X_new[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
            clf = TabPFNClassifier(
                n_estimators=8, device="auto",
                ignore_pretraining_limits=False, random_state=42,
            )
            clf.fit(X_train_full, self.y_train)
            return clf.predict_proba(X_new_full)[:, 1]
        if self.classifier_name in ("lightgbm", "logreg"):
            assert self.fitted_model is not None, (
                f"fitted_model missing for {self.classifier_name}; bug?"
            )
            return self.fitted_model.predict_proba(X_new)[:, 1]
        raise ValueError(f"unknown classifier_name {self.classifier_name!r}")


# ---------------------------------------------------------------------------
# Train + save
# ---------------------------------------------------------------------------


def train_and_save(
    golden_csv: Path,
    *,
    classifier_name: str,
    corpus_db_path: Path,
    goals_config: Any,
    output_dir: Path | None = None,
    n_folds: int = 5,
    pca_dim: int = 100,
    calibration: str = "isotonic",
    threshold_strategy: str = "youden",
    progress_cb: Callable[[int, int], None] | None = None,
) -> TrainedClassifier:
    """Full training pipeline: CV → calibrate → final fit → save.

    Writes ``{output_dir}/{classifier_name}.joblib`` + ``.json`` and appends a
    line to ``classifier-runs.jsonl`` so the training itself is recorded under
    the existing FAIR-persistence workflow.
    """
    import csv as _csv
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    if classifier_name not in ("tabpfn", "lightgbm", "logreg"):
        raise ValueError(f"unsupported classifier_name {classifier_name!r}")

    # 1. Load + filter training rows.
    with golden_csv.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(_csv.DictReader(f))

    keys, titles, abstracts, labels, train_rows = [], [], [], [], []
    for r in all_rows:
        gold = (r.get("gold_priority_final") or "").strip()
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        if not gold or not title or not abstract:
            continue
        keys.append(r.get("item_key", ""))
        titles.append(title)
        abstracts.append(abstract)
        labels.append(1 if gold in classifier.POSITIVE_CLASSES else 0)
        train_rows.append(r)
    if len(labels) < n_folds * 2:
        raise ValueError(
            f"need at least {n_folds * 2} labeled rows for {n_folds}-fold CV; got {len(labels)}"
        )

    # 2. Featurise (reuses the same builders as cross_validate).
    embed_cache, openalex_client = classifier._build_aux_providers(corpus_db_path, goals_config)
    n_train = len(labels)
    X_train = np.zeros((n_train, classifier.FEATURE_DIM), dtype=np.float32)
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        authors = (train_rows[i].get("authors") or "").strip()
        venue = (train_rows[i].get("venue") or "").strip()
        X_train[i, :classifier.EMBEDDING_DIM] = classifier.get_or_compute_embedding(
            corpus_db_path, k, t, a, authors=authors, venue=venue,
        )
        year_str = (train_rows[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (train_rows[i].get("doi") or "").strip()
        affinity, prestige = classifier._compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
        )
        X_train[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
            train_rows[i], t, a,
            corpus_affinity=affinity, prestige_score=prestige,
        )
        if progress_cb is not None and (i + 1) % 50 == 0:
            progress_cb(i + 1, n_train)
    y_train = np.asarray(labels, dtype=np.int32)

    # 3. Phase 1.15 (1.2): 80/20 held-out split for threshold tuning.
    #    The CV pool produces OOF predictions used to fit the final calibrator;
    #    thresholds (t_keep, t_must, t_could) are then picked on a separate
    #    held-out slice the calibrator never saw. This eliminates the modest
    #    data leak from picking Youden's J on the same OOF probs the calibrator
    #    was fit on.
    from sklearn.model_selection import train_test_split

    cv_idx, holdout_idx = train_test_split(
        np.arange(n_train),
        test_size=0.20,
        stratify=y_train,
        random_state=42,
    )
    X_cv, y_cv = X_train[cv_idx], y_train[cv_idx]
    X_holdout, y_holdout = X_train[holdout_idx], y_train[holdout_idx]

    # 3a. CV on the 80% pool → OOF probs for calibrator training + AUC.
    probs_oof_cv = np.zeros(len(cv_idx), dtype=np.float64)
    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    for fold_idx, (tr, vl) in enumerate(skf.split(X_cv, y_cv), start=1):
        p_tr_raw, p_vl_raw = classifier._fit_predict(
            classifier_name, X_cv[tr], y_cv[tr], X_cv[vl],
            pca_dim=pca_dim, return_train_probs=True,
        )
        cal = classifier._fit_calibrator(p_tr_raw, y_cv[tr], method=calibration)
        probs_oof_cv[vl] = classifier._apply_calibrator(cal, p_vl_raw)
        LOGGER.info("train_and_save: fold %d/%d done", fold_idx, n_folds)

    auc = float(roc_auc_score(y_cv, probs_oof_cv)) if len(set(y_cv)) > 1 else 0.0

    # 3b. Final calibrator fit on the CV pool's OOF probs.
    final_calibrator = classifier._fit_calibrator(probs_oof_cv, y_cv, method=calibration)

    # 3c. Pick thresholds on the held-out 20% (truly unseen by the calibrator).
    #     Fit one model on the CV pool, predict on held-out, calibrate.
    if len(set(y_holdout)) < 2:
        raise ValueError(
            "held-out slice is single-class after stratified 20% split; "
            "this should be impossible at n_train >= n_folds * 2 — check "
            "your golden CSV for class imbalance."
        )
    _, p_holdout_raw = classifier._fit_predict(
        classifier_name, X_cv, y_cv, X_holdout,
        pca_dim=pca_dim, return_train_probs=False,
    )
    probs_holdout = classifier._apply_calibrator(final_calibrator, p_holdout_raw)
    t_keep = classifier._find_optimal_threshold(
        y_holdout, probs_holdout, strategy=threshold_strategy,
    )
    must_t, could_t = classifier._adaptive_4class_cutoffs(probs_holdout, t_keep)

    # 4. Final fit on FULL training set.
    pca_object = None
    fitted_model = None
    if classifier_name == "tabpfn":
        from sklearn.decomposition import PCA

        actual_dim = min(pca_dim, n_train, classifier.EMBEDDING_DIM)
        pca_object = PCA(n_components=actual_dim, random_state=42)
        pca_object.fit(X_train[:, :classifier.EMBEDDING_DIM])
        # No persistent fitted_model — TabPFN re-fits per predict via in-context learning.
    else:
        # Fit the sklearn model once on full training data. Predict-time is fast.
        # We call _fit_predict with a single-row X_val (dummy) just to reuse its
        # model-construction logic, but build our own fitted clf below.
        if classifier_name == "lightgbm":
            import lightgbm as lgb

            # n_jobs=1 + num_threads=1: avoid libomp init crash on macOS when
            # the Booster is later unpickled alongside torch (SPECTER2). The
            # gate runs on tiny batches (≤30) so single-thread predict is plenty.
            fitted_model = lgb.LGBMClassifier(
                n_estimators=200, num_leaves=15, max_depth=4,
                learning_rate=0.05, min_child_samples=10, reg_lambda=1.0,
                class_weight="balanced", verbose=-1, random_state=42,
                n_jobs=1, num_threads=1,
            )
        elif classifier_name == "logreg":
            from sklearn.linear_model import LogisticRegression

            fitted_model = LogisticRegression(
                C=1.0, class_weight="balanced", max_iter=1000, solver="lbfgs",
            )
        else:  # pragma: no cover
            raise ValueError(classifier_name)
        fitted_model.fit(X_train, y_train)

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
        calibrator=final_calibrator,
        t_keep=float(t_keep),
        t_must=float(must_t),
        t_could=float(could_t),
        training_metadata={
            "n_train": n_train,
            "n_positive": int(y_train.sum()),
            "oof_auc": round(auc, 4),
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "git_commit": run_log.short_git_commit(),
        },
    )

    # 6. Persist artefacts.
    output_dir = output_dir or DEFAULT_MODEL_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    save_trained(trained, output_dir)
    LOGGER.info(
        "trained classifier %s saved to %s (n_train=%d, AUC=%.3f)",
        classifier_name, output_dir, n_train, auc,
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


# ---------------------------------------------------------------------------
# Load + lazy retrain
# ---------------------------------------------------------------------------


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
