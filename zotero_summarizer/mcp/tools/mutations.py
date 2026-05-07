from __future__ import annotations

from typing import Any, Literal

from zotero_summarizer.mcp.api_client import _api_request, _base_item_update_payload
from zotero_summarizer.mcp.helpers import (
    _error,
    _extract_data_or_error,
    _normalize_unique_strings,
    _ok,
    _require_non_empty_text,
)
from zotero_summarizer.mcp.server import mcp


@mcp.tool()
async def manage_tags(
    item_key: str,
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Add or remove tags on a Zotero item."""
    safe_item_key, validation_error = _require_non_empty_text(item_key, "item_key")
    if validation_error is not None:
        return validation_error

    normalized_add = _normalize_unique_strings(add_tags)
    normalized_remove = _normalize_unique_strings(remove_tags)
    if not normalized_add and not normalized_remove:
        return _error("validation_error", "at least one tag must be added or removed")

    update_result = await _api_request(
        "POST",
        f"/api/zotero/items/{safe_item_key}/tags",
        payload={
            "add_tags": normalized_add,
            "remove_tags": normalized_remove,
            "force": bool(force),
        },
    )
    data, update_error = _extract_data_or_error(update_result)
    if update_error is not None:
        return update_error

    payload = _base_item_update_payload(data, safe_item_key)
    return _ok(
        item_key=payload["item_key"],
        updated=payload["updated"],
        add_tags=list(data.get("add_tags") or []),
        remove_tags=list(data.get("remove_tags") or []),
        message=payload["message"],
        item=payload["item"],
    )


@mcp.tool()
async def manage_collections(
    item_key: str,
    add_collection_paths: list[str] | None = None,
    remove_collection_paths: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Add or remove a paper from Zotero collections."""
    safe_item_key, validation_error = _require_non_empty_text(item_key, "item_key")
    if validation_error is not None:
        return validation_error

    add_paths = _normalize_unique_strings(add_collection_paths)
    remove_paths = _normalize_unique_strings(remove_collection_paths)
    if not add_paths and not remove_paths:
        return _error("validation_error", "at least one collection path must be added or removed")

    payload = {
        "add": [{"collection_path": path} for path in add_paths],
        "remove": [{"collection_path": path} for path in remove_paths],
        "force": bool(force),
    }

    update_result = await _api_request(
        "POST",
        f"/api/zotero/items/{safe_item_key}/collections",
        payload=payload,
    )
    data, update_error = _extract_data_or_error(update_result)
    if update_error is not None:
        return update_error

    payload_base = _base_item_update_payload(data, safe_item_key)
    return _ok(
        item_key=payload_base["item_key"],
        updated=payload_base["updated"],
        added=list(data.get("added") or []),
        removed=list(data.get("removed") or []),
        message=payload_base["message"],
        item=payload_base["item"],
    )


@mcp.tool()
async def set_reading_priority(
    item_key: str,
    priority: Literal["must_read", "should_read", "could_read", "dont_read"],
    force: bool = False,
) -> dict[str, Any]:
    """Set or override reading priority for one paper."""
    safe_item_key, validation_error = _require_non_empty_text(item_key, "item_key")
    if validation_error is not None:
        return validation_error

    update_result = await _api_request(
        "POST",
        f"/api/zotero/items/{safe_item_key}/priority",
        payload={"priority": priority, "force": bool(force)},
    )
    data, update_error = _extract_data_or_error(update_result)
    if update_error is not None:
        return update_error

    payload_base = _base_item_update_payload(data, safe_item_key)
    return _ok(
        item_key=payload_base["item_key"],
        updated=payload_base["updated"],
        priority=str(data.get("priority") or priority),
        add_tags=list(data.get("add_tags") or []),
        remove_tags=list(data.get("remove_tags") or []),
        message=payload_base["message"],
        item=payload_base["item"],
    )
