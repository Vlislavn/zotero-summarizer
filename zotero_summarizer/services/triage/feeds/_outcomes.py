"""feeds: outcome detection — flow user actions back into feedback weights.

Days after an item is materialized, inspect what the user did with it
(engaged / moved / trashed / deleted) and write the asymmetric signal to
`user_feedback` so the corpus engagement weighting picks it up.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services import interaction_log
from zotero_summarizer.storage import feeds as feeds_storage
from zotero_summarizer.services.triage.feeds._common import _triage_conn


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
    resolved = 0
    for row in due:
        item_key = row["materialized_zotero_key"]
        membership = reader.get_item_membership(item_key)
        outcome = _compute_outcome_from_membership(membership)
        weight = feeds_storage.OUTCOME_WEIGHT[outcome]
        with _triage_conn() as conn:
            if not feeds_storage.record_outcome(
                conn,
                feed_library_id=int(row["feed_library_id"]),
                feed_item_id=int(row["feed_item_id"]),
                final_outcome=outcome,
            ):
                continue
            conn.commit()
        # Close the trajectory in the unified event stream: the daemon-resolved
        # behavioural outcome, joined to the at-triage verdict via feed_item_id.
        fid = int(row.get("feed_item_id") or 0)
        interaction_log.log_behavioural_outcome(
            item_key=item_key,
            item_key_kind=interaction_log.key_kind(item_key),
            model={
                "priority": str(row.get("reading_priority") or ""),
                "composite_score": row.get("composite_score"),
                "surprise_score": row.get("surprise_score"),
                "corpus_affinity": row.get("corpus_affinity"),
            },
            outcome=outcome,
            signal_weight=weight,
            stable_id={"feed_item_id": fid or None, "doi": row.get("doi") or None,
                       "arxiv": row.get("arxiv_id") or None},
        )
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
