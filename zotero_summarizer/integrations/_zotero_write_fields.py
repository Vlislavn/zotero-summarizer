"""Single-field write method of ZoteroWriter (mixin).

Split from ``_zotero_write_items`` to keep it ≤500 LOC. ``_apply_set_field`` reuses
the shared ``_get_field_id`` / ``_upsert_item_data_value`` helpers (resolved via the
ZoteroWriter MRO from the item-write mixin).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from zotero_summarizer.integrations._zotero_write_common import (
    ZoteroWriteError,
    resolve_user_library_item_id,
)


class ZoteroFieldWriteMixin:
    def _apply_set_field(
        self,
        conn: sqlite3.Connection,
        *,
        item_key: str,
        payload: dict[str, Any],
        item_data_columns: set[str],
        item_columns: set[str],
    ) -> None:
        """Set (or clear) ONE Zotero item field by name on an EXISTING item.

        Upsert semantics: replaces any prior value for ``(item, field)``; an empty
        value clears it. Used to stamp the goal-blended queue rank into a sortable
        field (Call Number) so the app's order is reproducible inside Zotero.

        Bumps the parent item's ``dateModified``/``version``/``synced`` (via the
        shared ``_touch_item``) like every other write path, so a running Zotero
        notices the change and zotero.org sync doesn't revert it as a phantom edit."""
        field_name = str(payload.get("field") or "").strip()
        if not field_name:
            raise ZoteroWriteError("set_field payload requires 'field'")
        if not {"itemID", "fieldID", "valueID"}.issubset(item_data_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required itemData columns missing")
        item_id = resolve_user_library_item_id(conn, item_key)
        field_id = self._get_field_id(conn, field_name)
        if field_id is None:
            raise ZoteroWriteError(f"set_field: unknown Zotero field {field_name!r}")
        # Replace any existing value for this (item, field) — itemData PK is
        # (itemID, fieldID), so delete-then-insert is the portable upsert.
        conn.execute("DELETE FROM itemData WHERE itemID = ? AND fieldID = ?", (item_id, field_id))
        value = str(payload.get("value") or "").strip()
        if value:
            value_id = self._upsert_item_data_value(conn, value)
            conn.execute(
                "INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
                (item_id, field_id, value_id),
            )
        self._touch_item(conn, item_id, item_columns)
