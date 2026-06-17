"""Shared constants + dataclasses for the relabel-audit pipeline.

Kept dependency-free so that route modules and tests can import just the
shapes without pulling in numpy/scipy/sklearn (the metric module pulls
those when imported).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from zotero_summarizer.domain import PRIORITY_TO_RELEVANCE
from zotero_summarizer.services._common import now_iso_z as now_iso

AUDIT_PRIORITY_NAMES = ("dont_read", "could_read", "should_read", "must_read")
# Single source of truth (domain) — kept under the legacy name for importers.
PRIORITY_TO_SCORE = dict(PRIORITY_TO_RELEVANCE)
AGE_BUCKET_EDGES = (90, 180, 365, 730)  # days_since_added thresholds
AGE_BUCKET_NAMES = ("90-180", "180-365", "365-730", ">730")
DEFAULT_SAMPLE_SIZE = 100
MIN_PER_CLASS = 5
SAMPLING_SEED = 42


@dataclass
class AuditCandidate:
    """One paper queued for blind re-label."""

    item_key: str
    title: str
    authors: str
    venue: str
    abstract: str
    days_since_added: int
    age_bucket: str
    original_priority: str
    original_inferred_relevance: float


@dataclass
class AuditResponse:
    """One paired (original, new) verdict from the user."""

    item_key: str
    original_priority: str
    original_inferred_relevance: float
    new_priority: str
    new_relevance: float
    timestamp_iso: str
    age_bucket: str


@dataclass
class AuditMetrics:
    """Reliability metrics computed from N paired responses."""

    n_paired: int
    cohen_kappa: float
    cohen_kappa_weighted: float
    icc_2_1: float
    pearson_r: float
    spearman_rho: float
    by_age_bucket: dict[str, float]
    by_class: dict[str, float]
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AUDIT_PRIORITY_NAMES",
    "PRIORITY_TO_SCORE",
    "AGE_BUCKET_EDGES",
    "AGE_BUCKET_NAMES",
    "DEFAULT_SAMPLE_SIZE",
    "MIN_PER_CLASS",
    "SAMPLING_SEED",
    "AuditCandidate",
    "AuditResponse",
    "AuditMetrics",
    "now_iso",
]
