from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from zotero_summarizer.domain import ReadingPriority


@dataclass(frozen=True)
class Paper:
    item_key: str
    title: str
    pdf_path: str
    doi: str = ""
    abstract: str = ""


@dataclass(frozen=True)
class TriageDecision:
    relevance_score: int
    composite_score: float
    reading_priority: str = ReadingPriority.COULD_READ.value
    tags: list[str] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class Summary:
    executive_summary: str
    triage: TriageDecision
    sections: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingChange:
    item_key: str
    item_title: str
    change_type: Literal[
        "tag_changes",
        "add_note",
        "add_to_collection",
        "remove_from_collection",
        "create_item_from_feed",
        "promote_from_inbox",
        "mark_feed_item_read",
    ]
    payload: dict[str, Any]


@dataclass(frozen=True)
class TriageJob:
    job_id: str
    status: str
    total: int
    completed: int = 0
    item_keys: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CorpusMatch:
    has_corpus: bool
    affinity_score: float
    positive_similarity: float = 0.0
    negative_similarity: float = 0.0
    matched_goal: str = ""
    matched_goal_similarity: float = 0.0
    suggested_collections: list[str] = field(default_factory=list)
    top_similar_items: list[str] = field(default_factory=list)
