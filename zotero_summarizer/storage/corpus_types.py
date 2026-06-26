"""Shared corpus value types (leaf module: no deps on corpus/corpus_read)."""
from __future__ import annotations

from dataclasses import dataclass

EMBEDDING_DIM = 384


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
