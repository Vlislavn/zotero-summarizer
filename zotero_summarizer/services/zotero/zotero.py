from __future__ import annotations

import asyncio
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.models import (
    ZoteroCollectionsResponse,
    ZoteroItemCollectionUpdateRequest,
    ZoteroItemPriorityUpdateRequest,
    ZoteroItemsResponse,
    ZoteroItemTagUpdateRequest,
    ZoteroStatusResponse,
)
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.corpus import refresh_corpus_items_by_keys
from zotero_summarizer.services.zotero.pending import (
    build_priority_tag_change,
    effective_tag_payload,
    normalize_pending_tag_payload,
)


def get_zotero_reader_or_raise() -> ZoteroReader:
    app_state = state()
    reader: ZoteroReader | None = getattr(app_state, "zotero_reader", None)
    if reader is None:
        error_message = getattr(app_state, "zotero_error", "Zotero library is not configured")
        raise APIError(error="zotero_unavailable", message=error_message, status_code=503)
    return reader


def get_zotero_writer_or_raise() -> ZoteroWriter:
    app_state = state()
    writer: ZoteroWriter | None = getattr(app_state, "zotero_writer", None)
    if writer is None:
        error_message = getattr(app_state, "zotero_error", "Zotero write access is unavailable")
        raise APIError(error="zotero_unavailable", message=error_message, status_code=503)
    return writer


def zotero_status_payload() -> ZoteroStatusResponse:
    app_state = state()
    reader: ZoteroReader | None = getattr(app_state, "zotero_reader", None)
    error_message = str(getattr(app_state, "zotero_error", "") or "")
    data_dir = str(getattr(reader, "data_dir", "") or settings().zotero_data_dir)
    db_path = str(getattr(reader, "db_path", "") or (settings().zotero_data_dir / "zotero.sqlite"))
    stats: dict[str, Any] = {}
    available = reader is not None

    if reader is not None:
        try:
            stats = reader.get_library_stats()
        except Exception as exc:
            available = False
            error_message = str(exc)

    return ZoteroStatusResponse(
        available=available,
        data_dir=data_dir,
        db_path=db_path,
        stats=stats,
        error=error_message,
    )


async def zotero_status() -> ZoteroStatusResponse:
    return await asyncio.to_thread(zotero_status_payload)


async def zotero_collections() -> ZoteroCollectionsResponse:
    reader = get_zotero_reader_or_raise()
    items = await asyncio.to_thread(reader.get_collections)
    return ZoteroCollectionsResponse(items=items)


async def zotero_tags(limit: int = 500) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    items = await asyncio.to_thread(reader.get_tags, limit)
    return {"items": items}


async def zotero_items(
    collection: str | None = None,
    search: str | None = None,
    tag: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> ZoteroItemsResponse:
    reader = get_zotero_reader_or_raise()
    result = await asyncio.to_thread(reader.get_items, collection, search, tag, limit, offset)
    return ZoteroItemsResponse.model_validate(result)


async def zotero_item_detail(item_key: str) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    detail = await asyncio.to_thread(reader.get_item_detail, item_key)
    if detail is None:
        raise APIError(error="not_found", message="Item not found", status_code=404)
    return detail


async def zotero_set_item_priority(item_key: str, req: ZoteroItemPriorityUpdateRequest) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(error="validation_error", message="item_key is required", status_code=422)

    detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
    if detail is None:
        raise APIError(error="not_found", message="Item not found", status_code=404)

    if writer.is_connector_running() and not req.force:
        return {
            "error": "zotero_running",
            "message": "Zotero appears to be running; close Zotero or confirm force apply.",
            "requires_force": True,
        }

    current_tags = [str(tag or "").strip() for tag in (detail.get("tags") or []) if str(tag or "").strip()]
    payload = build_priority_tag_change(current_tags, req.priority)
    if not payload["add_tags"] and not payload["remove_tags"]:
        refreshed_detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
        return {
            "updated": 0,
            "item_key": safe_item_key,
            "priority": req.priority,
            "message": "Priority tag already set",
            "item": refreshed_detail,
        }

    result = await asyncio.to_thread(
        writer.apply_changes,
        [{"id": 0, "item_key": safe_item_key, "change_type": "tag_changes", "payload_json": payload}],
        True,
    )
    failed = list(result.get("failed") or [])
    if failed:
        first_error = str(failed[0].get("error") or "Failed to update item priority")
        raise APIError(error="zotero_write_failed", message=first_error, status_code=500)

    await refresh_corpus_items_by_keys([safe_item_key])
    refreshed_detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
    return {
        "updated": 1,
        "item_key": safe_item_key,
        "priority": req.priority,
        "add_tags": payload["add_tags"],
        "remove_tags": payload["remove_tags"],
        "item": refreshed_detail,
    }


async def zotero_update_item_tags(item_key: str, req: ZoteroItemTagUpdateRequest) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(error="validation_error", message="item_key is required", status_code=422)

    detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
    if detail is None:
        raise APIError(error="not_found", message="Item not found", status_code=404)

    if writer.is_connector_running() and not req.force:
        return {
            "error": "zotero_running",
            "message": "Zotero appears to be running; close Zotero or confirm force apply.",
            "requires_force": True,
        }

    current_tags = [str(tag or "").strip() for tag in (detail.get("tags") or []) if str(tag or "").strip()]
    payload = normalize_pending_tag_payload({"add_tags": list(req.add_tags), "remove_tags": list(req.remove_tags)})
    effective_payload = effective_tag_payload(current_tags, payload)
    if not effective_payload["add_tags"] and not effective_payload["remove_tags"]:
        refreshed_detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
        return {
            "updated": 0,
            "item_key": safe_item_key,
            "message": "Tag update has no net changes",
            "add_tags": [],
            "remove_tags": [],
            "item": refreshed_detail,
        }

    result = await asyncio.to_thread(
        writer.apply_changes,
        [{"id": 0, "item_key": safe_item_key, "change_type": "tag_changes", "payload_json": effective_payload}],
        True,
    )
    failed = list(result.get("failed") or [])
    if failed:
        first_error = str(failed[0].get("error") or "Failed to update item tags")
        raise APIError(error="zotero_write_failed", message=first_error, status_code=500)

    await refresh_corpus_items_by_keys([safe_item_key])
    refreshed_detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
    return {
        "updated": 1,
        "item_key": safe_item_key,
        "add_tags": effective_payload["add_tags"],
        "remove_tags": effective_payload["remove_tags"],
        "item": refreshed_detail,
    }


def zotero_upsert_verdict_note(item_key: str, user_priority: str, comment: str) -> None:
    """Write (or update in place) the user's verdict comment as a Zotero note.

    Direct write — mirrors how tag chips persist immediately. Raises on failure
    so the caller can report it; the verdict itself is already saved upstream and
    must not be blocked by this. Refuses while Zotero is open (DB-lock risk),
    surfacing a clear message rather than corrupting the library.
    """
    from zotero_summarizer.services.zotero.pending import VERDICT_NOTE_MARKER, build_verdict_note_html

    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running():
        raise ZoteroWriteError("Zotero is open; close it to save the verdict note.")
    note_html = build_verdict_note_html(user_priority, comment)
    result = writer.apply_changes(
        [{
            "id": 0,
            "item_key": item_key,
            "change_type": "upsert_note",
            "payload_json": {
                "note_html": note_html,
                "marker": VERDICT_NOTE_MARKER,
                "note_title": f"Verdict: {user_priority}",
            },
        }],
        False,
    )
    failed = list(result.get("failed") or [])
    if failed:
        raise ZoteroWriteError(str(failed[0].get("error") or "verdict note write failed"))


def zotero_upsert_digest_note(item_key: str, digest: Any) -> None:
    """Write (or update in place) the deep-review digest as one Zotero note.

    Direct write mirroring the verdict note. Raises on failure so the deep-review
    job can record it; the in-app digest is unaffected. Refuses while Zotero is
    open (DB-lock risk)."""
    from zotero_summarizer.services.zotero.pending import DIGEST_NOTE_MARKER, build_digest_note_html

    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running():
        raise ZoteroWriteError("Zotero is open; close it to save the digest note.")
    note_html = build_digest_note_html(digest)
    result = writer.apply_changes(
        [{
            "id": 0,
            "item_key": item_key,
            "change_type": "upsert_note",
            "payload_json": {
                "note_html": note_html,
                "marker": DIGEST_NOTE_MARKER,
                "note_title": "Deep digest",
            },
        }],
        False,
    )
    failed = list(result.get("failed") or [])
    if failed:
        raise ZoteroWriteError(str(failed[0].get("error") or "digest note write failed"))


async def zotero_update_item_collections(item_key: str, req: ZoteroItemCollectionUpdateRequest) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(error="validation_error", message="item_key is required", status_code=422)

    detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
    if detail is None:
        raise APIError(error="not_found", message="Item not found", status_code=404)

    if writer.is_connector_running() and not req.force:
        return {
            "error": "zotero_running",
            "message": "Zotero appears to be running; close Zotero or confirm force apply.",
            "requires_force": True,
        }

    changes: list[dict[str, Any]] = []
    added_targets: list[dict[str, str]] = []
    removed_targets: list[dict[str, str]] = []

    for ref in req.add:
        payload = ref.to_writer_payload()
        changes.append(
            {"id": 0, "item_key": safe_item_key, "change_type": "add_to_collection", "payload_json": payload}
        )
        added_targets.append(payload)

    for ref in req.remove:
        payload = ref.to_writer_payload()
        changes.append(
            {"id": 0, "item_key": safe_item_key, "change_type": "remove_from_collection", "payload_json": payload}
        )
        removed_targets.append(payload)

    if not changes:
        refreshed_detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
        return {
            "updated": 0,
            "item_key": safe_item_key,
            "message": "Collection update has no changes",
            "item": refreshed_detail,
        }

    result = await asyncio.to_thread(writer.apply_changes, changes, True)
    failed = list(result.get("failed") or [])
    if failed:
        first_error = str(failed[0].get("error") or "Failed to update item collections")
        raise APIError(error="zotero_write_failed", message=first_error, status_code=500)

    await refresh_corpus_items_by_keys([safe_item_key])
    refreshed_detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
    return {
        "updated": len(changes),
        "item_key": safe_item_key,
        "added": added_targets,
        "removed": removed_targets,
        "item": refreshed_detail,
    }
