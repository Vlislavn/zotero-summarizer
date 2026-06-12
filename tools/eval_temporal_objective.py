"""Temporal-holdout eval of the gate: honest forward-looking metrics + objective A/B.

Two SOTA-hygiene questions the shuffled GroupKFold OOF numbers can't answer:

1. **Temporal honesty.** Production always predicts the FUTURE (today's feed)
   from the PAST (everything labeled so far), but the logged OOF Spearman
   shuffles time, letting folds train on rows newer than their validation
   rows. How much of the reported ~0.65 survives a strict
   train-on-oldest/test-on-newest split?

2. **Objective A/B.** The gate is a pointwise regressor on
   ``gold_inferred_relevance``. Learning-to-rank folklore says LambdaRank
   (listwise NDCG surrogate) should order better. Does it, on this data —
   enough to justify carrying a second, scale-free score the banding stack
   can't use?

Split is GROUP-aware (``paper_group_id``: same paper never on both sides) and
temporal (groups ordered by ``days_since_added``; newest ~20% of rows held
out). Featurization is the production path (``_featurize_training_matrix``),
so embeddings/aux match what the shipped gate sees. Eval-only: writes nothing.

Usage (repo root):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_temporal_objective.py
"""
from __future__ import annotations

import csv
import math

import numpy as np

# The split is the production one — train_and_save logs temporal_spearman with
# the same helpers on every retrain; this tool adds the NDCG + objective A/B.
from zotero_summarizer.services.model.classifier_training import (
    _NO_DATE_SENTINEL as NO_DATE_SENTINEL,
    _row_days,
    _temporal_group_split,
)


def _ndcg(y_true: np.ndarray, scores: np.ndarray, k: int) -> float:
    """NDCG@k with linear gains (the labels are already a 1..5 relevance scale)."""
    order = np.argsort(-scores)[:k]
    ideal = np.sort(y_true)[::-1][:k]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(y_true[order]))
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    if idcg == 0:
        raise SystemExit("degenerate holdout: all-zero relevance")
    return dcg / idcg


def _fit_ranker(
    X_tr: np.ndarray, y_tr: np.ndarray, X_te: np.ndarray, sw: np.ndarray
) -> np.ndarray:
    """LambdaRank with the regression head's hyperparameters; one query group
    (the feed is one global ranking, not per-query sessions)."""
    import lightgbm as lgb

    ranker = lgb.LGBMRanker(
        objective="lambdarank", n_estimators=200, num_leaves=15, max_depth=4,
        learning_rate=0.05, min_child_samples=10, reg_lambda=1.0,
        label_gain=list(range(32)), verbose=-1, random_state=42,
        n_jobs=1, num_threads=1,
    )
    # Integer gains 0..4 from the continuous 1..5 labels.
    y_int = np.clip(np.round(y_tr).astype(int) - 1, 0, 4)
    ranker.fit(X_tr, y_int, group=[len(y_int)], sample_weight=sw)
    from zotero_summarizer.services.model.classifier_fit import predict_named

    return np.asarray(predict_named(ranker, X_te), dtype=np.float64)


def main() -> None:
    from scipy.stats import spearmanr
    from sklearn.model_selection import GroupKFold

    from zotero_summarizer.domain import paper_group_id, score_to_priority
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services.golden import hybrid_gt
    from zotero_summarizer.services.model import classifier, golden_metrics
    from zotero_summarizer.services.model.classifier_training import _featurize_training_matrix
    from zotero_summarizer.services.model.label_weights import compute_row_weights
    from zotero_summarizer.services.model.library_features import load_positive_library_from_rows

    settings_ = get_settings()
    goals_config = read_config(settings_.config_path)
    with settings_.golden_csv_path.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(csv.DictReader(f))
    all_rows = hybrid_gt.apply_hybrid(all_rows, settings_.triage_db_path)

    data = classifier._filter_train_rows(all_rows, n_folds=5)
    _keys, _titles, _abstracts, y_cont, train_rows = data
    library = load_positive_library_from_rows(all_rows, settings_.corpus_db_path)
    print(f"featurising {len(train_rows)} rows (production path, cached embeds)…")
    X = _featurize_training_matrix(
        data, library,
        corpus_db_path=settings_.corpus_db_path, goals_config=goals_config,
    )
    y = np.asarray(y_cont, dtype=np.float64)
    sw = compute_row_weights(train_rows)
    groups = [paper_group_id(r) for r in train_rows]
    gold = [(r.get("gold_priority_final") or "").strip() for r in train_rows]

    tr_idx, te_idx = _temporal_group_split(train_rows, groups)
    te_days = [_row_days(train_rows[i]) for i in te_idx if _row_days(train_rows[i]) < NO_DATE_SENTINEL]
    print(f"temporal split: train={len(tr_idx)} test={len(te_idx)} "
          f"(test recency: {min(te_days):.0f}–{max(te_days):.0f} days old)")

    # --- 1. Shuffled GroupKFold OOF (the currently-reported number), same data.
    preds_oof = np.zeros(len(y))
    for tr, vl in GroupKFold(n_splits=5).split(X, groups=groups):
        _, p = classifier._fit_predict(
            "lightgbm", X[tr], y[tr], X[vl], objective="regression", sample_weight=sw[tr],
        )
        preds_oof[vl] = p
    print(f"\nshuffled GroupKFold OOF : Spearman={spearmanr(y, preds_oof).statistic:.3f}")

    # --- 2. Temporal holdout, regression (the production objective).
    _, p_reg = classifier._fit_predict(
        "lightgbm", X[tr_idx], y[tr_idx], X[te_idx],
        objective="regression", sample_weight=sw[tr_idx],
    )
    y_te = y[te_idx]
    gold_te = [gold[i] for i in te_idx]
    pred_bands = [score_to_priority(float(v)) for v in p_reg]
    binary = golden_metrics.compute_binary(gold_te, pred_bands).as_dict()
    print(f"temporal regression     : Spearman={spearmanr(y_te, p_reg).statistic:.3f}  "
          f"NDCG@10={_ndcg(y_te, p_reg, 10):.3f}  NDCG@20={_ndcg(y_te, p_reg, 20):.3f}  "
          f"keep-F1={binary['f1']:.3f}")

    # --- 3. Temporal holdout, LambdaRank.
    p_rank = _fit_ranker(X[tr_idx], y[tr_idx], X[te_idx], sw[tr_idx])
    print(f"temporal lambdarank     : Spearman={spearmanr(y_te, p_rank).statistic:.3f}  "
          f"NDCG@10={_ndcg(y_te, p_rank, 10):.3f}  NDCG@20={_ndcg(y_te, p_rank, 20):.3f}  "
          f"(scale-free: no banding)")


if __name__ == "__main__":
    main()
