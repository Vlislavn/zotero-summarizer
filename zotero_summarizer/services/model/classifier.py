"""SPECTER2-based paper classifier (Phase 2.0 pivot).

Replaces the LLM-from-abstract scoring path for **ranking**: SPECTER2 embeds
each paper, a logistic-regression classifier maps embedding → P(user keeps).
LLM stays in the picture for the deep-read pass on the top-K candidates.

Why SPECTER2: it is specifically trained on title+abstract pairs of academic
papers and outperforms general-purpose sentence embeddings on the citation-
recommendation benchmark by 1.5–2×. Output is 768-d CLS pooling.

Cache strategy: embeddings live in ``corpus_cache.db`` under the new table
``specter2_embeddings``, keyed by ``item_key`` + ``content_hash``. Re-runs are
free unless title/abstract changed.

Cross-validation: 5-fold stratified CV gives every row an out-of-fold
prediction. Avoids the train/test-split bias on a 707-row dataset.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from zotero_summarizer.services.model.classifier_const import *  # noqa: F401,F403
from zotero_summarizer.services.model.classifier_embed import *  # noqa: F401,F403
from zotero_summarizer.services.model.classifier_features import *  # noqa: F401,F403
from zotero_summarizer.services.model.classifier_fit import *  # noqa: F401,F403
from zotero_summarizer.services.model.classifier_io import *  # noqa: F401,F403


def cross_validate(
    rows: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    n_folds: int = 5,
    classifier_name: str = "logreg",
    pca_dim: int = 100,
    holdout_fraction: float = 0.20,
    calibration: str = "isotonic",
    threshold_strategy: str = "youden",
    goals_config: Any | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> ClassifierReport:
    """Stratified hold-out + k-fold CV with calibration and Youden-J thresholding.

    Pipeline:

    1. Filter rows to those with non-empty ``title``, ``abstract``, and
       ``gold_priority_final``.
    2. Stratified split into a CV pool (``1 - holdout_fraction``) and a
       held-out test set (``holdout_fraction``). Held-out is touched ONLY at
       the very end — protects against hyperparameter overfit.
    3. Per-fold (5-fold stratified):
         - train classifier on the train portion
         - fit calibrator (Isotonic by default) on those train predictions
         - apply calibrator to val predictions → calibrated OOF probabilities
    4. From OOF probabilities: find optimal binary threshold ``t*`` via
       Youden's J (TPR − FPR). Then split keep/skip groups by median
       probability to derive the 4-class adaptive cutoffs.
    5. Fit a final classifier on the FULL CV pool. Fit a fresh calibrator on
       OOF predictions (the only label-honest signal we have). Predict on
       held-out, apply calibrator + thresholds.

    ``classifier_name`` selects the model:
      * ``"logreg"`` — sklearn LogisticRegression on the full 773-d feature
        vector (the default — fast, transparent, decent baseline).
      * ``"lightgbm"`` — gradient-boosted trees on the same 773-d vector.
      * ``"tabpfn"`` — TabPFN-v2 transformer. Needs PCA on the SPECTER2
        embedding to stay under TabPFN's 500-feature ceiling.

    ``calibration`` ∈ {"isotonic", "sigmoid", "none"}.
    ``threshold_strategy`` ∈ {"youden", "f1"}.
    """
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold, train_test_split

    start = time.perf_counter()
    keys: list[str] = []
    titles: list[str] = []
    abstracts: list[str] = []
    labels: list[int] = []
    gold_priorities: list[str] = []
    selected_rows: list[dict[str, str]] = []
    for r in rows:
        gold = (r.get("gold_priority_final") or "").strip()
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        if not gold or not title or not abstract:
            continue
        keys.append(r.get("item_key", ""))
        titles.append(title)
        abstracts.append(abstract)
        labels.append(1 if gold in POSITIVE_CLASSES else 0)
        gold_priorities.append(gold)
        selected_rows.append(r)

    if len(labels) < n_folds * 2:
        raise ValueError(
            f"need at least {n_folds * 2} labeled rows for {n_folds}-fold CV; got {len(labels)}"
        )

    # Build feature matrix for ALL kept rows (CV + held-out).
    embed_cache, openalex_client = _build_aux_providers(corpus_db_path, goals_config)
    computed = 0
    cached = 0
    X = np.zeros((len(labels), FEATURE_DIM), dtype=np.float32)
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        authors = (selected_rows[i].get("authors") or "").strip()
        venue = (selected_rows[i].get("venue") or "").strip()
        if _embedding_cached(corpus_db_path, k, _content_hash(t, a, authors, venue)):
            cached += 1
        else:
            computed += 1
        X[i, :EMBEDDING_DIM] = get_or_compute_embedding(
            corpus_db_path, k, t, a, authors=authors, venue=venue,
        )
        year_str = (selected_rows[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (selected_rows[i].get("doi") or "").strip()
        affinity, prestige = _compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
        )
        X[i, EMBEDDING_DIM:] = _extra_features(
            selected_rows[i], t, a,
            corpus_affinity=affinity, prestige_score=prestige,
        )
        if progress_cb is not None and (i + 1) % 25 == 0:
            progress_cb(i + 1, len(labels))
    y = np.asarray(labels, dtype=np.int32)
    idx_all = np.arange(len(y))

    # G5: stratified hold-out split.
    if holdout_fraction > 0.0:
        cv_idx, holdout_idx = train_test_split(
            idx_all,
            test_size=holdout_fraction,
            stratify=y,
            random_state=42,
        )
    else:
        cv_idx, holdout_idx = idx_all, np.array([], dtype=int)
    X_cv, y_cv = X[cv_idx], y[cv_idx]

    # G3: per-fold predict + calibrate.
    probs_oof = np.zeros(len(y_cv), dtype=np.float64)
    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    for fold_idx, (train_local, val_local) in enumerate(skf.split(X_cv, y_cv), start=1):
        Xtr, ytr = X_cv[train_local], y_cv[train_local]
        Xval = X_cv[val_local]
        p_tr_raw, p_val_raw = _fit_predict(
            classifier_name, Xtr, ytr, Xval, pca_dim=pca_dim, return_train_probs=True,
        )
        calibrator = _fit_calibrator(p_tr_raw, ytr, method=calibration)
        probs_oof[val_local] = _apply_calibrator(calibrator, p_val_raw)
        LOGGER.info(
            "fold %d/%d (%s): train=%d val=%d positives=%d",
            fold_idx, n_folds, classifier_name,
            len(train_local), len(val_local), int(ytr.sum()),
        )

    auc = float(roc_auc_score(y_cv, probs_oof)) if len(set(y_cv)) > 1 else 0.0

    # G1: optimal threshold + adaptive 4-class cutoffs from OOF probs.
    t_opt = _find_optimal_threshold(y_cv, probs_oof, strategy=threshold_strategy)
    must_t, could_t = _adaptive_4class_cutoffs(probs_oof, t_opt)
    cv_predictions = [
        _prob_to_priority_adaptive(p, t_keep=t_opt, t_must=must_t, t_could=could_t)
        for p in probs_oof
    ]

    # Held-out evaluation: fit one final classifier on the full CV pool, then
    # fit a calibrator on its OOF probabilities (only label-honest signal),
    # predict on held-out, apply same thresholds.
    holdout_probs: list[float] = []
    holdout_predictions: list[str] = []
    holdout_auc = 0.0
    holdout_n_positive = 0
    if len(holdout_idx) >= 2:
        X_ho, y_ho = X[holdout_idx], y[holdout_idx]
        _, p_holdout_raw = _fit_predict(
            classifier_name, X_cv, y_cv, X_ho, pca_dim=pca_dim, return_train_probs=False,
        )
        holdout_calibrator = _fit_calibrator(probs_oof, y_cv, method=calibration)
        p_holdout_cal = _apply_calibrator(holdout_calibrator, p_holdout_raw)
        if len(set(y_ho)) > 1:
            holdout_auc = float(roc_auc_score(y_ho, p_holdout_cal))
        holdout_probs = p_holdout_cal.tolist()
        holdout_predictions = [
            _prob_to_priority_adaptive(p, t_keep=t_opt, t_must=must_t, t_could=could_t)
            for p in p_holdout_cal
        ]
        holdout_n_positive = int(y_ho.sum())

    elapsed = time.perf_counter() - start
    keys_cv = [keys[i] for i in cv_idx]
    keys_ho = [keys[i] for i in holdout_idx]

    return ClassifierReport(
        n_rows=len(y_cv),
        n_positive=int(y_cv.sum()),
        embeddings_computed=computed,
        embeddings_cached=cached,
        auc=auc,
        elapsed_seconds=elapsed,
        cv_probabilities=probs_oof.tolist(),
        cv_predictions=cv_predictions,
        item_keys=keys_cv,
        optimal_threshold=t_opt,
        must_threshold=must_t,
        could_threshold=could_t,
        holdout_n_rows=len(holdout_idx),
        holdout_n_positive=holdout_n_positive,
        holdout_auc=holdout_auc,
        holdout_probabilities=holdout_probs,
        holdout_predictions=holdout_predictions,
        holdout_item_keys=keys_ho,
    )


def predict_new_items(
    training_rows: list[dict[str, str]],
    new_items: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    classifier_name: str = "lightgbm",
    pca_dim: int = 100,
    n_folds: int = 5,
    abstract_preview_chars: int = 200,
    goals_config: Any | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[list[FeedPrediction], dict[str, float]]:
    """Train regressor on `gold_inferred_relevance`, predict relevance for new items.

    Sprint-1 redesign (May 2026). The model now outputs a continuous score
    in [1, 5]; the 4-class priority is a deterministic bucketing via
    :func:`domain.score_to_priority` and only kept for UI/notes
    compatibility. The legacy isotonic-calibrator / Youden's-J /
    quantile-bin stack has been removed.

    Pipeline:
      1. Build (X_train, y_train) from filtered rows. `y_train` is the
         continuous `gold_inferred_relevance` value.
      2. Run k-fold CV → OOF Spearman ρ for diagnostic logging.
      3. Fit the final regressor on the full training set.
      4. Featurise + score every new item; map score → priority.

    Returns ``(predictions, metadata)`` where ``metadata`` reports the
    diagnostic OOF Spearman ρ and the row counts. No threshold dict any
    more — thresholds are constants in ``domain``.
    """
    from scipy.stats import spearmanr
    from sklearn.model_selection import GroupKFold

    from zotero_summarizer.domain import (
        is_training_eligible,
        paper_group_id,
        score_to_priority,
    )

    # 1. Filter & featurise training set.
    keys, titles, abstracts, y_cont, train_rows = [], [], [], [], []
    for r in training_rows:
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

    embed_cache, openalex_client = _build_aux_providers(corpus_db_path, goals_config)
    from zotero_summarizer.services.model.library_features import (
        compute_library_features,
        load_positive_library_from_rows,
    )
    library = load_positive_library_from_rows(training_rows, corpus_db_path)
    n_train = len(y_cont)
    X_train = np.zeros((n_train, FEATURE_DIM), dtype=np.float32)
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        emb = get_or_compute_embedding(corpus_db_path, k, t, a)
        X_train[i, :EMBEDDING_DIM] = emb
        year_str = (train_rows[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (train_rows[i].get("doi") or "").strip()
        affinity, prestige = _compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
        )
        authors_str = (train_rows[i].get("authors") or "").strip()
        nearest, centroid, recent, drift, authors_overlap = compute_library_features(
            emb, library, candidate_authors=authors_str, exclude_item_key=k,
        )
        X_train[i, EMBEDDING_DIM:] = _extra_features(
            train_rows[i], t, a,
            corpus_affinity=affinity, prestige_score=prestige,
            nearest_kept_cosine=nearest, positive_centroid_cosine=centroid,
            recent_centroid_cosine=recent, topic_drift=drift,
            author_overlap_count=authors_overlap,
        )
        if progress_cb is not None and (i + 1) % 50 == 0:
            progress_cb(i + 1, n_train)
    y_train = np.asarray(y_cont, dtype=np.float64)
    from zotero_summarizer.services.model.label_weights import compute_row_weights
    sw_all = compute_row_weights(train_rows)

    # 2. K-fold OOF predictions purely for diagnostic Spearman ρ logging.
    preds_oof = np.zeros(n_train, dtype=np.float64)
    groups = [paper_group_id(r) for r in train_rows]
    skf = GroupKFold(n_splits=n_folds)
    for fold_idx, (tr, vl) in enumerate(skf.split(X_train, groups=groups), start=1):
        _, p_vl = _fit_predict(
            classifier_name, X_train[tr], y_train[tr], X_train[vl],
            pca_dim=pca_dim, return_train_probs=False,
            objective="regression",
            sample_weight=sw_all[tr],
        )
        preds_oof[vl] = p_vl
        LOGGER.info("oof fold %d/%d done", fold_idx, n_folds)
    oof_rho = float(spearmanr(y_train, preds_oof).statistic) if n_train > 2 else 0.0

    # 3. Featurise new items.
    valid_new: list[dict[str, str]] = [
        it for it in new_items
        if (it.get("title") or "").strip() and (it.get("abstract") or "").strip()
    ]
    if not valid_new:
        return [], {"oof_spearman": oof_rho, "n_train": float(n_train)}

    n_new = len(valid_new)
    X_new = np.zeros((n_new, FEATURE_DIM), dtype=np.float32)
    for i, it in enumerate(valid_new):
        title = (it.get("title") or "").strip()
        abstract = (it.get("abstract") or "").strip()
        cache_key = str(it.get("item_key") or it.get("item_id") or f"feed_{i}")
        emb_new = get_or_compute_embedding(
            corpus_db_path, cache_key, title, abstract,
        )
        X_new[i, :EMBEDDING_DIM] = emb_new
        doi = (it.get("doi") or "").strip()
        year_str = (it.get("publication_date") or "")[:4]
        year_i = int(year_str) if year_str.isdigit() else None
        affinity, prestige = _compute_aux(
            embed_cache, openalex_client,
            title=title, abstract=abstract, doi=doi, year=year_i,
        )
        authors_str = (it.get("authors") or "").strip()
        nearest_n, centroid_n, recent_n, drift_n, authors_overlap_n = compute_library_features(
            emb_new, library, candidate_authors=authors_str, exclude_item_key=cache_key,
        )
        venue = (it.get("publication_title") or it.get("venue") or "").strip()
        feature_row = {"doi": doi, "venue": venue, "year": year_str}
        X_new[i, EMBEDDING_DIM:] = _extra_features(
            feature_row, title, abstract,
            corpus_affinity=affinity, prestige_score=prestige,
            nearest_kept_cosine=nearest_n, positive_centroid_cosine=centroid_n,
            recent_centroid_cosine=recent_n, topic_drift=drift_n,
            author_overlap_count=authors_overlap_n,
        )

    # 4. Fit final regressor on FULL training, predict on new.
    _, p_new = _fit_predict(
        classifier_name, X_train, y_train, X_new,
        pca_dim=pca_dim, return_train_probs=False,
        objective="regression",
        sample_weight=sw_all,
    )
    p_new = np.clip(p_new, 1.0, 5.0)

    # 5. Assemble result rows.
    predictions: list[FeedPrediction] = []
    for it, score in zip(valid_new, p_new):
        title = (it.get("title") or "").strip()
        abstract = (it.get("abstract") or "").strip()
        if len(abstract) > abstract_preview_chars:
            abstract = abstract[:abstract_preview_chars].rstrip() + "…"
        s = float(score)
        predictions.append(FeedPrediction(
            item_key=str(it.get("item_key") or it.get("item_id") or ""),
            title=title,
            authors=(it.get("authors") or "").strip(),
            venue=(it.get("publication_title") or it.get("venue") or "").strip(),
            doi=(it.get("doi") or "").strip(),
            abstract_preview=abstract,
            raw_score=s,
            calibrated_score=s / 5.0,
            predicted_priority=score_to_priority(s),
        ))

    predictions.sort(key=lambda p: p.raw_score, reverse=True)
    return predictions, {"oof_spearman": oof_rho, "n_train": float(n_train)}

