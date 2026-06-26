"""Shared logger + error type + tiny DB helpers for the Zotero writer (leaf module)."""
from __future__ import annotations

import logging
import random
import sqlite3
from dataclasses import dataclass
from typing import Callable

LOGGER = logging.getLogger("zotero_summarizer.integrations.zotero_write")


class ZoteroWriteError(RuntimeError):
    """Raised when writing to the local Zotero database fails."""


@dataclass(frozen=True)
class WriteColumns:
    """The Zotero-schema column sets the write path introspects once per transaction."""

    items: set[str]
    tags: set[str]
    item_tags: set[str]
    item_notes: set[str]
    collections: set[str]
    collection_items: set[str]
    item_data: set[str]
    item_data_values: set[str]
    creators: set[str]
    item_creators: set[str]


def read_write_columns(table_columns: Callable[[str], set[str]]) -> WriteColumns:
    """Introspect every column set the write path needs (``table_columns`` is the
    ``name -> column set`` lookup, e.g. ``lambda t: self._table_columns(conn, t)``)."""
    return WriteColumns(
        items=table_columns("items"),
        tags=table_columns("tags"),
        item_tags=table_columns("itemTags"),
        item_notes=table_columns("itemNotes"),
        collections=table_columns("collections"),
        collection_items=table_columns("collectionItems"),
        item_data=table_columns("itemData"),
        item_data_values=table_columns("itemDataValues"),
        creators=table_columns("creators"),
        item_creators=table_columns("itemCreators"),
    )


def lookup_int_id(conn: sqlite3.Connection, sql: str, param: str, column: str) -> int | None:
    """Return ``int(row[column])`` for a single-row ``sql`` lookup, or ``None`` if absent."""
    row = conn.execute(sql, (param,)).fetchone()
    if not row:
        return None
    return int(row[column])


def resolve_user_library_item_id(
    conn: sqlite3.Connection, item_key: str, *, required: bool = True
) -> int | None:
    """Resolve an EXISTING **user-library** item's ``itemID`` by key.

    The single guard for every write that mutates an item identified by key.
    Zotero stores the user's library alongside ~dozens of RSS feed libraries
    (and optional group libraries) in the same ``items`` table, so a bare
    ``WHERE key=?`` lookup can resolve a *feed* item and let a user-library
    mutation (tag/note/field/collection/attachment) graft onto it — the
    cross-library leak that produced 403 attachments. All app writes operate on
    the user's personal library only, so resolution is scoped to ``type='user'``
    (this also disambiguates the rare cross-library key collision, since Zotero
    keys are unique per-library, not globally).

    ``required=True`` (default) raises ``ZoteroWriteError`` when the key is
    absent or belongs to another library — the fail-fast contract every
    single-item applier wants. ``required=False`` returns ``None`` instead, for
    the pre-existing best-effort batch path (``remove_items_from_collection``)
    that skips unknown keys rather than aborting the whole batch.
    """
    row = conn.execute(
        "SELECT i.itemID FROM items i "
        "JOIN libraries l ON l.libraryID = i.libraryID "
        "WHERE i.key = ? AND l.type = 'user' LIMIT 1",
        (item_key,),
    ).fetchone()
    if row is None:
        if required:
            raise ZoteroWriteError(
                f"{item_key!r} is not a user-library item — cross-library write rejected"
            )
        return None
    return int(row["itemID"])


def generate_unique_key(
    conn: sqlite3.Connection, table: str, alphabet: str, label: str
) -> str:
    """Generate an 8-char key absent from ``table.key`` (32 attempts), else raise.

    ``table`` is a trusted in-source identifier (never caller input).
    """
    for _ in range(32):
        key = "".join(random.choice(alphabet) for _ in range(8))
        row = conn.execute(f"SELECT 1 FROM {table} WHERE key = ? LIMIT 1", (key,)).fetchone()
        if row is None:
            return key
    raise ZoteroWriteError(f"Could not generate a unique Zotero {label} key")
