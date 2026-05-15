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


# Human-readable names for the 12 tabular extras (order must match
# ``_extra_features`` in classifier.py).
_EXTRA_FEATURE_NAMES = (
    "has_doi", "has_venue", "year_recency",
    "title_log_len", "abstract_log_len",
    "corpus_affinity", "prestige_score",
    "nearest_kept_cosine", "positive_centroid_cosine",
    "recent_centroid_cosine", "topic_drift",
    "author_overlap_count",
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
    feature_dim: int                # 777 = 768 SPECTER2 + 9 extras (Sprint 1)
    pca_dim: int                    # only meaningful for TabPFN
    # Training payload — what we need to predict
    X_train: np.ndarray             # (n_train, feature_dim) float32
    y_train: np.ndarray             # (n_train,) float64 — continuous relevance label
    pca_object: Any = None          # sklearn PCA, only set for TabPFN
    fitted_model: Any = None        # sklearn LGBMRegressor / Ridge
    calibrator: Any = None          # legacy field, always None after Sprint 1
    # Legacy threshold fields, kept as zeros for joblib backward compat.
    t_keep: float = 0.0
    t_must: float = 0.0
    t_could: float = 0.0
    # Library-conditioned feature payload (Sprint 1 + Sprint 2).
    library_embeddings: np.ndarray | None = None  # (n_P, EMBEDDING_DIM) L2-normalised
    library_centroid: np.ndarray | None = None    # (EMBEDDING_DIM,) L2-normalised
    library_recent_centroid: np.ndarray | None = None  # mean(P ∩ last 90d), L2-norm
    library_authors_lower: frozenset[str] | None = None  # surnames in P, lower-case
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
        from zotero_summarizer.services.library_features import (
            PositiveLibrary,
            compute_library_features,
        )
        zero_centroid = np.zeros(classifier.EMBEDDING_DIM, dtype=np.float32)
        if self.library_embeddings is not None and self.library_centroid is not None:
            library = PositiveLibrary(
                embeddings=self.library_embeddings,
                centroid=self.library_centroid,
                recent_centroid=(
                    self.library_recent_centroid
                    if self.library_recent_centroid is not None
                    else self.library_centroid
                ),
                item_keys=tuple(),
                authors_lower=self.library_authors_lower or frozenset(),
            )
        else:
            library = PositiveLibrary(
                embeddings=np.zeros((0, classifier.EMBEDDING_DIM), dtype=np.float32),
                centroid=zero_centroid,
                recent_centroid=zero_centroid,
                item_keys=tuple(),
                authors_lower=frozenset(),
            )
        X_new = np.zeros((len(valid), self.feature_dim), dtype=np.float32)
        aux_contexts: list[dict[str, float]] = []
        for i, it in enumerate(valid):
            title = (it.get("title") or "").strip()
            abstract = (it.get("abstract") or "").strip()
            venue = (it.get("publication_title") or it.get("venue") or "").strip()
            cache_key = str(it.get("item_key") or it.get("item_id") or f"item_{i}")
            emb = classifier.get_or_compute_embedding(
                corpus_db_path, cache_key, title, abstract,
            )
            X_new[i, :classifier.EMBEDDING_DIM] = emb
            doi = (it.get("doi") or "").strip()
            year_str = (it.get("publication_date") or it.get("year") or "")[:4]
            year_i = int(year_str) if year_str.isdigit() else None
            affinity, prestige, ctx = classifier._compute_aux_with_context(
                embed_cache, openalex_client,
                title=title, abstract=abstract, doi=doi, year=year_i,
            )
            aux_contexts.append(ctx)
            authors_str = (it.get("authors") or "").strip()
            nearest, centroid, recent, drift, authors_overlap = compute_library_features(
                emb, library, candidate_authors=authors_str,
            )
            feature_row = {"doi": doi, "venue": venue, "year": year_str}
            X_new[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
                feature_row, title, abstract,
                corpus_affinity=affinity, prestige_score=prestige,
                nearest_kept_cosine=nearest, positive_centroid_cosine=centroid,
                recent_centroid_cosine=recent, topic_drift=drift,
                author_overlap_count=authors_overlap,
            )
            if progress_cb is not None and (i + 1) % 10 == 0:
                progress_cb(i + 1, len(valid))

        # 2. Raw predict — uses pre-fitted sklearn model or re-fits TabPFN.
        p_raw = self._raw_predict(X_new)

        # 2b. SHAP (optional, LightGBM only — TreeSHAP via pred_contrib=True).
        shap_per_item: list[list[dict[str, float]] | None] = [None] * len(valid)
        if return_shap and self.classifier_name == "lightgbm" and self.fitted_model is not None:
            contribs = self.fitted_model.predict(X_new, pred_contrib=True)
            for i in range(len(valid)):
                shap_per_item[i] = _format_shap(contribs[i])

        # 3. Score → priority. Regression output is the continuous relevance
        # in [1, 5]; deterministic bucketing via `domain.score_to_priority`
        # produces the four-class label kept for UI / Zotero-note compat.
        from zotero_summarizer.domain import score_to_priority

        p_clip = np.clip(p_raw, 1.0, 5.0)
        predictions: list[classifier.FeedPrediction] = []
        for i, (it, raw, score) in enumerate(zip(valid, p_raw, p_clip)):
            title = (it.get("title") or "").strip()
            abstract = (it.get("abstract") or "").strip()
            preview = abstract[:200].rstrip()
            if len(abstract) > 200:
                preview += "…"
            s = float(score)
            predictions.append(classifier.FeedPrediction(
                item_key=str(it.get("item_key") or it.get("item_id") or ""),
                title=title,
                authors=(it.get("authors") or "").strip(),
                venue=(it.get("publication_title") or it.get("venue") or "").strip(),
                doi=(it.get("doi") or "").strip(),
                abstract_preview=preview,
                raw_score=float(raw),
                calibrated_score=s / 5.0,
                predicted_priority=score_to_priority(s),
                shap_contribs=shap_per_item[i],
                aux_context=aux_contexts[i],
            ))
        return predictions

    def _raw_predict(self, X_new: np.ndarray) -> np.ndarray:
        """Model-specific predict, returning a 1-D array of relevance scores in [1, 5]
        (clipping is the caller's responsibility)."""
        if self.classifier_name == "tabpfn":
            from tabpfn import TabPFNRegressor

            X_train_red = self.pca_object.transform(self.X_train[:, :classifier.EMBEDDING_DIM])
            X_new_red = self.pca_object.transform(X_new[:, :classifier.EMBEDDING_DIM])
            X_train_full = np.concatenate(
                [X_train_red, self.X_train[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
            X_new_full = np.concatenate(
                [X_new_red, X_new[:, classifier.EMBEDDING_DIM:]], axis=1
            ).astype(np.float32)
            reg = TabPFNRegressor(
                n_estimators=8, device="auto",
                ignore_pretraining_limits=False, random_state=42,
            )
            reg.fit(X_train_full, self.y_train)
            return reg.predict(X_new_full)
        if self.classifier_name in ("lightgbm", "logreg"):
            assert self.fitted_model is not None, (
                f"fitted_model missing for {self.classifier_name}; bug?"
            )
            # Sprint-3b: when a PCA object is attached (LightGBM with
            # Optuna-suggested PCA dim), apply it to new items so the
            # predict matrix matches the trained shape.
            if self.pca_object is not None and self.classifier_name == "lightgbm":
                emb_red = self.pca_object.transform(
                    X_new[:, :classifier.EMBEDDING_DIM]
                )
                X_new = np.concatenate(
                    [emb_red, X_new[:, classifier.EMBEDDING_DIM:]], axis=1
                ).astype(np.float32)
            return self.fitted_model.predict(X_new)
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
    from sklearn.model_selection import KFold
    from zotero_summarizer.services import hybrid_gt

    if classifier_name not in ("tabpfn", "lightgbm", "logreg"):
        raise ValueError(f"unsupported classifier_name {classifier_name!r}")

    # 1. Load + filter training rows.
    with golden_csv.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(_csv.DictReader(f))
    if triage_db_path is not None:
        all_rows = hybrid_gt.apply_hybrid(all_rows, triage_db_path)

    # Hygiene cut: F5 (in_trash) + Sprint-1 tier filter (first_glance, meta).
    from zotero_summarizer.domain import is_training_eligible

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
    from zotero_summarizer.services.library_features import (
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
            emb, library, candidate_authors=authors_str,
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
    from zotero_summarizer.services.label_weights import compute_row_weights
    sw_all = compute_row_weights(train_rows)

    preds_oof = np.zeros(n_train, dtype=np.float64)
    kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)
    for fold_idx, (tr, vl) in enumerate(kf.split(X_train), start=1):
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
        from zotero_summarizer.services.tune import load_tuned_params

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
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
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
