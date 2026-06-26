from __future__ import annotations

import json
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar
from urllib.request import urlopen

from zotero_summarizer.integrations._zotero_write_common import (  # noqa: F401
    LOGGER,
    WriteColumns,
    ZoteroWriteError,
    read_write_columns,
)
from zotero_summarizer.integrations._zotero_write_items import ZoteroItemWriteMixin
from zotero_summarizer.integrations._zotero_write_fields import ZoteroFieldWriteMixin
from zotero_summarizer.integrations._zotero_write_attachments import ZoteroAttachmentWriteMixin
from zotero_summarizer.integrations._zotero_write_tags import ZoteroTagMixin
from zotero_summarizer.integrations._zotero_write_collections import ZoteroCollectionMixin

_T = TypeVar("_T")


class ZoteroWriter(
    ZoteroItemWriteMixin, ZoteroFieldWriteMixin, ZoteroAttachmentWriteMixin,
    ZoteroTagMixin, ZoteroCollectionMixin,
):
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

    _BACKUP_GLOB = "zotero.sqlite.backup_*"
    _BACKUP_KEEP = 5

    def backup_database(self) -> str:
        """Create a WAL-consistent, integrity-checked timestamped backup and
        return its path.

        Uses SQLite's online backup API rather than a raw file copy: the live DB
        is WAL-mode, so ``shutil.copy`` of ``zotero.sqlite`` alone captures only
        checkpointed pages (a torn/stale snapshot) — exactly the file we rely on
        right before a destructive rewrite. ``src.backup(dst)`` snapshots
        WAL-resident pages consistently. The copy is then verified with
        ``PRAGMA integrity_check``; a bad backup raises (fail-fast) so it can
        never silently precede the write. Lock contention is retried."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = self.data_dir / f"zotero.sqlite.backup_{timestamp}"

        def _do() -> None:
            src = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=15)
            try:
                dst = sqlite3.connect(str(backup_path))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()

        self._retry_on_lock(_do, ctx="backup")
        check = sqlite3.connect(f"file:{backup_path}?mode=ro&immutable=1", uri=True)
        try:
            row = check.execute("PRAGMA integrity_check").fetchone()
        finally:
            check.close()
        if not row or str(row[0]).lower() != "ok":
            backup_path.unlink(missing_ok=True)
            raise ZoteroWriteError(f"Backup failed integrity_check ({row}); aborting write.")
        return str(backup_path)

    def _prune_backups(self) -> None:
        """Keep only the ``_BACKUP_KEEP`` most-recent app-created backups (our
        timestamped glob only — never Zotero's own ``.bak`` files). Names sort
        chronologically, so lexical-desc == newest-first."""
        backups = sorted(self.data_dir.glob(self._BACKUP_GLOB), key=lambda p: p.name, reverse=True)
        for stale in backups[self._BACKUP_KEEP:]:
            stale.unlink(missing_ok=True)

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
            except sqlite3.Error as _:
                pass

            cols = read_write_columns(lambda t: self._table_columns(conn, t))

            applied_ids: list[int] = []
            failed: list[dict[str, Any]] = []

            for change in changes:
                change_id = int(change.get("id") or 0)
                savepoint_name = f"change_{change_id or random.randint(1000, 9999)}"
                conn.execute(f"SAVEPOINT {savepoint_name}")
                try:
                    self._dispatch_change(conn, change, cols)
                    conn.execute(f"RELEASE {savepoint_name}")
                    applied_ids.append(change_id)
                except Exception as exc:
                    conn.execute(f"ROLLBACK TO {savepoint_name}")
                    conn.execute(f"RELEASE {savepoint_name}")
                    failed.append({"id": change_id, "error": str(exc)})

            conn.commit()
            if backup_path is not None:
                self._prune_backups()  # cap app-created backups (after a successful write)
            return {"applied_ids": applied_ids, "failed": failed, "backup_path": backup_path}
        except sqlite3.Error as exc:
            conn.rollback()
            raise ZoteroWriteError(f"Failed to apply queued changes: {exc}") from exc
        finally:
            conn.close()

    def _dispatch_change(self, conn: sqlite3.Connection, change: dict[str, Any], cols: WriteColumns) -> None:
        """Validate one queued change and route it to the right ``_apply_*`` writer."""
        change_type = str(change.get("change_type") or "").strip()
        item_key = str(change.get("item_key") or "").strip()
        if not change_type or not item_key:
            raise ZoteroWriteError("Invalid pending change record")
        payload_dict = self._coerce_payload(change.get("payload_json", {}))

        if change_type == "tag_changes":
            self._apply_tag_change(
                conn, item_key=item_key, payload=payload_dict,
                item_columns=cols.items, tag_columns=cols.tags, item_tag_columns=cols.item_tags,
            )
        elif change_type == "add_note":
            self._apply_note_change(
                conn, item_key=item_key, payload=payload_dict,
                item_columns=cols.items, note_columns=cols.item_notes,
            )
        elif change_type == "upsert_note":
            self._apply_upsert_note_change(
                conn, item_key=item_key, payload=payload_dict,
                item_columns=cols.items, note_columns=cols.item_notes,
            )
        elif change_type == "add_to_collection":
            self._apply_collection_change(
                conn, item_key=item_key, payload=payload_dict,
                item_columns=cols.items, collection_columns=cols.collections,
                collection_item_columns=cols.collection_items,
            )
        elif change_type == "remove_from_collection":
            self._apply_collection_remove(
                conn, item_key=item_key, payload=payload_dict,
                item_columns=cols.items, collection_columns=cols.collections,
            )
        elif change_type == "create_item_from_feed":
            self._apply_create_item_from_feed(conn, new_item_key=item_key, payload=payload_dict, cols=cols)
        elif change_type == "promote_from_inbox":
            # Promote = remove from "Inbox"; the user's target collection assignments are
            # queued as separate add_to_collection changes by the orchestrator.
            self._apply_collection_remove(
                conn, item_key=item_key, payload={"collection_path": "Inbox"},
                item_columns=cols.items, collection_columns=cols.collections,
            )
        elif change_type == "set_field":
            self._apply_set_field(
                conn, item_key=item_key, payload=payload_dict,
                item_data_columns=cols.item_data, item_columns=cols.items,
            )
        elif change_type == "add_attachment":
            self._apply_add_attachment(
                conn, item_key=item_key, payload=payload_dict,
                item_columns=cols.items, item_data_columns=cols.item_data,
                item_data_value_columns=cols.item_data_values,
            )
        elif change_type == "mark_feed_item_read":
            self._apply_mark_feed_item_read(conn, item_key=item_key, payload=payload_dict)
        else:
            raise ZoteroWriteError(f"Unsupported change type: {change_type}")

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
