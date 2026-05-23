"""Phase 1.16 Step 0.2 + Phase 1.17 Step 3 — relabel-audit pipeline.

The model's Spearman ρ is upper-bounded by the user's self-consistency on
their own labels. We sample N=100 papers stratified across age + class,
hide the original verdict, ask the user to re-label them blind, and
compute Cohen's κ + ICC(2,1) + Pearson r against the original.

The Pearson r is the empirical upper bound for any model's Spearman ρ on
this task (Hooker et al. 2019; McHugh 2012; Koo & Li 2016).

Sample-size justification (statistical-analysis skill: Bujang & Baharum
2017): n=100 gives ≥80% power to detect κ ≥ 0.6 vs null at α=0.05 for a
4-class scheme.

Sampling stratification (avoid temporal confounding from the 180-day
decay): 25 papers from each of 4 ``days_since_added`` buckets, with ≥15
papers per priority class.

Phase 1.17 Step 3 adds a daily-trickle picker (:func:`next_audit_for_today`)
that surfaces 1-2 unanswered audit cards per day, gated on rate-limit.

Public API (re-exported so callers can use
``from zotero_summarizer.services.golden import relabel_audit``):
"""
from __future__ import annotations

# Constants + dataclasses + tiny helpers.
from zotero_summarizer.services.golden.relabel_audit._constants import (
    AGE_BUCKET_EDGES,
    AGE_BUCKET_NAMES,
    AUDIT_PRIORITY_NAMES,
    DEFAULT_SAMPLE_SIZE,
    MIN_PER_CLASS,
    PRIORITY_TO_SCORE,
    SAMPLING_SEED,
    AuditCandidate,
    AuditMetrics,
    AuditResponse,
)
# Private alias kept for tests that imported the old internal symbol.
from zotero_summarizer.services.golden.relabel_audit._constants import now_iso as _now_iso

# Sampling.
from zotero_summarizer.services.golden.relabel_audit._sampling import (
    _build_candidate,  # re-exported: existing tests reach the private helper
    is_eligible_row,
    load_golden_rows,
    sample_stratified,
)

# Session I/O.
from zotero_summarizer.services.golden.relabel_audit._session import (
    read_session,
    record_response,
    responses_from_session,
    write_session,
)

# Metrics.
from zotero_summarizer.services.golden.relabel_audit._metrics import (
    _icc_2_1,  # re-exported: existing tests reach the private helper
    compute_metrics,
    metrics_to_dict,
)

# Phase 1.17 Step 3 trickle.
from zotero_summarizer.services.golden.relabel_audit._trickle import next_audit_for_today

__all__ = [
    # constants
    "AGE_BUCKET_EDGES",
    "AGE_BUCKET_NAMES",
    "AUDIT_PRIORITY_NAMES",
    "DEFAULT_SAMPLE_SIZE",
    "MIN_PER_CLASS",
    "PRIORITY_TO_SCORE",
    "SAMPLING_SEED",
    # dataclasses
    "AuditCandidate",
    "AuditMetrics",
    "AuditResponse",
    # sampling
    "is_eligible_row",
    "load_golden_rows",
    "sample_stratified",
    # session I/O
    "read_session",
    "record_response",
    "responses_from_session",
    "write_session",
    # metrics
    "compute_metrics",
    "metrics_to_dict",
    # trickle (Phase 1.17 Step 3)
    "next_audit_for_today",
]
