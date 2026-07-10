"""App-owned library reader — the Zotero-absent drop-in for the read path.

When no Zotero DB is configured, the "library" the user reads is the set of
feed papers they kept (Today -> "Add to library"): ``processed_feed_items`` rows
with a kept ``decision``, joined to their ``rss_items`` metadata on
``stable_feed_key``. This adapter mirrors the *minimal* ``ZoteroReader`` surface
the reading stack consumes — ``get_all_items`` (reading queue) and
``get_item_detail`` (brief / deep-review / ask / PDF acquire) — keyed by the
durable ``stable_feed_key`` instead of a Zotero item key.

PDFs are never local Zotero attachments here, so every item reports
``has_pdf=False`` / ``pdf_path=None``; ``_pdf_acquire`` then fetches into the
local cache from the item's url/doi (the same path the review-fleet already
uses). Collections / notes / annotations are Zotero concepts with no app-owned
equivalent, so they are returned empty. See ``services/library/README.md``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from zotero_summarizer.storage import rss as rss_storage
from zotero_summarizer.storage.feeds_constants import (
    DECISION_BLACK_SWAN,
    DECISION_SELECTED,
    DECISION_USER_APPROVED,
)
from zotero_summarizer.storage.feeds_lookup import get_processed_feed_item_by_stable_key

# Decisions that put a paper into the user's reading library: kept from Today
# ("Add to library" -> user_approved) plus the auto-selected daily picks.
KEPT_DECISIONS: tuple[str, ...] = (DECISION_USER_APPROVED, DECISION_SELECTED, DECISION_BLACK_SWAN)


class AppLibraryReader:
    """Read-only library over kept feed papers. Constructed with the triage DB
    path; opens a short-lived connection per call (mirrors ``AppRssReader``)."""

    def __init__(self, triage_db_path: Path | str):
        self._db_path = Path(triage_db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def get_all_items(
        self,
        collection_key: str | None = None,
        search: str | None = None,
        tag: str | None = None,
        include_abstract: bool = True,
    ) -> dict[str, Any]:
        """Every kept paper as the shared item dict. ``collection_key`` / ``tag``
        are Zotero-only filters with no app equivalent and are ignored; ``search``
        is a substring match on title/abstract."""
        conn = self._connect()
        try:
            rows = rss_storage.list_library_items(
                conn, decisions=KEPT_DECISIONS, search=search,
            )
        except sqlite3.OperationalError as exc:
            if _is_missing_feed_schema(exc):
                return {"items": [], "total": 0}
            raise
        finally:
            conn.close()
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            key = str(row.get("stable_feed_key") or "")
            if not key or key in seen:  # newest row per paper wins (ORDER BY created_at DESC)
                continue
            seen.add(key)
            items.append(self._row_to_item(row, include_abstract=include_abstract))
        return {"items": items, "total": len(items)}

    def get_item_detail(self, item_key: str) -> dict[str, Any] | None:
        """Rich detail for one kept paper by ``stable_feed_key``. ``None`` when the
        key resolves to no processed row (caller's contract: not in library)."""
        key = str(item_key or "").strip()
        if not key:
            return None
        conn = self._connect()
        try:
            processed = get_processed_feed_item_by_stable_key(conn, key)
            if processed is None:
                return None
            meta = rss_storage.get_rss_item_by_stable_key(conn, key)
        except sqlite3.OperationalError as exc:
            if _is_missing_feed_schema(exc):
                return None
            raise
        finally:
            conn.close()
        meta = meta or {}
        return {
            "item_key": key,
            "item_type": str(meta.get("item_type") or "journalArticle"),
            "title": str(meta.get("title") or processed.get("title") or "Untitled"),
            "abstract": str(meta.get("abstract") or ""),
            "publication_date": str(meta.get("publication_date") or ""),
            "doi": str(meta.get("doi") or processed.get("doi") or ""),
            "url": str(meta.get("url") or meta.get("canonical_url") or ""),
            "authors": _authors_list(meta.get("authors")),
            "tags": [],
            "collections": [],
            "notes": [],
            "annotations": [],
            "attachments": [],
            "pdf_path": None,
            "has_pdf": False,
            "reading_priority": str(processed.get("reading_priority") or "could_read"),
            "date_added": str(processed.get("created_at") or ""),
            "date_modified": str(processed.get("updated_at") or ""),
        }

    @staticmethod
    def _row_to_item(row: dict[str, Any], *, include_abstract: bool) -> dict[str, Any]:
        item = {
            "item_key": str(row.get("stable_feed_key") or ""),
            "title": str(row.get("ri_title") or row.get("p_title") or "Untitled"),
            "authors": str(row.get("authors") or ""),
            "publication_date": str(row.get("publication_date") or ""),
            "tags": [],
            "collections": [],
            "reading_priority": str(row.get("reading_priority") or "could_read"),
            "has_pdf": False,
            "pdf_path": None,
            "date_added": str(row.get("p_created") or ""),
            "date_modified": str(row.get("p_updated") or ""),
        }
        item["abstract"] = str(row.get("abstract") or "") if include_abstract else ""
        return item


def _authors_list(authors: Any) -> list[str]:
    """rss_items stores authors as a single string; mirror ZoteroReader's list."""
    text = str(authors or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in text.replace(";", ",").split(",")]
    return [p for p in parts if p]


def _is_missing_feed_schema(exc: sqlite3.OperationalError) -> bool:
    return "no such table:" in str(exc).lower()


__all__ = ["AppLibraryReader", "KEPT_DECISIONS"]
