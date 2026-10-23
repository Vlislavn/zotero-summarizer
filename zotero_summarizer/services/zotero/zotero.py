from __future__ import annotations

import asyncio
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.integrations.zotero_write import ZoteroWriter, ZoteroWriteError
from zotero_summarizer.models import (
    ZoteroAttachFiguresRequest,
    ZoteroCollectionsResponse,
    ZoteroItemCollectionUpdateRequest,
    ZoteroItemPriorityUpdateRequest,
    ZoteroItemsResponse,
    ZoteroItemTagUpdateRequest,
    ZoteroStatusResponse,
)
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.corpus import refresh_corpus_items_by_keys
from zotero_summarizer.services.zotero import direct_actions
from zotero_summarizer.services.zotero.pending import build_label_tag_change


def get_zotero_reader_or_raise() -> ZoteroReader:
    app_state = state()
    reader: ZoteroReader | None = getattr(app_state, "zotero_reader", None)
    if reader is None:
        error_message = getattr(app_state, "zotero_error", "Zotero library is not configured")
        raise APIError(error="zotero_unavailable", message=error_message, status_code=503)
    return reader


def get_library_reader(app_state: Any | None = None) -> Any:
    """Resolve the reader for the READ path: the live Zotero reader when a Zotero
    DB is configured, else the app-owned library over kept feed papers
    (:class:`AppLibraryReader`). Both expose ``get_all_items`` / ``get_item_detail``,
    the only surface the reading stack uses. Never raises — the app reader is
    always constructible — so brief / deep-review / ask / the reading queue work
    with Zotero absent. The WRITE path keeps :func:`get_zotero_reader_or_raise`."""
    app_state = app_state or state()
    reader: ZoteroReader | None = getattr(app_state, "zotero_reader", None)
    if reader is not None:
        return reader
    from zotero_summarizer.services.library.app_library_reader import AppLibraryReader

    return AppLibraryReader(settings().triage_db_path)


def resolve_reader_for_key(item_key: str) -> Any:
    """Resolve the reader by the KEY's shape, not by Zotero presence: a
    ``stable_feed_key`` (``feed:<ns>:<sha>``) is an un-materialized Today feed item that
    only the app library can resolve (by stable_feed_key, decision-independent), so it
    routes to :class:`AppLibraryReader` even when a live Zotero reader is configured;
    anything else (a Zotero item_key) routes to :func:`get_library_reader`. This is what
    lets the deep-review/render/detail paths serve an in-place-reviewed feed paper that
    has no Zotero item yet."""
    from zotero_summarizer.storage.feed_identity import is_stable_feed_key

    if is_stable_feed_key(item_key):
        from zotero_summarizer.services.library.app_library_reader import AppLibraryReader

        return AppLibraryReader(settings().triage_db_path)
    return get_library_reader()


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


async def _finish_direct_edit(result: dict[str, Any], reader: ZoteroReader) -> dict[str, Any]:
    if "action_ids" not in result:
        return result  # connector refusal and genuine no-ops retain their existing shape
    try:
        await refresh_corpus_items_by_keys([result["item_key"]])
        detail = await asyncio.to_thread(reader.get_item_detail, result["item_key"])
        if detail is None:
            raise ValueError("Item detail unavailable after native delivery")
    except Exception as exc:
        raise APIError(error="zotero_refresh_failed", status_code=500,
                       message="Native changes were recorded; refreshing the item failed", details={
                           "action_ids": result["action_ids"], "applied_ids": result["action_ids"],
                           "failed_ids": [], "backup_path": result["backup_path"],
                           "delivery_status": result["delivery_status"],
                       }) from exc
    return {**result, "item": detail}


async def zotero_set_item_priority(item_key: str, req: ZoteroItemPriorityUpdateRequest) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    result = await asyncio.to_thread(direct_actions.apply_priority_edit, item_key, req, reader, writer)
    return await _finish_direct_edit(result, reader)


async def zotero_attach_figures(item_key: str, req: ZoteroAttachFiguresRequest) -> dict[str, Any]:
    """Attach the paper's already-extracted figures to its Zotero item as child image
    attachments (they render in Zotero next to the paper). Dedups by filename so a
    re-click never duplicates. Force-gated like the other writes: refuses while Zotero
    is open unless ``req.force`` (a forced write only shows after a Zotero restart)."""
    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(error="validation_error", message="item_key is required", status_code=422)

    detail = await asyncio.to_thread(reader.get_item_detail, safe_item_key)
    if detail is None:
        raise APIError(error="not_found", message="Item not found", status_code=404)

    from zotero_summarizer.services.library.paper_figures import attachable_figures
    figures = await asyncio.to_thread(attachable_figures, safe_item_key)
    # Skip figures already attached (Zotero stores the path as "storage:<filename>").
    existing = {
        str(a.get("stored_path") or "").split("storage:", 1)[-1]
        for a in (detail.get("attachments") or [])
        if str(a.get("stored_path") or "").startswith("storage:")
    }
    pending = [f for f in figures if f["name"] not in existing]
    if not pending:
        message = ("No new figures to attach — they're already on the item"
                   if figures else "No figures generated yet — build the review first.")
        return {"updated": 0, "item_key": safe_item_key, "message": message}

    if writer.is_connector_running() and not req.force:
        return {
            "error": "zotero_running",
            "message": "Zotero appears to be running; close Zotero or confirm force apply.",
            "requires_force": True,
        }

    changes = [
        {"id": 0, "item_key": safe_item_key, "change_type": "add_attachment",
         "payload_json": {"source_path": fig["path"], "filename": fig["name"],
                          "title": fig["name"], "content_type": "image/png"}}
        for fig in pending
    ]
    result = await asyncio.to_thread(writer.apply_changes, changes, True)
    failed = list(result.get("failed") or [])
    if failed:
        first_error = str(failed[0].get("error") or "Failed to attach figures")
        raise APIError(error="zotero_write_failed", message=first_error, status_code=500)
    return {"updated": len(changes), "item_key": safe_item_key, "figures": [fig["name"] for fig in pending]}


async def zotero_update_item_tags(item_key: str, req: ZoteroItemTagUpdateRequest) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    result = await asyncio.to_thread(direct_actions.apply_tag_edit, item_key, req, reader, writer)
    return await _finish_direct_edit(result, reader)


def _apply_mirror_change(item_key: str, change_type: str, payload: dict[str, Any]) -> None:
    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running():
        raise ZoteroWriteError("Zotero is open; close it to save changes.", expected_io=True)
    result = writer.apply_changes(
        [{"id": 0, "item_key": item_key, "change_type": change_type, "payload_json": payload}],
        True,
    )
    if result["failed"]:
        failure = result["failed"][0]
        raise ZoteroWriteError(failure["error"], expected_io=failure.get("expected_io") is True)


def zotero_upsert_verdict_note(item_key: str, user_priority: str, comment: str) -> None:
    """Mirror the verdict comment; refuse an open Zotero, back up, then upsert."""
    from zotero_summarizer.services.zotero.pending import VERDICT_NOTE_MARKER, build_verdict_note_html

    _apply_mirror_change(
        item_key, "upsert_note",
        {"note_html": build_verdict_note_html(user_priority, comment),
         "marker": VERDICT_NOTE_MARKER, "note_title": f"Verdict: {user_priority}"},
    )


def zotero_set_label_tag(item_key: str, priority: str | None) -> None:
    """Mirror a human label (``None`` clears it); preserve unrelated tags."""
    detail = get_zotero_reader_or_raise().get_item_detail(item_key)
    if detail is None:
        raise ZoteroWriteError(f"item {item_key!r} not found in Zotero")
    payload = build_label_tag_change(detail["tags"], priority)
    if payload["add_tags"] or payload["remove_tags"]:
        _apply_mirror_change(item_key, "tag_changes", payload)


def zotero_upsert_digest_note(item_key: str, digest: Any) -> None:
    """Compatibility entry: propose the exact published digest; never auto-write."""
    from zotero_summarizer.services.zotero.generated_notes import stage_current_digest

    stage_current_digest(item_key, digest)


async def zotero_update_item_collections(item_key: str, req: ZoteroItemCollectionUpdateRequest) -> dict[str, Any]:
    reader = get_zotero_reader_or_raise()
    writer = get_zotero_writer_or_raise()
    result = await asyncio.to_thread(direct_actions.apply_collection_edit, item_key, req, reader, writer)
    return await _finish_direct_edit(result, reader)
