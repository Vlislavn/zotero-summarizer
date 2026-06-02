"""Decision and outcome taxonomies for ``processed_feed_items``.

Split out of ``storage/feeds.py`` for file-size compliance (<500 LOC each)
and single-responsibility: this module is pure data — no I/O, no SQL. Every
``DECISION_*`` and ``OUTCOME_*`` constant lives here so the rest of the
codebase can import the taxonomy without pulling in the SQL helpers.

Importers should still go through ``from zotero_summarizer.storage import
feeds`` — that module re-exports every name defined here, keeping the
public API stable.
"""
from __future__ import annotations


# Decision taxonomy — keep in sync with services/feeds.py.
DECISION_TRIAGED_PENDING = "triaged_pending"  # LLM-scored, awaiting daily selection
DECISION_SELECTED = "selected"
DECISION_BLACK_SWAN = "black_swan"
DECISION_REJECTED_DAILY_CUTOFF = "rejected_daily_cutoff"  # below daily plateau
DECISION_REJECTED_ELBOW = "rejected_elbow"  # legacy Phase 1 (one-shot batch)
DECISION_REJECTED_LOW_SCORE = "rejected_low_score"  # corpus fast-reject
DECISION_REJECTED_DEDUP_LIBRARY = "rejected_dedup_library"
DECISION_REJECTED_DEDUP_PROCESSED = "rejected_dedup_processed"
DECISION_GATE_REJECTED = "gate_rejected"            # Phase 1.13 classifier gate
DECISION_SKIPPED_ERROR = "skipped_error"

# Phase 1.14 — review-mode states (replaced the old auto-materialize).
# `feeds run` parks items in AWAITING_REVIEW; UI flips to USER_APPROVED →
# pending_changes → Zotero, or USER_REJECTED (terminal, no Zotero write).
DECISION_AWAITING_REVIEW = "awaiting_review"
DECISION_USER_APPROVED = "user_approved"
DECISION_USER_REJECTED = "user_rejected"

# Outcome taxonomy — what the user did with a materialized item after N days.
OUTCOME_PENDING = "pending"  # outcome window not yet elapsed
OUTCOME_KEPT_INBOX = "kept_inbox"  # still in Inbox only — weak negative
OUTCOME_MOVED_COLLECTION = "moved_collection"  # moved out of Inbox to a real collection — weak positive
OUTCOME_DELETED_ALL = "deleted_all"  # removed from every collection — strong negative
OUTCOME_TRASHED = "trashed"  # moved to Zotero trash — strong negative
OUTCOME_ENGAGED = "engaged"  # has 🧠 or 👀 tag — strong positive
OUTCOME_UNKNOWN = "unknown"  # item key resolved to nothing (hard-delete, merge edge case)

# Signal weights — asymmetric per Schnabel et al. ICML 2016
# (Recommendations as Treatments, arXiv:1602.05352). Industrial-feed convention
# (YouTube/Pinterest/Meta) is delete ≈ 3–10× ignore. We sit at 6× (3.0 vs 0.5).
OUTCOME_WEIGHT = {
    OUTCOME_ENGAGED: 3.0,
    OUTCOME_MOVED_COLLECTION: 1.0,
    OUTCOME_KEPT_INBOX: -0.5,
    OUTCOME_DELETED_ALL: -3.0,
    OUTCOME_TRASHED: -3.0,
    OUTCOME_UNKNOWN: -1.0,
}
