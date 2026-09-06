"""Shared corpus value types (leaf module: no deps on corpus/corpus_read)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from zotero_summarizer.domain import normalize_doi


def _paper_identity(title: str, doi: str) -> tuple[str, str]:
    return normalize_doi(doi), " ".join(title.lower().split())


def _same_paper(left: tuple[str, str], right: tuple[str, str]) -> bool:
    """Known DOIs win; missing DOI (including legacy rows) uses normalized title."""
    if left[0] and right[0]:
        return left[0] == right[0]
    return bool(left[1]) and left[1] == right[1]


def _weighted_affinity(similarities: np.ndarray, weights: np.ndarray) -> np.ndarray:
    means = []
    for signed in (weights, -weights):
        positive = np.maximum(signed, 0)
        denominator = positive.sum(axis=-1)
        numerator = (similarities * positive).sum(axis=-1)
        means.append(np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0))
    return np.clip(means[0] - means[1], -1, 1)


@dataclass(frozen=True)
class CorpusAffinity:
    """Per-run candidate×corpus snapshot; no model, connection or shared-cache mutation."""

    similarities: np.ndarray
    weights: np.ndarray
    same_paper: np.ndarray

    def scores(self, train_idx: np.ndarray | None = None) -> np.ndarray:
        allowed = np.ones(len(self.weights), dtype=bool)
        if train_idx is not None:
            held_out = np.ones(len(self.similarities), dtype=bool)
            held_out[train_idx] = False
            # A legacy title can match both sides: held-out exclusion wins.
            allowed = self.same_paper[train_idx].any(axis=0) & ~self.same_paper[held_out].any(axis=0)
        weights = np.where(allowed & ~self.same_paper, self.weights, 0)
        return np.round(_weighted_affinity(self.similarities, weights), 4)

    def subset(self, indices: np.ndarray) -> CorpusAffinity:
        return CorpusAffinity(self.similarities[indices], self.weights, self.same_paper[indices])


@dataclass
class CorpusMatchResult:
    has_corpus: bool
    affinity_score: float
    positive_similarity: float
    negative_similarity: float
    matched_goal: str
    matched_goal_similarity: float
    suggested_collections: list[str]
    top_similar_items: list[str]
