"""Add a stored PDF attachment to an existing Zotero item (writer mixin).

Split from ``_zotero_write_items`` to keep both ≤500 LOC. Creates a native
Zotero **imported_url** attachment (the same shape Zotero's own "Find Available
PDF" produces): an ``attachment``-type item + an ``itemAttachments`` row +
``itemData`` (title/url/accessDate) + the PDF file copied under
``<dataDir>/storage/<KEY>/<filename>``.

Sync-correctness (the user's library syncs to zotero.org): the new attachment is
created with ``synced=0`` and ``itemAttachments.syncState=0`` (TO_UPLOAD) and
``storageHash``/``storageModTime`` left NULL — Zotero computes the hash, indexes
the text, and uploads the file on its next file-sync pass. We never fake the
hash. File is copied BEFORE the rows are inserted, so a rolled-back transaction
leaves only a harmless orphan ``storage/<KEY>`` dir, never a DB row pointing at a
missing file.
"""
from __future__ import annotations

import random
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from zotero_summarizer.integrations._zotero_write_common import ZoteroWriteError

_ATTACHMENT_TYPE = "attachment"
_LINK_MODE_IMPORTED_URL = 1
_SYNC_STATE_TO_UPLOAD = 0
_PDF_CONTENT_TYPE = "application/pdf"


class ZoteroAttachmentWriteMixin:
    def _apply_add_attachment(
        self,
        conn: sqlite3.Connection,
        *,
        item_key: str,
        payload: dict[str, Any],
        item_columns: set[str],
        item_data_columns: set[str],
        item_data_value_columns: set[str],
    ) -> None:
        """Attach a local PDF (``payload['source_path']``) to the parent item
        ``item_key`` as a native imported_url Zotero attachment.

        payload: ``{source_path, filename, source_url, title}``."""
        source_path = Path(str(payload.get("source_path") or "")).expanduser()
        if not source_path.is_file():
            raise ZoteroWriteError(f"add_attachment: source PDF not found: {source_path}")
        if not {"itemID", "fieldID", "valueID"}.issubset(item_data_columns):
            raise ZoteroWriteError("Unsupported Zotero schema: required itemData columns missing")

        parent = conn.execute("SELECT itemID FROM items WHERE key = ? LIMIT 1", (item_key,)).fetchone()
        if parent is None:
            raise ZoteroWriteError(f"add_attachment: parent item {item_key} not found")
        parent_id = int(parent["itemID"])

        type_id = self._get_item_type_id(conn, _ATTACHMENT_TYPE)
        if type_id is None:
            raise ZoteroWriteError("add_attachment: Zotero schema has no 'attachment' item type")
        lib = conn.execute("SELECT libraryID FROM libraries WHERE type='user' LIMIT 1").fetchone()
        if not lib:
            raise ZoteroWriteError("add_attachment: no user library")
        library_id = int(lib["libraryID"])

        filename = self._safe_pdf_filename(payload.get("filename"), fallback="fulltext.pdf")
        att_key = self._new_item_key(conn)

        # File FIRST (orphan-on-rollback is harmless; a missing-file DB row is not).
        dest_dir = self.data_dir / "storage" / att_key
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest_dir / filename)

        now = self._sqlite_timestamp_now()
        item_values: dict[str, Any] = {"itemTypeID": type_id, "libraryID": library_id, "key": att_key}
        if "version" in item_columns:
            item_values["version"] = 0  # new, unsynced — server assigns on upload
        if "synced" in item_columns:
            item_values["synced"] = 0
        for col in ("dateAdded", "dateModified", "clientDateModified"):
            if col in item_columns:
                item_values[col] = now
        cols = ", ".join(item_values)
        cursor = conn.execute(
            f"INSERT INTO items ({cols}) VALUES ({', '.join('?' for _ in item_values)})",
            tuple(item_values.values()),
        )
        att_id = int(cursor.lastrowid)

        conn.execute(
            "INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path, syncState) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (att_id, parent_id, _LINK_MODE_IMPORTED_URL, _PDF_CONTENT_TYPE,
             f"storage:{filename}", _SYNC_STATE_TO_UPLOAD),
        )

        for field_name, value in (
            ("title", str(payload.get("title") or "Full Text PDF")),
            ("url", str(payload.get("source_url") or "")),
            ("accessDate", now),
        ):
            if not value:
                continue
            field_id = self._get_field_id(conn, field_name)
            if field_id is None:
                continue
            value_id = self._upsert_item_data_value(conn, value)
            conn.execute(
                "INSERT OR IGNORE INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
                (att_id, field_id, value_id),
            )

    def _new_item_key(self, conn: sqlite3.Connection) -> str:
        """8-char Zotero-style key not already present in ``items``."""
        for _ in range(50):
            key = "".join(random.choice(self._KEY_ALPHABET) for _ in range(8))
            if conn.execute("SELECT 1 FROM items WHERE key = ? LIMIT 1", (key,)).fetchone() is None:
                return key
        raise ZoteroWriteError("add_attachment: could not generate a unique item key")  # pragma: no cover

    @staticmethod
    def _safe_pdf_filename(name: Any, *, fallback: str) -> str:
        """Strip path separators / control chars; force a .pdf suffix."""
        base = "".join(c for c in str(name or "").strip() if c not in '/\\\x00').strip() or fallback
        if not base.lower().endswith(".pdf"):
            base += ".pdf"
        return base[:120]
