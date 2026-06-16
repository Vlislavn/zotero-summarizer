from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from zotero_summarizer.domain import is_valid_reading_priority
from zotero_summarizer.integrations._zotero_read_common import (  # noqa: F401  (ZoteroReadError re-exported)
    ZoteroReadError,
    _INJECTION_CHAR_PATTERN,
    _NON_BIBLIOGRAPHIC_TYPES_SQL,
    _USER_LIBRARY_ID_SELECT,
)
from zotero_summarizer.integrations._zotero_read_feeds import ZoteroFeedsMixin
from zotero_summarizer.integrations._zotero_read_items import ZoteroItemsMixin
from zotero_summarizer.integrations._zotero_read_lookup import ZoteroLookupMixin


class ZoteroReader(ZoteroItemsMixin, ZoteroLookupMixin, ZoteroFeedsMixin):
    """Read-only adapter over Zotero's local SQLite database.

    Query methods live in mixins (items / lookup / feeds); this class owns the
    connection/execute infrastructure + collection helpers they call via ``self``.
    """

    _RETRY_DELAYS_SECONDS = (0.0, 0.05)
    _SQLITE_TIMEOUT_SECONDS = 0.2

    def __init__(self, zotero_data_dir: str | Path | None = None) -> None:
        data_dir = Path(zotero_data_dir or (Path.home() / "Zotero")).expanduser().resolve()
        db_path = data_dir / "zotero.sqlite"
        storage_dir = data_dir / "storage"

        if not data_dir.exists():
            raise ZoteroReadError(f"Zotero data directory not found: {data_dir}")
        if not db_path.exists():
            raise ZoteroReadError(f"Zotero database not found: {db_path}")

        self.data_dir = data_dir
        self.db_path = db_path
        self.storage_dir = storage_dir

    def get_library_stats(self) -> dict[str, Any]:
        """Return high-level counts for the local Zotero library."""
        query_items = f"""
            SELECT COUNT(*) AS value
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN deletedItems di ON di.itemID = i.itemID
            WHERE di.itemID IS NULL
              AND i.libraryID = ({_USER_LIBRARY_ID_SELECT})
              AND it.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})
        """
        query_collections = "SELECT COUNT(*) AS value FROM collections"
        query_tags = "SELECT COUNT(*) AS value FROM tags"
        query_items_with_pdf = f"""
            SELECT COUNT(DISTINCT ia.parentItemID) AS value
            FROM itemAttachments ia
            JOIN items parent ON parent.itemID = ia.parentItemID
            JOIN itemTypes it ON it.itemTypeID = parent.itemTypeID
            LEFT JOIN deletedItems di ON di.itemID = parent.itemID
            WHERE ia.parentItemID IS NOT NULL
              AND lower(COALESCE(ia.contentType, '')) = 'application/pdf'
              AND di.itemID IS NULL
              AND parent.libraryID = ({_USER_LIBRARY_ID_SELECT})
              AND it.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})
        """

        def _read(conn: sqlite3.Connection) -> dict[str, Any]:
            total_items = int(conn.execute(query_items).fetchone()["value"])
            total_collections = int(conn.execute(query_collections).fetchone()["value"])
            total_tags = int(conn.execute(query_tags).fetchone()["value"])
            items_with_pdf = int(conn.execute(query_items_with_pdf).fetchone()["value"])
            return {
                "total_items": total_items,
                "total_collections": total_collections,
                "total_tags": total_tags,
                "items_with_pdf": items_with_pdf,
            }

        return self._execute_read(_read)

    def get_collections(self) -> list[dict[str, Any]]:
        """Return the collection tree with per-collection item counts."""

        def _read(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            collection_rows = conn.execute(
                """
                SELECT collectionID, key, collectionName, parentCollectionID
                FROM collections
                """
            ).fetchall()
            count_rows = conn.execute(
                f"""
                SELECT ci.collectionID, COUNT(ci.itemID) AS item_count
                FROM collectionItems ci
                JOIN items i ON i.itemID = ci.itemID
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE di.itemID IS NULL
                  AND it.typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL})
                GROUP BY ci.collectionID
                """
            ).fetchall()

            count_by_collection_id = {
                int(row["collectionID"]): int(row["item_count"]) for row in count_rows
            }

            nodes: dict[int, dict[str, Any]] = {}
            roots: list[dict[str, Any]] = []
            for row in collection_rows:
                collection_id = int(row["collectionID"])
                nodes[collection_id] = {
                    "collection_id": collection_id,
                    "key": str(row["key"]),
                    "name": str(row["collectionName"] or ""),
                    "parent_collection_id": row["parentCollectionID"],
                    "item_count": count_by_collection_id.get(collection_id, 0),
                    "children": [],
                }

            for node in nodes.values():
                parent_id_raw = node["parent_collection_id"]
                if parent_id_raw is None:
                    roots.append(node)
                    continue
                parent_id = int(parent_id_raw)
                parent = nodes.get(parent_id)
                if parent is None:
                    roots.append(node)
                else:
                    parent["children"].append(node)

            self._sort_collection_nodes(roots)
            return roots

        return self._execute_read(_read)

    def get_user_library_id(self) -> int:
        """Return the libraryID of the user's personal library (type='user')."""

        def _read(conn: sqlite3.Connection) -> int:
            row = conn.execute(_USER_LIBRARY_ID_SELECT).fetchone()
            if not row:
                raise ZoteroReadError("No user library found in Zotero database")
            return int(row["libraryID"])

        return self._execute_read(_read)

    def _connect(self) -> sqlite3.Connection:
        return self._connect_db(self.db_path)

    def _connect_db(self, db_path: Path, *, immutable: bool = False) -> sqlite3.Connection:
        # immutable=1 disables WAL replay and change detection. Safe ONLY for snapshot
        # copies in a temp dir (where the file truly won't change). Never apply to the
        # live Zotero DB while Zotero may be writing — that produces stale reads.
        params = "mode=ro&immutable=1" if immutable else "mode=ro"
        uri = f"file:{db_path}?{params}"
        conn = sqlite3.connect(uri, uri=True, timeout=self._SQLITE_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self._SQLITE_TIMEOUT_SECONDS * 1000)}")
        return conn

    def _execute_read(self, fn):
        last_error: Exception | None = None
        for delay in self._RETRY_DELAYS_SECONDS:
            if delay > 0:
                time.sleep(delay)
            try:
                conn = self._connect()
                try:
                    return fn(conn)
                finally:
                    conn.close()
            except sqlite3.OperationalError as exc:
                last_error = exc
                if self._is_busy_error(exc):
                    continue
                raise ZoteroReadError(f"Failed to query Zotero DB: {exc}") from exc
            except sqlite3.Error as exc:
                raise ZoteroReadError(f"Failed to query Zotero DB: {exc}") from exc
        if last_error is not None:
            try:
                return self._execute_snapshot_read(fn)
            except ZoteroReadError:
                raise ZoteroReadError(f"Zotero DB is busy: {last_error}") from last_error
        raise ZoteroReadError("Unable to query Zotero DB")

    def _execute_snapshot_read(self, fn):
        with tempfile.TemporaryDirectory(prefix="zotero-snapshot-") as tmp_dir:
            snapshot_dir = Path(tmp_dir)
            snapshot_db_path = snapshot_dir / self.db_path.name
            self._copy_database_snapshot(snapshot_db_path)
            # immutable=1 tells SQLite to skip WAL replay on the snapshot copy. This
            # gives us a consistent point-in-time view even if the source DB's WAL/SHM
            # was mid-flight when we copied — without needing write access to checkpoint.
            conn = self._connect_db(snapshot_db_path, immutable=True)
            try:
                return fn(conn)
            except sqlite3.Error as exc:
                raise ZoteroReadError(f"Failed to query Zotero snapshot DB: {exc}") from exc
            finally:
                conn.close()

    def _copy_database_snapshot(self, snapshot_db_path: Path) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            source_path = Path(f"{self.db_path}{suffix}")
            if not source_path.exists():
                continue
            target_path = Path(f"{snapshot_db_path}{suffix}")
            shutil.copy2(source_path, target_path)

    @staticmethod
    def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    def _load_collection_map(self, conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT collectionID, collectionName, parentCollectionID
            FROM collections
            """
        ).fetchall()
        return {
            int(row["collectionID"]): {
                "name": str(row["collectionName"] or ""),
                "parent": row["parentCollectionID"],
            }
            for row in rows
        }

    def _collection_path(self, collection_id: int, collection_map: dict[int, dict[str, Any]]) -> str:
        parts: list[str] = []
        seen: set[int] = set()
        current_id: int | None = collection_id
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            node = collection_map.get(current_id)
            if not node:
                break
            name = str(node.get("name") or "").strip()
            if name:
                parts.append(name)
            parent = node.get("parent")
            current_id = int(parent) if parent is not None else None
        return " > ".join(reversed(parts))

    # The two workflow collections pin to the top of every collections list (rank
    # 0 = the daily-selection landing zone, rank 1 = the read-next queue) so the
    # surfaces the user acts on most aren't buried alphabetically; everything else
    # stays alphabetical. The read-next pattern mirrors the frontend
    # CollectionEditor's READ_NEXT_RE so the two never drift.
    _PINNED_COLLECTIONS: tuple[tuple[int, "re.Pattern[str]"], ...] = (
        (0, re.compile(r"^\s*inbox\b", re.IGNORECASE)),
        (1, re.compile(r"read[\s_-]*next|read[\s_-]*later|to[\s_-]*read|reading[\s_-]*list", re.IGNORECASE)),
    )

    @staticmethod
    def _collection_sort_key(node: dict[str, Any]) -> tuple[int, str]:
        name = str(node.get("name") or "")
        for rank, pattern in ZoteroReader._PINNED_COLLECTIONS:
            if pattern.search(name):
                return (rank, name.lower())
        return (len(ZoteroReader._PINNED_COLLECTIONS), name.lower())

    @staticmethod
    def _sort_collection_nodes(nodes: list[dict[str, Any]]) -> None:
        nodes.sort(key=ZoteroReader._collection_sort_key)
        for node in nodes:
            ZoteroReader._sort_collection_nodes(node.get("children", []))

    @staticmethod
    def _sanitize_text(value: str) -> str:
        """Strip injection-risk control + Unicode tag chars from feed-supplied text.

        Defense layer 1 against indirect prompt injection: feed abstracts go
        directly into the triage LLM prompt. Without this strip, U+E0000-U+E007F
        tag chars or control chars can smuggle hidden instructions past visual
        review. The triage prompt also wraps the content in <untrusted_input>
        tags as layer 2 (see goals.yaml prompts.triage).
        """
        if not value:
            return ""
        return _INJECTION_CHAR_PATTERN.sub("", value)

    @staticmethod
    def _split_blob(blob: str) -> list[str]:
        if not blob:
            return []
        return [part.strip() for part in blob.split("|||") if part.strip()]

    @staticmethod
    def _priority_from_tags(tags: list[str]) -> str | None:
        for tag in tags:
            if tag.startswith("zs:"):
                value = tag.split(":", 1)[1].strip()
                if is_valid_reading_priority(value):
                    return value
        return None

    def _resolve_attachment_path(self, attachment_key: str, stored_path: str) -> str | None:
        value = (stored_path or "").strip()
        if not value:
            return None

        candidate: Path | None = None
        if value.startswith("storage:"):
            relative_name = value.split(":", 1)[1].strip()
            candidate = self.storage_dir / attachment_key / relative_name
        elif value.startswith("file://"):
            parsed = urlparse(value)
            candidate = Path(unquote(parsed.path))
        else:
            raw_path = Path(value).expanduser()
            if raw_path.is_absolute():
                candidate = raw_path
            elif attachment_key:
                candidate = self.storage_dir / attachment_key / value
            else:
                candidate = self.data_dir / value

        if candidate is None:
            return None
        try:
            return str(candidate.resolve())
        except OSError:
            return str(candidate)
