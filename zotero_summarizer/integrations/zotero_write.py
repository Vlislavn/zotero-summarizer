from __future__ import annotations

import json
import random
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar
from urllib.request import urlopen

from zotero_summarizer.integrations._zotero_write_common import LOGGER, ZoteroWriteError  # noqa: F401
from zotero_summarizer.integrations._zotero_write_items import ZoteroItemWriteMixin
from zotero_summarizer.integrations._zotero_write_tags import ZoteroTagMixin
from zotero_summarizer.integrations._zotero_write_collections import ZoteroCollectionMixin

_T = TypeVar("_T")


class ZoteroWriter(ZoteroItemWriteMixin, ZoteroTagMixin, ZoteroCollectionMixin):
    """Write adapter that applies reviewed tag/note changes to Zotero SQLite."""

    _KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"

    def __init__(self, zotero_data_dir: str | Path | None = None) -> None:
        data_dir = Path(zotero_data_dir or (Path.home() / "Zotero")).expanduser().resolve()
        db_path = data_dir / "zotero.sqlite"
        if not data_dir.exists():
            raise ZoteroWriteError(f"Zotero data directory not found: {data_dir}")
        if not db_path.exists():
            raise ZoteroWriteError(f"Zotero database not found: {db_path}")

        self.data_dir = data_dir
        self.db_path = db_path

    def is_connector_running(self) -> bool:
        """Return True when Zotero connector HTTP server responds locally."""
        try:
            with urlopen("http://127.0.0.1:23119/connector/ping", timeout=0.8) as response:
                return int(getattr(response, "status", 0)) == 200
        except Exception:
            return False

    @staticmethod
    def _is_db_locked(exc: Exception) -> bool:
        return "database is locked" in str(exc).lower()

    @staticmethod
    def _retry_on_lock(
        fn: Callable[[], _T],
        *,
        max_retries: int = 3,
        delay_secs: float = 5.0,
        ctx: str = "",
    ) -> _T:
        """Call fn(); retry up to max_retries times on 'database is locked'."""
        label = f" [{ctx}]" if ctx else ""
        for attempt in range(1, max_retries + 2):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if not ZoteroWriter._is_db_locked(exc) or attempt > max_retries:
                    raise
                LOGGER.warning(
                    "DB locked%s (attempt %d/%d) — retrying in %.0fs",
                    label, attempt, max_retries, delay_secs,
                )
                time.sleep(delay_secs)
        raise RuntimeError("unreachable")  # pragma: no cover

    def backup_database(self) -> str:
        """Create timestamped backup of zotero.sqlite and return backup path."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = self.data_dir / f"zotero.sqlite.backup_{timestamp}"
        shutil.copy2(self.db_path, backup_path)
        return str(backup_path)

    def apply_changes(self, changes: Sequence[dict[str, Any]], create_backup: bool = True) -> dict[str, Any]:
        """Apply queued changes and return applied IDs and per-item failures."""
        if not changes:
            return {"applied_ids": [], "failed": [], "backup_path": None}

        backup_path = self.backup_database() if create_backup else None

        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass

            item_columns = self._table_columns(conn, "items")
            tag_columns = self._table_columns(conn, "tags")
            item_tag_columns = self._table_columns(conn, "itemTags")
            note_columns = self._table_columns(conn, "itemNotes")
            collection_columns = self._table_columns(conn, "collections")
            collection_item_columns = self._table_columns(conn, "collectionItems")
            item_data_columns = self._table_columns(conn, "itemData")
            item_data_value_columns = self._table_columns(conn, "itemDataValues")
            creators_columns = self._table_columns(conn, "creators")
            item_creators_columns = self._table_columns(conn, "itemCreators")

            applied_ids: list[int] = []
            failed: list[dict[str, Any]] = []

            for change in changes:
                change_id = int(change.get("id") or 0)
                savepoint_name = f"change_{change_id or random.randint(1000, 9999)}"
                conn.execute(f"SAVEPOINT {savepoint_name}")
                try:
                    change_type = str(change.get("change_type") or "").strip()
                    item_key = str(change.get("item_key") or "").strip()
                    if not change_type or not item_key:
                        raise ZoteroWriteError("Invalid pending change record")

                    payload = change.get("payload_json", {})
                    payload_dict = self._coerce_payload(payload)

                    if change_type == "tag_changes":
                        self._apply_tag_change(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            tag_columns=tag_columns,
                            item_tag_columns=item_tag_columns,
                        )
                    elif change_type == "add_note":
                        self._apply_note_change(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            note_columns=note_columns,
                        )
                    elif change_type == "upsert_note":
                        self._apply_upsert_note_change(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            note_columns=note_columns,
                        )
                    elif change_type == "add_to_collection":
                        self._apply_collection_change(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            collection_columns=collection_columns,
                            collection_item_columns=collection_item_columns,
                        )
                    elif change_type == "remove_from_collection":
                        self._apply_collection_remove(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            collection_columns=collection_columns,
                        )
                    elif change_type == "create_item_from_feed":
                        self._apply_create_item_from_feed(
                            conn,
                            new_item_key=item_key,
                            payload=payload_dict,
                            item_columns=item_columns,
                            item_data_columns=item_data_columns,
                            item_data_value_columns=item_data_value_columns,
                            creators_columns=creators_columns,
                            item_creators_columns=item_creators_columns,
                        )
                    elif change_type == "promote_from_inbox":
                        # Promote = remove from "Inbox" collection; the user's target
                        # collection assignments are queued as separate add_to_collection
                        # changes by the orchestrator (already supported above).
                        self._apply_collection_remove(
                            conn,
                            item_key=item_key,
                            payload={"collection_path": "Inbox"},
                            item_columns=item_columns,
                            collection_columns=collection_columns,
                        )
                    elif change_type == "mark_feed_item_read":
                        self._apply_mark_feed_item_read(
                            conn,
                            item_key=item_key,
                            payload=payload_dict,
                        )
                    else:
                        raise ZoteroWriteError(f"Unsupported change type: {change_type}")

                    conn.execute(f"RELEASE {savepoint_name}")
                    applied_ids.append(change_id)
                except Exception as exc:
                    conn.execute(f"ROLLBACK TO {savepoint_name}")
                    conn.execute(f"RELEASE {savepoint_name}")
                    failed.append({"id": change_id, "error": str(exc)})

            conn.commit()
            return {"applied_ids": applied_ids, "failed": failed, "backup_path": backup_path}
        except sqlite3.Error as exc:
            conn.rollback()
            raise ZoteroWriteError(f"Failed to apply queued changes: {exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def _coerce_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ZoteroWriteError("Invalid JSON payload in pending change") from exc
            if not isinstance(parsed, dict):
                raise ZoteroWriteError("Pending change payload must be a JSON object")
            return parsed
        if payload is None:
            return {}
        raise ZoteroWriteError("Pending change payload must be a JSON object")
