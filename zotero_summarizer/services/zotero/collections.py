"""Zotero collection picker helpers shared by the services that file items."""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations.zotero_read import ZoteroReader


def resolve_collection_name(collection_key: str | None, zotero_data_dir: str | Path) -> str:
    """Picker key → user-library collection name (default "Inbox" when unset). An
    unknown/foreign key is a 400 — a raw key must never reach the name-based writer,
    which would auto-create a junk collection named after it."""
    if not collection_key:
        return "Inbox"
    name = ZoteroReader(zotero_data_dir).collection_name_for_key(collection_key)
    if not name:
        raise APIError(
            error="invalid_collection",
            message=f"no such collection: {collection_key}",
            status_code=400,
        )
    return name
