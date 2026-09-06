"""Idempotent post-commit effects shared by online and offline verdict saves."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.library import review_detail
from zotero_summarizer.services.zotero._notes import VERDICT_NOTE_MARKER
from zotero_summarizer.services.zotero.zotero import (
    get_library_reader,
    get_zotero_reader_or_raise,
    zotero_set_label_tag,
    zotero_upsert_user_note,
    zotero_upsert_verdict_note,
)
from zotero_summarizer.storage import label_mirrors, review_notes

LOGGER = logging.getLogger(__name__)
_POSITIVE_PRIORITIES = ("must_read", "should_read", "could_read")


def source_payload(item_key: str) -> dict[str, Any] | None:
    """Resolve metadata for a feed, note, or library key."""
    config = settings()
    source = review_detail.classify_item_key(item_key)
    if source == review_detail.SOURCE_FEED:
        return review_detail.build_feed_detail_by_key(
            triage_db_path=config.triage_db_path,
            zotero_data_dir=config.zotero_data_dir,
            feed_key=review_detail.parse_feed_item_key(item_key),
        )
    if source == review_detail.SOURCE_NOTE:
        parent_key, note_id = review_detail.parse_note_key(item_key)
        return review_detail.build_note_detail(
            get_zotero_reader_or_raise(),
            parent_key,
            note_id,
        )
    return review_detail.build_library_detail(get_library_reader(), item_key)


def append_training_row(item_key: str, priority: str, comment: str) -> None:
    """Idempotently enrich a verdict with source metadata for training."""
    from zotero_summarizer.services.library import review

    payload = source_payload(item_key)
    if payload is None:
        return
    authors = "; ".join(
        str(author.get("name") or "")
        for author in (payload.get("authors") or [])
        if author.get("name")
    )
    review.append_verdict_to_golden(
        item_key,
        title=str(payload.get("title") or ""),
        abstract=str(payload.get("abstract") or ""),
        priority=priority,
        authors=authors,
        venue=str(payload.get("venue") or ""),
        year=str(payload.get("year") or ""),
        doi=str(payload.get("doi") or ""),
        comment=comment,
    )


def add_feed_verdict_to_library(item_key: str, priority: str) -> dict[str, Any]:
    """Resolve a feed verdict's real Zotero key, creating positive picks only."""
    from zotero_summarizer.services.triage import daily_actions

    if review_detail.classify_item_key(item_key) != review_detail.SOURCE_FEED:
        return {
            "added_to_library": False,
            "add_status": "not_applicable",
            "add_error": None,
        }
    try:
        result = daily_actions.materialize_feed_verdict(
            item_key,
            priority,
            create_if_missing=priority in _POSITIVE_PRIORITIES,
        )
    except Exception as exc:  # noqa: BLE001 - the verdict is already committed
        LOGGER.warning("verdict auto-add to library for %s failed: %s", item_key, exc)
        return {
            "added_to_library": False,
            "add_status": "error",
            "add_error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "added_to_library": bool(result.get("added")),
        "add_status": str(result.get("status") or ""),
        "add_error": None,
        "_zotero_key": result.get("zotero_key"),
    }


def _optional_zotero(exc: BaseException) -> bool:
    return isinstance(exc, APIError) and exc.error == "zotero_unavailable"


def mirror_current_verdict(db_path: Path, item_key: str, *, redeliver: bool = False) -> dict | None:
    """Mirror current label and rationale under one lock; acknowledge deletions."""
    try:
        with label_mirrors.current_label(db_path, item_key, redeliver=redeliver) as desired:
            if desired is None:
                return None
            key, priority = desired["target_key"], desired["value"]
            comment = desired["comment"] if priority is not None else ""
            note_written = bool(comment.strip()) or any(
                VERDICT_NOTE_MARKER in note["note"]
                for note in get_zotero_reader_or_raise().get_item_notes(key)
            )
            if note_written:
                zotero_upsert_verdict_note(key, priority or "", comment)
            if desired["label_pending"]:
                zotero_set_label_tag(key, priority)
        return {"label_written": desired["label_pending"], "note_written": note_written}
    except APIError as exc:
        # Local-first verdicts remain usable without configured Zotero. Leaving
        # the receipt absent keeps an explicit retraction retryable.
        if not _optional_zotero(exc):
            raise
        return None


def apply_verdict_effects(db_path: Path, item_key: str, priority: str, comment: str) -> dict[str, Any]:
    """Run the online/offline training, materialization, and mirror effects.

    Both external mirrors read current state. A stored sync mutation can retry
    a failed delivery without replacing newer labels or rationale.
    """
    try:
        append_training_row(item_key, priority, comment)
    except Exception as exc:  # noqa: BLE001 - enrichment cannot undo the verdict
        LOGGER.warning("golden append for verdict %s failed: %s", item_key, exc)

    add_result = add_feed_verdict_to_library(item_key, priority)
    source = review_detail.classify_item_key(item_key)
    mirror_key = (
        item_key
        if source == review_detail.SOURCE_LIBRARY
        else add_result.pop("_zotero_key", None)
    )
    mirror = None
    if mirror_key:
        mirror = mirror_current_verdict(
            db_path, item_key, redeliver=bool(add_result["added_to_library"]),
        )
    return {
        "label_written": mirror["label_written"] if mirror is not None else False,
        "label_error": None,
        "note_written": mirror["note_written"] if mirror is not None else False,
        "note_error": None,
        **add_result,
    }


def mirror_review_note(db_path: Path, item_key: str) -> dict[str, Any]:
    """Deliver current committed note intent, never a historical request body."""
    source = review_detail.classify_item_key(item_key)
    if source in (review_detail.SOURCE_FEED, review_detail.SOURCE_NOTE):
        return {"note_written": False, "note_error": None}
    try:
        with review_notes.current_for_mirror(db_path, item_key) as note:
            # The app's clear-body contract also applies to sync deletion tombstones.
            zotero_upsert_user_note(item_key, "" if note is None else note)
        return {"note_written": True, "note_error": None}
    except APIError as exc:
        # Unconfigured Zotero is optional for the existing local-first workflow.
        if not _optional_zotero(exc):
            raise
        return {"note_written": False, "note_error": None}
