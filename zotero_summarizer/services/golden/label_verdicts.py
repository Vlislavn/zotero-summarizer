"""Current-label commands that also preserve deliberate user transitions."""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK, VERDICT_SOURCE_USER
from zotero_summarizer.services import interaction_log
from zotero_summarizer.storage import repositories


def log_committed_transition(
    *, item_key: str, previous: dict, new_user_priority: str | None,
    surface: str, source: str, comment: str = "", model_priority: str | None = None,
    history_known: bool = True,
) -> None:
    """Emit the shared #13 transition after another command committed its row."""
    prior_user = (
        previous.get("user_priority", previous.get("value"))
        if previous.get("source") == VERDICT_SOURCE_USER else None
    )
    interaction_log._log_label_transition(
        item_key=item_key, previous_user_priority=prior_user,
        new_user_priority=new_user_priority,
        model_priority=model_priority or previous.get("original_derived_priority")
        or previous.get("model_priority"), surface=surface,
        previous_source=previous.get("source"), source=source, comment=comment,
        history_known=history_known or prior_user is not None,
    )


def set_label_verdict(
    db_path: Path,
    *,
    item_key: str,
    user_priority: str,
    surface: str,
    original_derived_priority: str | None = None,
    comment: str = "",
    source: str = VERDICT_SOURCE_USER,
    event_source: str | None = None,
    transition_comment: str | None = None,
    history_known: bool = True,
) -> int:
    """Commit the current snapshot, then append a human transition if applicable.

    Machine writes never replace a deliberate user verdict. ``history_known`` is
    false when a Zotero label is merely first observed on this device; no prior
    human label is reconstructed from the model/provenance field.
    """
    existing = repositories.get_label_verdict(db_path, item_key)
    stored_key = str(existing.get("item_key") or item_key) if existing is not None else item_key
    if (
        source != VERDICT_SOURCE_USER
        and existing is not None
        and existing.get("source") == VERDICT_SOURCE_USER
    ):
        return int(existing["id"])

    stored_original = (
        original_derived_priority
        or (existing or {}).get("original_derived_priority")
        or "unknown"
    )
    model_priority = stored_original
    if model_priority not in READING_PRIORITY_SORT_RANK and existing is not None:
        model_priority = existing.get("original_derived_priority")
    row_id = repositories.insert_or_update_label_verdict(
        db_path,
        item_key=stored_key,
        original_derived_priority=stored_original,
        user_priority=user_priority,
        comment=comment,
        source=source,
    )
    if source == VERDICT_SOURCE_USER:
        log_committed_transition(
            item_key=stored_key,
            new_user_priority=user_priority,
            previous=existing or {}, model_priority=model_priority,
            surface=surface,
            source=event_source or source,
            comment=comment if transition_comment is None else transition_comment,
            history_known=history_known,
        )
    return row_id


def retract_label_verdict(
    db_path: Path, *, item_key: str, surface: str, event_source: str = VERDICT_SOURCE_USER,
) -> bool:
    """Delete the current snapshot and record a prior human label as retracted."""
    existing = repositories.get_label_verdict(db_path, item_key)
    if existing is None:
        return False
    stored_key = existing["item_key"]
    deleted = repositories.delete_label_verdict(db_path, stored_key)
    if deleted and existing.get("source") == VERDICT_SOURCE_USER:
        log_committed_transition(
            item_key=stored_key,
            new_user_priority=None,
            previous=existing,
            surface=surface,
            source=event_source,
        )
    return deleted
