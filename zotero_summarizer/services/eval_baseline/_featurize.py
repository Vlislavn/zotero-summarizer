"""Featurise the golden CSV for eval_baseline.

Mirrors classifier.cross_validate's featurization path but exposes the
continuous label (``gold_inferred_relevance``) and the 4-class priority
alongside the binary target. Reuses ``classifier`` helpers verbatim so the
features we evaluate match what the live model sees.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from zotero_summarizer.services import classifier


@dataclass
class FeaturizedGolden:
    """Featurized + labeled training matrix, ready for K-fold CV."""

    X: np.ndarray          # (n, FEATURE_DIM) float32
    y_binary: np.ndarray   # (n,) int — 1 if must/should, else 0
    y_continuous: np.ndarray  # (n,) float — gold_inferred_relevance ∈ [1,5]
    y_priority: list[str]
    item_keys: list[str]
    n_features: int


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
    for r in rows:
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

    embed_cache, openalex_client = classifier._build_aux_providers(
        corpus_db_path, goals_config,
    )
    X = np.zeros((n, classifier.FEATURE_DIM), dtype=np.float32)
    for i, (k, t, a) in enumerate(zip(keys, titles, abstracts)):
        authors = (selected[i].get("authors") or "").strip()
        venue = (selected[i].get("venue") or "").strip()
        X[i, :classifier.EMBEDDING_DIM] = classifier.get_or_compute_embedding(
            corpus_db_path, k, t, a, authors=authors, venue=venue,
        )
        year_str = (selected[i].get("year") or "").strip()
        year_i = int(year_str[:4]) if year_str[:4].isdigit() else None
        doi = (selected[i].get("doi") or "").strip()
        affinity, prestige = classifier._compute_aux(
            embed_cache, openalex_client,
            title=t, abstract=a, doi=doi, year=year_i,
        )
        X[i, classifier.EMBEDDING_DIM:] = classifier._extra_features(
            selected[i], t, a,
            corpus_affinity=affinity, prestige_score=prestige,
        )
        if progress_cb is not None and (i + 1) % 50 == 0:
            progress_cb(i + 1, n)
    return FeaturizedGolden(
        X=X,
        y_binary=np.asarray(y_binary, dtype=np.int32),
        y_continuous=np.asarray(y_continuous, dtype=np.float64),
        y_priority=y_priority,
        item_keys=keys,
        n_features=classifier.FEATURE_DIM,
    )


def load_golden_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the golden CSV, returning a list of row dicts. Fail-fast on missing."""
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV not found at {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
