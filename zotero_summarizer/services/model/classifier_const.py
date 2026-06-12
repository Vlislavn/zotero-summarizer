"""Classifier constants + result types (leaf module)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field  # noqa: F401
from typing import Any  # noqa: F401

LOGGER = logging.getLogger(__name__)


SPECTER2_MODEL_NAME = "allenai/specter2_base"
# Sprint-3a (May 2026): load the `proximity` adapter on top of SPECTER2 base.
# The proximity adapter (Singh et al. 2023, allenai/specter2) is fine-tuned
# for nearest-neighbour retrieval — exactly the geometry our P-set features
# need. Adapter switch invalidates every cached embedding; the content_hash
# below is keyed by adapter name so the change auto-busts the cache.
SPECTER2_ADAPTER_NAME = "allenai/specter2"
EMBEDDING_DIM = 768
# Extras layout (12 dims after Sprint 2):
#   0  has_doi                  binary
#   1  has_venue                binary
#   2  year_recency             int 0..20
#   3  title_log_len            log1p(len)
#   4  abstract_log_len         log1p(len)
#   5  corpus_affinity          engagement-weighted pos−neg cosine to the ENGAGED
#                               library (corpus_read.affinity_and_goals) — NOT goal
#                               match; the goal-text signal (goal_sims) is
#                               aux-context only, deliberately not a feature
#   6  prestige_score           OpenAlex field-normalized citation percentile → [1, 5]
#   7  nearest_kept_cosine      max cosine to positive-engagement subset P
#   8  positive_centroid_cosine cosine to mean(P)
#   9  recent_centroid_cosine   cosine to mean(P ∩ last 90d)
#  10  topic_drift              recent − all-time centroid cosine
#  11  author_overlap_count     surname overlap with P authors, [0, 5]
N_EXTRA_FEATURES = 12
FEATURE_DIM = EMBEDDING_DIM + N_EXTRA_FEATURES
POSITIVE_CLASSES = frozenset({"must_read", "should_read"})
CURRENT_YEAR = 2026          # for `year_recency` feature; bump or compute dynamically

# TabPFN runs on CPU, never the shared GPU pool. Unlike the persistent ~0.5 GB
# SPECTER2 encoder (which earns its MPS slot — see classifier_embed._select_device),
# TabPFN is re-fit in-context on *every* predict, so there is no warm GPU model to
# amortise, over a tiny context (~500 rows × ~112 PCA feats) that costs only ~3–4 s
# on CPU. Meanwhile the encoder + bge reranker already saturate the Apple-Silicon
# MPS pool, so a `device="auto"` TabPFN became the third uncoordinated claimant and
# OOM'd mid-scoring (TabPFNMPSOutOfMemoryError) even with its own memory_saving_mode
# batching active. Pinning CPU removes the contention outright — no fixed memory
# ceiling to overrun, deterministic, fast enough at this scale, and covering both
# the regressor and classifier constructors. (Longer term: a shared device-policy
# primitive all three local models route through.)
TABPFN_DEVICE = "cpu"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS specter2_embeddings (
    item_key      TEXT PRIMARY KEY,
    content_hash  TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    computed_at   TEXT DEFAULT (datetime('now'))
);
"""


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



__all__ = [
    "LOGGER",
    "SPECTER2_MODEL_NAME",
    "SPECTER2_ADAPTER_NAME",
    "EMBEDDING_DIM",
    "N_EXTRA_FEATURES",
    "FEATURE_DIM",
    "POSITIVE_CLASSES",
    "CURRENT_YEAR",
    "TABPFN_DEVICE",
    "_SCHEMA",
    "ClassifierReport",
    "FeedPrediction",
]
