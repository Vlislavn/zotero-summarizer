"""feeds: outcome detection — flow user actions back into feedback weights.

Days after an item is materialized, inspect what the user did with it
(engaged / moved / trashed / deleted) and write the asymmetric signal to
`user_feedback` so the corpus engagement weighting picks it up.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.storage import repositories as triage_db
from zotero_summarizer.services.triage.feeds._common import LOGGER, _triage_conn


def _resolve_due_outcomes(
    *,
    reader: ZoteroReader,
    limit: int,
) -> int:
    """Resolve up to `limit` due outcomes. Returns count resolved.

    For each due row (outcome_eligible_at <= now, outcome_detected_at IS NULL,
    materialized_zotero_key NOT NULL):
      - Query Zotero for the item's collections + trash + engagement tags.
      - Compute the outcome label per the OUTCOME_* constants.
      - Write `user_feedback` row with the asymmetric weight.
      - Update `processed_feed_items` with final_outcome + signal_weight.
    """
    with _triage_conn() as conn:
        due = feeds_storage.due_outcome_checks(conn, limit=limit)
    if not due:
        return 0

    resolved = 0
    for row in due:
        item_key = str(row.get("materialized_zotero_key") or "").strip()
        if not item_key:
            continue
        try:
            membership = reader.get_item_membership(item_key)
        except Exception as exc:
            LOGGER.warning("get_item_membership failed for %s: %s", item_key, exc)
            continue
        outcome = _compute_outcome_from_membership(membership)
        weight = feeds_storage.OUTCOME_WEIGHT.get(outcome, 0.0)
        with _triage_conn() as conn:
            feeds_storage.record_outcome(
                conn,
                feed_library_id=int(row.get("feed_library_id") or 0),
                feed_item_id=int(row.get("feed_item_id") or 0),
                final_outcome=outcome,
                signal_weight=weight,
            )
            conn.commit()
        # Push to user_feedback so corpus.py's engagement weighting can pick
        # it up on the next refresh. (Done outside the feeds-storage conn
        # because insert_feedback_events uses its own connection via _get_conn.)
        try:
            triage_db.insert_feedback_events(
                [
                    {
                        "item_id": item_key,
                        "feedback_type": _feedback_type_from_outcome(outcome),
                        "signal": f"feed_outcome:{outcome}",
                        "original_priority": str(row.get("reading_priority") or ""),
                        "inferred_relevance": _relevance_from_weight(weight),
                    }
                ]
            )
        except Exception:
            LOGGER.exception("insert_feedback_events failed for %s", item_key)
        resolved += 1
    return resolved


def _compute_outcome_from_membership(membership: dict[str, Any]) -> str:
    """Reduce a ZoteroReader membership dict to one of the OUTCOME_* labels.

    Precedence (strongest signal first):
      1. has_engagement_tag (🧠/👀) -> OUTCOME_ENGAGED (+3)
      2. is_trashed                  -> OUTCOME_TRASHED (-3)
      3. !exists                     -> OUTCOME_UNKNOWN (-1, hard-delete)
      4. zero collections            -> OUTCOME_DELETED_ALL (-3)
      5. has collections, !is_in_inbox -> OUTCOME_MOVED_COLLECTION (+1)
      6. only Inbox membership       -> OUTCOME_KEPT_INBOX (-0.5)

    The engagement check wins over trash (a user who tagged 🧠 then trashed
    later still gave a strong positive signal earlier — we surface the
    positive). The corpus engagement signal handles the trash separately.
    """
    if membership.get("has_engagement_tag"):
        return feeds_storage.OUTCOME_ENGAGED
    if not membership.get("exists"):
        return feeds_storage.OUTCOME_UNKNOWN
    if membership.get("is_trashed"):
        return feeds_storage.OUTCOME_TRASHED
    collection_keys = membership.get("collection_keys") or []
    if not collection_keys:
        return feeds_storage.OUTCOME_DELETED_ALL
    if membership.get("is_in_inbox") and len(collection_keys) == 1:
        return feeds_storage.OUTCOME_KEPT_INBOX
    return feeds_storage.OUTCOME_MOVED_COLLECTION


def _feedback_type_from_outcome(outcome: str) -> str:
    """Map outcome -> existing user_feedback type vocabulary."""
    if outcome in (feeds_storage.OUTCOME_ENGAGED, feeds_storage.OUTCOME_MOVED_COLLECTION):
        return "implicit_engagement"
    if outcome in (feeds_storage.OUTCOME_DELETED_ALL, feeds_storage.OUTCOME_TRASHED, feeds_storage.OUTCOME_UNKNOWN):
        return "implicit_negative_strong"
    return "implicit_weak_negative"


def _relevance_from_weight(weight: float) -> float:
    """Map signal_weight (-3..+3) to inferred_relevance scale (1..5).

    Delegates to the single shared definition next to ``OUTCOME_WEIGHT`` so the
    feedback emitter and the training-label outcome correction can't drift.
    """
    return feeds_storage.relevance_from_signal_weight(weight)
