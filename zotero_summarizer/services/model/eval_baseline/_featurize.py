"""Featurise the golden CSV for eval_baseline.

Mirrors classifier.cross_validate's featurization path but exposes the
continuous label (``gold_inferred_relevance``) and the 4-class priority
alongside the binary target. Reuses ``classifier`` helpers verbatim so the
features we evaluate match what the live model sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from zotero_summarizer.services._common import load_golden_rows
from zotero_summarizer.services.model import classifier


@dataclass
class FeaturizedGolden:
    """Featurized + labeled training matrix, ready for K-fold CV."""

    X: np.ndarray          # (n, FEATURE_DIM) float32
    y_binary: np.ndarray   # (n,) int — 1 if must/should, else 0
    y_continuous: np.ndarray  # (n,) float — gold_inferred_relevance ∈ [1,5]
    y_priority: list[str]
    item_keys: list[str]
    n_features: int
    sample_weights: np.ndarray | None = None  # (n,) float — per-row confidence
    # The eligible rows aligned with X — kept so per-fold CV can rebuild the
    # positive-set P from train rows only (leakage-free) and group by paper id.
    selected_rows: list[dict[str, str]] | None = None


def featurize_golden(
    rows: list[dict[str, str]],
    *,
    corpus_db_path: Path,
    goals_config: Any,
    progress_cb: Callable[[int, int], None] | None = None,
) -> FeaturizedGolden:
    """Build (X, y) from golden CSV rows, matching ``classifier.cross_validate``."""
    keys: list[str] = []
    titles: list[str] = []
    abstracts: list[str] = []
    y_binary: list[int] = []
    y_continuous: list[float] = []
    y_priority: list[str] = []
    selected: list[dict[str, str]] = []
    from zotero_summarizer.domain import is_training_eligible

    for r in rows:
        # Hygiene cut: F5 (in_trash) + Sprint-1 tier filter (first_glance, meta).
        # Matches production training in classifier.predict_new_items and
        # classifier_persistence.train_and_save.
        if not is_training_eligible(r):
            continue
        gold = (r.get("gold_priority_final") or "").strip()
        title = (r.get("title") or "").strip()
        abstract = (r.get("abstract") or "").strip()
        if not gold or not title or not abstract:
            continue
        rel_str = (r.get("gold_inferred_relevance") or "").strip()
        if not rel_str:
            continue
        try:
            rel = float(rel_str)
        except ValueError:
            raise ValueError(
                f"row {r.get('item_key')!r} has unparseable "
                f"gold_inferred_relevance={rel_str!r}"
            )
        keys.append(r.get("item_key", ""))
        titles.append(title)
        abstracts.append(abstract)
        y_binary.append(1 if gold in classifier.POSITIVE_CLASSES else 0)
        y_continuous.append(rel)
        y_priority.append(gold)
        selected.append(r)

    n = len(keys)
    if n < 50:
        raise ValueError(
            f"need at least 50 labeled rows with non-empty inferred_relevance "
            f"for repeated CV; got {n}"
        )

    embed_cache, openalex_client, cold_start_policy = classifier._build_aux_providers(
        corpus_db_path, goals_config,
    )
    from zotero_summarizer.services.model.library_features import (
        compute_library_features,
        load_positive_library_from_rows,
    )
    library = load_positive_library_from_rows(rows, corpus_db_path)
    X = np.zeros((n, classifier.FEATURE_DIM), dtype=np.float32)
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        emb = classifier.get_or_compute_embedding(corpus_db_path, k, t, a)
        X[i, :classifier.EMBEDDING_DIM] = emb
        year_str = (selected[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (selected[i].get("doi") or "").strip()
        affinity, prestige = classifier._compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
            cold_start_policy=cold_start_policy,
        )
        authors_str = (selected[i].get("authors") or "").strip()
        nearest, centroid, recent, drift, authors_overlap = compute_library_features(
            emb, library, candidate_authors=authors_str, exclude_item_key=k,
        )
        X[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
            selected[i], t, a,
            corpus_affinity=affinity, prestige_score=prestige,
            nearest_kept_cosine=nearest, positive_centroid_cosine=centroid,
            recent_centroid_cosine=recent, topic_drift=drift,
            author_overlap_count=authors_overlap,
        )
        if progress_cb is not None and (i + 1) % 50 == 0:
            progress_cb(i + 1, n)
    from zotero_summarizer.services.model.label_weights import compute_row_weights

    weights = compute_row_weights(selected)
    return FeaturizedGolden(
        X=X,
        y_binary=np.asarray(y_binary, dtype=np.int32),
        y_continuous=np.asarray(y_continuous, dtype=np.float64),
        y_priority=y_priority,
        item_keys=keys,
        n_features=classifier.FEATURE_DIM,
        sample_weights=weights,
        selected_rows=selected,
    )
