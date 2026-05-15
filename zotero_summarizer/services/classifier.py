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


LOGGER = logging.getLogger(__name__)


SPECTER2_MODEL_NAME = "allenai/specter2_base"
EMBEDDING_DIM = 768
# Extras layout: has_doi, has_venue, year_recency, title_log_len, abstract_log_len,
#                corpus_affinity, prestige_score
N_EXTRA_FEATURES = 7
FEATURE_DIM = EMBEDDING_DIM + N_EXTRA_FEATURES
POSITIVE_CLASSES = frozenset({"must_read", "should_read"})
CURRENT_YEAR = 2026          # for `year_recency` feature; bump or compute dynamically


_SCHEMA = """
CREATE TABLE IF NOT EXISTS specter2_embeddings (
    item_key      TEXT PRIMARY KEY,
    content_hash  TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    computed_at   TEXT DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Embedding layer
# ---------------------------------------------------------------------------


def _content_hash(title: str, abstract: str, authors: str = "", venue: str = "") -> str:
    """Stable identity for the (title, abstract, authors, venue) tuple.

    Changing any of these invalidates the cached embedding — desired, since
    SPECTER2 input now includes all four. Pre-1.10 entries (hash from
    title+abstract only) will silently miss and recompute.
    """
    blob = f"{title}|||{abstract}|||{authors}|||{venue}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


_MODEL_CACHE: dict[str, Any] = {}


def _load_specter2() -> tuple[Any, Any, Any]:
    """Lazy-load SPECTER2 (heavy import). Returns (tokenizer, model, torch)."""
    if "loaded" in _MODEL_CACHE:
        return _MODEL_CACHE["tok"], _MODEL_CACHE["mdl"], _MODEL_CACHE["torch"]
    LOGGER.info("loading SPECTER2 model %r — first call may download ~400MB", SPECTER2_MODEL_NAME)
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(SPECTER2_MODEL_NAME)
    mdl = AutoModel.from_pretrained(SPECTER2_MODEL_NAME)
    mdl.eval()
    _MODEL_CACHE.update({"tok": tok, "mdl": mdl, "torch": torch, "loaded": True})
    LOGGER.info("SPECTER2 ready")
    return tok, mdl, torch


def compute_embedding(
    title: str,
    abstract: str,
    *,
    authors: str = "",
    venue: str = "",
) -> np.ndarray:
    """Run SPECTER2 once. Returns a (768,) float32 ndarray.

    Input layout: ``title [SEP] authors [SEP] venue [SEP] abstract``. SPECTER2
    was trained on (title [SEP] abstract); appending authors+venue gives the
    encoder access to provenance signals the user implicitly weights when
    deciding whether to engage with a paper. Empty fields are dropped from
    the prompt to avoid stuffing the encoder with separators-only.
    """
    tok, mdl, torch = _load_specter2()
    parts = [p for p in [
        (title or "Untitled").strip(),
        (authors or "").strip(),
        (venue or "").strip(),
        (abstract or "").strip(),
    ] if p]
    text = tok.sep_token.join(parts)
    inputs = tok(text, padding=True, truncation=True, max_length=512, return_tensors="pt")
    with torch.no_grad():
        outputs = mdl(**inputs)
    cls = outputs.last_hidden_state[:, 0, :].squeeze(0).cpu().numpy()
    return cls.astype(np.float32)


def get_or_compute_embedding(
    db_path: Path,
    item_key: str,
    title: str,
    abstract: str,
    *,
    authors: str = "",
    venue: str = "",
) -> np.ndarray:
    """Return cached embedding when content_hash matches, otherwise compute."""
    _ensure_schema(db_path)
    ch = _content_hash(title, abstract, authors=authors, venue=venue)
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT content_hash, embedding_json FROM specter2_embeddings WHERE item_key = ?",
            (item_key,),
        ).fetchone()
        if row and row[0] == ch:
            return np.asarray(json.loads(row[1]), dtype=np.float32)
        emb = compute_embedding(title, abstract, authors=authors, venue=venue)
        conn.execute(
            "INSERT OR REPLACE INTO specter2_embeddings (item_key, content_hash, embedding_json) "
            "VALUES (?, ?, ?)",
            (item_key, ch, json.dumps(emb.tolist())),
        )
        conn.commit()
        return emb
    finally:
        conn.close()


def _ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Training + cross-validation
# ---------------------------------------------------------------------------


@dataclass
class ClassifierReport:
    n_rows: int
    n_positive: int
    embeddings_computed: int
    embeddings_cached: int
    auc: float                            # OOF AUC on the CV portion
    elapsed_seconds: float
    cv_probabilities: list[float]         # CALIBRATED out-of-fold P(keep), one per CV row
    cv_predictions: list[str]             # 4-class priority from adaptive thresholds
    item_keys: list[str]                  # parallel to cv_probabilities
    # Phase-1.10 additions:
    optimal_threshold: float = 0.5        # Youden's-J threshold for binary keep/skip
    must_threshold: float = 0.75          # adaptive 4-class cutoffs (see _adaptive_cutoffs)
    could_threshold: float = 0.25
    # Held-out test set evaluated with the same calibrator + thresholds.
    holdout_n_rows: int = 0
    holdout_n_positive: int = 0
    holdout_auc: float = 0.0
    holdout_probabilities: list[float] = field(default_factory=list)
    holdout_predictions: list[str] = field(default_factory=list)
    holdout_item_keys: list[str] = field(default_factory=list)


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
    from sklearn.linear_model import LogisticRegression
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


@dataclass
class FeedPrediction:
    """One row in a feed-prediction batch."""

    item_key: str
    title: str
    authors: str
    venue: str
    doi: str
    abstract_preview: str
    raw_score: float
    calibrated_score: float
    predicted_priority: str
    # Empty slot for the human reviewer to fill in.
    your_label: str = ""
    # Phase 1.14: per-feature TreeSHAP contributions (LightGBM only) + raw
    # OpenAlex author/venue stats for UI display. None when the classifier
    # doesn't support SHAP (e.g. TabPFN) or the caller didn't request it.
    shap_contribs: list[dict[str, float]] | None = None
    aux_context: dict[str, float] | None = None


def predict_new_items(
    training_rows: list[dict[str, str]],
    new_items: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    classifier_name: str = "tabpfn",
    pca_dim: int = 100,
    n_folds: int = 5,
    calibration: str = "isotonic",
    threshold_strategy: str = "youden",
    abstract_preview_chars: int = 200,
    goals_config: Any | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[list[FeedPrediction], dict[str, float]]:
    """Train on the golden labels, predict 4-class priority for each new item.

    Pipeline mirrors :func:`cross_validate`'s held-out path:

      1. Build (X_train, y_train) from rows that have title + abstract + gold.
      2. Run k-fold CV on training set → OOF probabilities.
      3. Fit a fresh calibrator on (OOF probs, y_train).
      4. Pick the binary keep-threshold from OOF probs via Youden's J.
      5. Derive adaptive 4-class cutoffs from OOF probs.
      6. Fit the FINAL classifier on the full training set.
      7. Extract features for every new item, predict raw probs, apply
         calibrator, then apply thresholds.

    Returns ``(predictions, threshold_info)``. The latter contains the binary
    keep / must / could cutoffs so callers can document how labels were
    derived.
    """
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    # 1. Filter & featurise training set.
    keys, titles, abstracts, labels, train_rows = [], [], [], [], []
    for r in training_rows:
        gold = (r.get("gold_priority_final") or "").strip()
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        if not gold or not title or not abstract:
            continue
        keys.append(r.get("item_key", ""))
        titles.append(title)
        abstracts.append(abstract)
        labels.append(1 if gold in POSITIVE_CLASSES else 0)
        train_rows.append(r)
    if len(labels) < n_folds * 2:
        raise ValueError(
            f"need at least {n_folds * 2} labeled rows for {n_folds}-fold CV; got {len(labels)}"
        )

    embed_cache, openalex_client = _build_aux_providers(corpus_db_path, goals_config)
    n_train = len(labels)
    X_train = np.zeros((n_train, FEATURE_DIM), dtype=np.float32)
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        authors = (train_rows[i].get("authors") or "").strip()
        venue = (train_rows[i].get("venue") or "").strip()
        X_train[i, :EMBEDDING_DIM] = get_or_compute_embedding(
            corpus_db_path, k, t, a, authors=authors, venue=venue,
        )
        year_str = (train_rows[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (train_rows[i].get("doi") or "").strip()
        affinity, prestige = _compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
        )
        X_train[i, EMBEDDING_DIM:] = _extra_features(
            train_rows[i], t, a,
            corpus_affinity=affinity, prestige_score=prestige,
        )
        if progress_cb is not None and (i + 1) % 50 == 0:
            progress_cb(i + 1, n_train)
    y_train = np.asarray(labels, dtype=np.int32)

    # 2. K-fold OOF predictions for calibration + threshold tuning.
    probs_oof = np.zeros(n_train, dtype=np.float64)
    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    for fold_idx, (tr, vl) in enumerate(skf.split(X_train, y_train), start=1):
        p_tr_raw, p_vl_raw = _fit_predict(
            classifier_name, X_train[tr], y_train[tr], X_train[vl],
            pca_dim=pca_dim, return_train_probs=True,
        )
        cal = _fit_calibrator(p_tr_raw, y_train[tr], method=calibration)
        probs_oof[vl] = _apply_calibrator(cal, p_vl_raw)
        LOGGER.info("oof fold %d/%d done", fold_idx, n_folds)

    auc = float(roc_auc_score(y_train, probs_oof)) if len(set(y_train)) > 1 else 0.0
    t_keep = _find_optimal_threshold(y_train, probs_oof, strategy=threshold_strategy)
    must_t, could_t = _adaptive_4class_cutoffs(probs_oof, t_keep)

    # 3. Final calibrator + final classifier on full training set.
    final_calibrator = _fit_calibrator(probs_oof, y_train, method=calibration)

    # 4. Featurise new items.
    valid_new: list[dict[str, str]] = [
        it for it in new_items
        if (it.get("title") or "").strip() and (it.get("abstract") or "").strip()
    ]
    if not valid_new:
        return [], {
            "oof_auc": auc,
            "t_keep": t_keep,
            "t_must": must_t,
            "t_could": could_t,
        }

    n_new = len(valid_new)
    X_new = np.zeros((n_new, FEATURE_DIM), dtype=np.float32)
    for i, it in enumerate(valid_new):
        title = (it.get("title") or "").strip()
        abstract = (it.get("abstract") or "").strip()
        authors = (it.get("authors") or "").strip()
        venue = (it.get("publication_title") or it.get("venue") or "").strip()
        # Use stable per-item cache key. Feed items have integer item_id; library
        # items have item_key. Either becomes the cache primary key.
        cache_key = str(it.get("item_key") or it.get("item_id") or f"feed_{i}")
        X_new[i, :EMBEDDING_DIM] = get_or_compute_embedding(
            corpus_db_path, cache_key, title, abstract, authors=authors, venue=venue,
        )
        doi = (it.get("doi") or "").strip()
        year_str = (it.get("publication_date") or "")[:4]
        year_i = int(year_str) if year_str.isdigit() else None
        affinity, prestige = _compute_aux(
            embed_cache, openalex_client,
            title=title, abstract=abstract, doi=doi, year=year_i,
        )
        # Build a row dict that _extra_features understands.
        feature_row = {
            "doi": doi,
            "venue": venue,
            "year": year_str,
        }
        X_new[i, EMBEDDING_DIM:] = _extra_features(
            feature_row, title, abstract,
            corpus_affinity=affinity, prestige_score=prestige,
        )

    # 5. Final classifier fit on FULL training → predict on new.
    _, p_new_raw = _fit_predict(
        classifier_name, X_train, y_train, X_new,
        pca_dim=pca_dim, return_train_probs=False,
    )
    p_new_cal = _apply_calibrator(final_calibrator, p_new_raw)

    # 6. Assemble report rows.
    predictions: list[FeedPrediction] = []
    for it, raw, cal in zip(valid_new, p_new_raw, p_new_cal):
        title = (it.get("title") or "").strip()
        abstract = (it.get("abstract") or "").strip()
        if len(abstract) > abstract_preview_chars:
            abstract = abstract[:abstract_preview_chars].rstrip() + "…"
        priority = _prob_to_priority_adaptive(
            float(cal), t_keep=t_keep, t_must=must_t, t_could=could_t,
        )
        predictions.append(FeedPrediction(
            item_key=str(it.get("item_key") or it.get("item_id") or ""),
            title=title,
            authors=(it.get("authors") or "").strip(),
            venue=(it.get("publication_title") or it.get("venue") or "").strip(),
            doi=(it.get("doi") or "").strip(),
            abstract_preview=abstract,
            raw_score=float(raw),
            calibrated_score=float(cal),
            predicted_priority=priority,
        ))

    # Sort by calibrated score descending so highest-confidence picks are first.
    predictions.sort(key=lambda p: p.calibrated_score, reverse=True)

    return predictions, {
        "oof_auc": auc,
        "t_keep": t_keep,
        "t_must": must_t,
        "t_could": could_t,
    }


def write_feed_predictions_csv(
    predictions: list[FeedPrediction],
    path: Path,
) -> None:
    """Write predictions to CSV with an empty ``your_label`` column for review."""
    import csv as _csv
    from dataclasses import asdict as _asdict

    if not predictions:
        path.write_text("")
        return
    fieldnames = list(_asdict(predictions[0]).keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in predictions:
            writer.writerow(_asdict(p))


def format_feed_predictions_markdown(
    predictions: list[FeedPrediction],
    thresholds: dict[str, float],
) -> str:
    """Compact human-readable summary suitable for terminal review."""
    lines = []
    lines.append(
        f"thresholds (calibrated probability): keep≥{thresholds['t_keep']:.3f} · "
        f"must≥{thresholds['t_must']:.3f} · could≥{thresholds['t_could']:.3f}"
    )
    lines.append(f"OOF AUC on training set: {thresholds['oof_auc']:.3f}")
    lines.append("")
    lines.append("| # | priority | cal_score | title (~80 chars) | venue | authors (1st) |")
    lines.append("|---|---|---|---|---|---|")
    for i, p in enumerate(predictions, start=1):
        title = p.title[:80].replace("|", "\\|")
        first_author = p.authors.split(";")[0].strip()[:30].replace("|", "\\|")
        venue = p.venue[:25].replace("|", "\\|")
        lines.append(
            f"| {i} | **{p.predicted_priority}** | {p.calibrated_score:.3f} "
            f"| {title} | {venue} | {first_author} |"
        )
    return "\n".join(lines)


def _fit_predict(
    classifier_name: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    *,
    pca_dim: int = 100,
    return_train_probs: bool = False,
) -> tuple[np.ndarray | None, np.ndarray]:
    """Train ``classifier_name`` and return ``(train_probs_or_None, val_probs)``.

    ``train_probs`` is needed by the calibrator (we fit on training-set
    predictions to learn how the classifier's raw scores map to true
    probabilities); ``None`` for the held-out predict-only path.
    """
    from sklearn.linear_model import LogisticRegression

    if classifier_name == "logreg":
        clf = LogisticRegression(
            C=1.0,
            class_weight="balanced",
            max_iter=1000,
            solver="lbfgs",
        )
        clf.fit(X_train, y_train)
        p_val = clf.predict_proba(X_val)[:, 1]
        p_train = clf.predict_proba(X_train)[:, 1] if return_train_probs else None
        return p_train, p_val

    if classifier_name == "tabpfn":
        X_train_red, X_val_red = _reduce_for_tabpfn(X_train, X_val, pca_dim=pca_dim)
        from tabpfn import TabPFNClassifier

        clf = TabPFNClassifier(
            n_estimators=8,
            device="auto",
            ignore_pretraining_limits=False,
            random_state=42,
        )
        clf.fit(X_train_red, y_train)
        p_val = clf.predict_proba(X_val_red)[:, 1]
        p_train = clf.predict_proba(X_train_red)[:, 1] if return_train_probs else None
        return p_train, p_val

    if classifier_name == "lightgbm":
        import lightgbm as lgb

        clf = lgb.LGBMClassifier(
            n_estimators=200,
            num_leaves=15,
            max_depth=4,
            learning_rate=0.05,
            min_child_samples=10,
            reg_lambda=1.0,
            class_weight="balanced",
            verbose=-1,
            random_state=42,
            n_jobs=1,
            num_threads=1,
        )
        clf.fit(X_train, y_train)
        p_val = clf.predict_proba(X_val)[:, 1]
        p_train = clf.predict_proba(X_train)[:, 1] if return_train_probs else None
        return p_train, p_val

    raise ValueError(
        f"unknown classifier_name {classifier_name!r}; "
        "use 'logreg', 'tabpfn', or 'lightgbm'"
    )


def _fit_calibrator(p_train: np.ndarray, y_train: np.ndarray, *, method: str = "isotonic"):
    """Fit a probability calibrator on training scores.

    * ``isotonic``: monotonic step-function, no parametric assumption.
    * ``sigmoid``: Platt scaling (logistic on raw scores).
    * ``none``: identity — return raw probabilities.
    """
    if method == "none":
        return None
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression

        cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        cal.fit(p_train, y_train)
        return cal
    if method == "sigmoid":
        from sklearn.linear_model import LogisticRegression

        cal = LogisticRegression(solver="lbfgs", max_iter=1000)
        cal.fit(p_train.reshape(-1, 1), y_train)
        return cal
    raise ValueError(f"unknown calibration method {method!r}")


def _apply_calibrator(calibrator, p: np.ndarray) -> np.ndarray:
    if calibrator is None:
        return np.asarray(p, dtype=np.float64)
    # IsotonicRegression: 1-D in/out. LogisticRegression: needs 2-D.
    try:
        return calibrator.transform(p).astype(np.float64)
    except AttributeError:
        return calibrator.predict_proba(np.asarray(p).reshape(-1, 1))[:, 1].astype(np.float64)


def _find_optimal_threshold(
    y: np.ndarray,
    p: np.ndarray,
    *,
    strategy: str = "youden",
) -> float:
    """Pick the binary cutoff on calibrated probs.

    ``youden``: TPR(t) − FPR(t) maximised over all candidate thresholds (the
    classical operating-point choice when FP and FN cost equally).
    ``f1``: F1-score maximised — biased toward recall when positives are
    rare.
    """
    from sklearn.metrics import f1_score, roc_curve

    if len(set(y)) < 2:
        return 0.5
    if strategy == "youden":
        fpr, tpr, thresholds = roc_curve(y, p)
        j = tpr - fpr
        # Skip the first threshold (which is +inf in sklearn).
        best = int(np.argmax(j[1:])) + 1
        return float(thresholds[best])
    if strategy == "f1":
        # Sweep over unique predicted probabilities.
        cand = np.unique(np.concatenate([[0.0, 1.0], p]))
        scores = [f1_score(y, (p >= t).astype(int), zero_division=0) for t in cand]
        return float(cand[int(np.argmax(scores))])
    raise ValueError(f"unknown threshold strategy {strategy!r}")


def _adaptive_4class_cutoffs(p: np.ndarray, t_keep: float) -> tuple[float, float]:
    """Split keep/skip groups by quantile to derive must/could thresholds.

    Returns ``(must_threshold, could_threshold)`` such that:
      * ``p >= must_threshold``        → must_read   (top quarter of keep group)
      * ``t_keep <= p < must_threshold`` → should_read
      * ``could_threshold <= p < t_keep`` → could_read
      * ``p < could_threshold``        → dont_read   (bottom quarter of skip group)

    Uses the **75th percentile** of the keep group for ``must_threshold`` and
    the **25th percentile** of the skip group for ``could_threshold``. This
    avoids the degenerate "median = 0" case that made ``dont_read``
    unreachable when negatives clustered tightly near zero. Falls back to a
    small offset around ``t_keep`` if a group is empty or collapses.
    """
    keep_probs = p[p >= t_keep]
    skip_probs = p[p < t_keep]
    if len(keep_probs) >= 4:
        must_t = float(np.quantile(keep_probs, 0.75))
    elif len(keep_probs) >= 1:
        must_t = float(np.max(keep_probs))
    else:
        must_t = float(t_keep)
    if len(skip_probs) >= 4:
        could_t = float(np.quantile(skip_probs, 0.25))
    elif len(skip_probs) >= 1:
        could_t = float(np.min(skip_probs))
    else:
        could_t = float(t_keep)
    # Guard against the buckets collapsing into each other.
    must_t = max(must_t, t_keep)
    could_t = min(could_t, t_keep)
    # If the skip distribution is degenerate (everything at 0), pull could_t
    # off the floor so dont_read is actually reachable.
    if could_t <= 0.0 and t_keep > 0.0:
        could_t = t_keep / 4.0
    return must_t, could_t


def _prob_to_priority_adaptive(
    p: float,
    *,
    t_keep: float,
    t_must: float,
    t_could: float,
) -> str:
    """4-class label using calibrated probability + tuned cutoffs."""
    if p >= t_must:
        return "must_read"
    if p >= t_keep:
        return "should_read"
    if p >= t_could:
        return "could_read"
    return "dont_read"


def _reduce_for_tabpfn(
    X_train: np.ndarray,
    X_val: np.ndarray,
    *,
    pca_dim: int = 100,
) -> tuple[np.ndarray, np.ndarray]:
    """PCA-reduce the SPECTER2 part (first 768 dims) so total feature count
    fits under TabPFN's 500-feature ceiling. Tabular extras pass through.

    PCA is fit on the TRAIN fold only (no test leakage) and applied to val.
    """
    from sklearn.decomposition import PCA

    emb_train = X_train[:, :EMBEDDING_DIM]
    emb_val = X_val[:, :EMBEDDING_DIM]
    extras_train = X_train[:, EMBEDDING_DIM:]
    extras_val = X_val[:, EMBEDDING_DIM:]
    # PCA can output at most min(n_samples, n_features) components.
    actual_dim = min(pca_dim, emb_train.shape[0], emb_train.shape[1])
    pca = PCA(n_components=actual_dim, random_state=42)
    emb_train_red = pca.fit_transform(emb_train)
    emb_val_red = pca.transform(emb_val)
    return (
        np.concatenate([emb_train_red, extras_train], axis=1).astype(np.float32),
        np.concatenate([emb_val_red, extras_val], axis=1).astype(np.float32),
    )


def _build_aux_providers(
    corpus_db_path: Path,
    goals_config: Any | None,
) -> tuple[Any, Any]:
    """Lazy-init the corpus EmbeddingCache + OpenAlex client when configured.

    Returns ``(embed_cache_or_None, openalex_client_or_None)``. Either being
    None makes :func:`_compute_aux` fall back to its neutral defaults so the
    classifier still runs end-to-end without those signals.
    """
    embed_cache = None
    openalex_client = None
    if goals_config is None:
        return embed_cache, openalex_client

    try:
        corpus_cfg = getattr(goals_config, "corpus", None)
        if corpus_cfg is not None and getattr(corpus_cfg, "enabled", False):
            from zotero_summarizer.storage.corpus import EmbeddingCache

            embed_cache = EmbeddingCache(
                corpus_db_path, corpus_cfg.embedding_model
            )
    except Exception as exc:
        LOGGER.warning("corpus EmbeddingCache load failed: %s", exc)

    try:
        prestige_cfg = getattr(goals_config, "prestige", None)
        if prestige_cfg is not None and getattr(prestige_cfg, "enabled", False):
            from zotero_summarizer.integrations.openalex import OpenAlexClient
            from zotero_summarizer.integrations.openalex_cache import OpenAlexCache

            cache = OpenAlexCache(
                corpus_db_path,
                ttl_seconds=int(prestige_cfg.cache_ttl_days) * 86400,
            )
            mailto = (getattr(prestige_cfg, "user_agent_email", "") or "").strip() or None
            openalex_client = OpenAlexClient(cache, mailto=mailto)
    except Exception as exc:
        LOGGER.warning("OpenAlex client init failed: %s", exc)

    return embed_cache, openalex_client


def _compute_aux(
    embed_cache: Any,
    openalex_client: Any,
    *,
    title: str,
    abstract: str,
    doi: str,
    year: int | None,
    prestige_neutral: float = 3.0,
    stale_days: int = 30,
) -> tuple[float, float]:
    """Return ``(corpus_affinity, prestige_score)`` for one paper.

    Both defaults are 0.0 / 3.0 (neutral). Failures are swallowed — these
    features must never block training.
    """
    affinity, prestige, _ctx = _compute_aux_with_context(
        embed_cache, openalex_client,
        title=title, abstract=abstract, doi=doi, year=year,
        prestige_neutral=prestige_neutral, stale_days=stale_days,
    )
    return affinity, prestige


def _compute_aux_with_context(
    embed_cache: Any,
    openalex_client: Any,
    *,
    title: str,
    abstract: str,
    doi: str,
    year: int | None,
    prestige_neutral: float = 3.0,
    stale_days: int = 30,
) -> tuple[float, float, dict[str, float]]:
    """Same as :func:`_compute_aux` but also returns raw OpenAlex Work stats.

    The third element is an ``aux_context`` dict consumed by the review UI:

      ``max_author_h_index`` — highest h-index across all authors (int)
      ``venue_works_count``  — host journal/conference output count (int)
      ``cited_by_count``     — citations of THIS work to date (int)

    Missing fields default to ``0`` (not "neutral"), so the UI can distinguish
    "OpenAlex said zero" from "we didn't ask".
    """
    affinity = 0.0
    prestige = float(prestige_neutral)
    ctx: dict[str, float] = {
        "max_author_h_index": 0.0,
        "venue_works_count": 0.0,
        "cited_by_count": 0.0,
    }
    if embed_cache is not None:
        try:
            result = embed_cache.match_candidate(title, abstract, stale_days_for_weak_negative=stale_days)
            affinity = float(getattr(result, "affinity_score", 0.0) or 0.0)
        except Exception as exc:
            LOGGER.debug("corpus match failed: %s", exc)
    if openalex_client is not None:
        try:
            from zotero_summarizer.services.prestige import lookup_prestige

            score, work = lookup_prestige(
                openalex_client,
                doi=doi or None,
                title=title,
                year=year,
                neutral=prestige_neutral,
            )
            prestige = float(score)
            if work is not None:
                ctx["max_author_h_index"] = float(getattr(work, "max_author_h_index", 0) or 0)
                ctx["venue_works_count"] = float(getattr(work, "venue_works_count", 0) or 0)
                ctx["cited_by_count"] = float(getattr(work, "cited_by_count", 0) or 0)
        except Exception as exc:
            LOGGER.debug("prestige lookup failed: %s", exc)
    return affinity, prestige, ctx


def _extra_features(
    row: dict[str, str],
    title: str,
    abstract: str,
    *,
    corpus_affinity: float = 0.0,
    prestige_score: float = 3.0,
) -> np.ndarray:
    """Tabular features alongside the SPECTER2 embedding (7 dims).

    Layout matches ``N_EXTRA_FEATURES``:
      0  has_doi          binary
      1  has_venue        binary
      2  year_recency     int 0..20 (older = 20, missing = 20)
      3  title_log_len    log1p(len)
      4  abstract_log_len log1p(len)
      5  corpus_affinity  cosine to research_goals via all-MiniLM, [-1, 1]
      6  prestige_score   OpenAlex h-index/venue blend, [1, 5] (3.0 = neutral)

    All seven are CONTENT- and provenance-based. Engagement-derived signals
    (emoji tags, notes, annotations) are deliberately excluded — those are
    the labels we are trying to predict, so they would leak.
    """
    has_doi = 1.0 if (row.get("doi") or "").strip() else 0.0
    has_venue = 1.0 if (row.get("venue") or "").strip() else 0.0
    year_str = (row.get("year") or "").strip()
    try:
        year = int(year_str[:4]) if year_str[:4].isdigit() else 0
    except (ValueError, IndexError):
        year = 0
    recency = float(min(20, max(0, CURRENT_YEAR - year))) if year else 20.0
    title_log_len = float(np.log1p(len(title or "")))
    abstract_log_len = float(np.log1p(len(abstract or "")))
    return np.asarray(
        [
            has_doi, has_venue, recency, title_log_len, abstract_log_len,
            float(corpus_affinity), float(prestige_score),
        ],
        dtype=np.float32,
    )


def _embedding_cached(db_path: Path, item_key: str, content_hash: str) -> bool:
    if not db_path.exists():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM specter2_embeddings WHERE item_key=? AND content_hash=?",
            (item_key, content_hash),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# CSV in-place update
# ---------------------------------------------------------------------------


def write_predictions_to_csv(
    input_csv: Path,
    report: ClassifierReport,
    *,
    classifier_name: str,
) -> int:
    """Write predictions back into the golden CSV under per-classifier columns.

    Columns ``cls_{name}_score``, ``cls_{name}_priority``, ``cls_{name}_split``
    are created on first use and rewritten on subsequent runs of the SAME
    classifier. Running a different classifier never touches another's
    columns — every run is preserved (FAIR ``Reusable``).

    Rows that didn't get a prediction (skipped during CV) get blank values.
    Returns the number of updated rows.
    """
    import csv

    if not classifier_name or "/" in classifier_name or " " in classifier_name:
        raise ValueError(
            f"invalid classifier_name {classifier_name!r}; must be a short slug like "
            "'logreg' / 'tabpfn' / 'lightgbm' / 'llm_kather'."
        )

    rows: list[dict[str, str]] = []
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    score_col = f"cls_{classifier_name}_score"
    priority_col = f"cls_{classifier_name}_priority"
    split_col = f"cls_{classifier_name}_split"
    for col in (score_col, priority_col, split_col):
        if col not in fieldnames:
            fieldnames.append(col)

    cv_by_key = {
        key: (p, pri) for key, p, pri in zip(
            report.item_keys, report.cv_probabilities, report.cv_predictions,
        )
    }
    ho_by_key = {
        key: (p, pri) for key, p, pri in zip(
            report.holdout_item_keys, report.holdout_probabilities, report.holdout_predictions,
        )
    }
    updated = 0
    for row in rows:
        key = row.get("item_key", "")
        if key in cv_by_key:
            p, pri = cv_by_key[key]
            row[score_col] = f"{p:.4f}"
            row[priority_col] = pri
            row[split_col] = "cv"
            updated += 1
        elif key in ho_by_key:
            p, pri = ho_by_key[key]
            row[score_col] = f"{p:.4f}"
            row[priority_col] = pri
            row[split_col] = "holdout"
            updated += 1
        else:
            row.setdefault(score_col, "")
            row.setdefault(priority_col, "")
            row.setdefault(split_col, "")

    tmp = input_csv.with_suffix(input_csv.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    tmp.replace(input_csv)
    return updated


def compute_metrics_against_gold(
    input_csv: Path,
    *,
    strength_filter: set[str] | None = None,
    split: str | None = None,
    priority_column: str = "cls_priority",
    split_column: str | None = None,
) -> dict[str, Any]:
    """Read predictions vs ``gold_priority_final`` and compute P/R/F1.

    ``priority_column`` selects which prediction column to score (e.g.
    ``cls_tabpfn_priority``, ``cls_llm_kather_priority``).

    ``split`` ∈ {"cv", "holdout", None}. When set, filters by
    ``split_column`` (defaults to ``cls_{name}_split`` derived from
    ``priority_column`` when not provided). LLM-classifier columns don't have
    a split — pass ``split=None``.
    """
    import csv

    from zotero_summarizer.services import golden_metrics as gm

    if split is not None and split_column is None:
        # Auto-derive split column name from the priority column. Strips the
        # trailing "_priority" and appends "_split".
        if priority_column.endswith("_priority"):
            split_column = priority_column[: -len("_priority")] + "_split"
        else:
            split_column = "cls_split"

    gold: list[str] = []
    pred: list[str] = []
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if strength_filter:
                if (row.get("gold_signal_strength") or "").strip() not in strength_filter:
                    continue
            if split is not None and split_column:
                if (row.get(split_column) or "").strip() != split:
                    continue
            g = (row.get("gold_priority_final") or "").strip()
            p = (row.get(priority_column) or "").strip()
            if not g or not p:
                continue
            gold.append(g)
            pred.append(p)

    return {
        "total": len(gold),
        "accuracy": round(gm.accuracy(gold, pred), 4),
        "per_class": {k: v.as_dict() for k, v in gm.compute_per_class(gold, pred).items()},
        "binary": gm.compute_binary(gold, pred).as_dict(),
        "confusion": gm.compute_confusion(gold, pred),
    }
