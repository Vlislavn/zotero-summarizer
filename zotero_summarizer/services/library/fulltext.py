"""Acquire OA PDFs and attach them to Zotero, backup-first and non-interactively.

Today Add and whole-library retry share this engine and can reuse a deep-review path.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from zotero_summarizer.integrations.pdf_fetch import valid_pdf_path
from zotero_summarizer.services.library import _pdf_acquire
from zotero_summarizer.services.zotero.zotero import (
    get_zotero_reader_or_raise,
    get_zotero_writer_or_raise,
)

_ATTACH_TITLE = "Full Text PDF"

_LOCK = threading.Lock()
_RUNNING = False
_RESULT: dict[str, Any] | None = None
_PROGRESS = {"done": 0, "total": 0}


def is_running() -> bool:
    with _LOCK:
        return _RUNNING


def last_result() -> dict[str, Any] | None:
    with _LOCK:
        return _RESULT


def progress() -> dict[str, int]:
    with _LOCK:
        return dict(_PROGRESS)


def _try_start() -> bool:
    global _RUNNING, _RESULT
    with _LOCK:
        if _RUNNING:
            return False
        _RUNNING = True
        _RESULT = None
        _PROGRESS.update(done=0, total=0)
        return True


def _finish(result: dict[str, Any]) -> None:
    global _RUNNING, _RESULT
    with _LOCK:
        _RUNNING = False
        _RESULT = result


def _acquire(item: dict[str, Any]) -> dict[str, str]:
    """Return an attachment candidate or a typed non-fatal outcome."""
    key = str(item["item_key"])
    cached = item.get("cached_acquisition")
    if isinstance(cached, dict):
        path = Path(str(cached.get("path") or "")).expanduser()
        if valid_pdf_path(path):
            return {
                "item_key": key, "path": str(path), "source": str(cached.get("source") or "cached"),
                "source_url": str(cached.get("source_url") or ""), "status": "ready_cached",
            }
    result = _pdf_acquire.acquire_pdf_for(key, item, allow_browser=False)
    if result.path is None:
        return {
            "item_key": key, "status": result.outcome,
            "source": result.source, "source_url": result.source_url,
        }
    return {
        "item_key": key, "path": str(result.path), "source": result.source,
        "source_url": result.source_url, "status": f"ready_{result.source}",
    }


def _summary(outcomes: list[dict[str, str]], backup_path: Any = None) -> dict[str, Any]:
    statuses = [row["status"] for row in outcomes]
    return {
        "attached": sum(s.startswith("attached_") for s in statuses),
        "skipped_has_pdf": statuses.count("skipped_has_pdf"),
        "backup_path": backup_path,
        "outcomes": outcomes,
    }


def fetch_fulltext_for_items(items: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    """Acquire once per key, then attach. Acquisition errors abort before writes.

    First occurrence supplies metadata, but any existing-PDF flag wins over a
    duplicate wanting acquisition. Outcome/progress counts describe unique keys.
    """
    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running() and not force:  # fast-fail before downloading
        return {"error": "zotero_running", "requires_force": True,
                "message": "Zotero appears to be running; close Zotero or confirm force apply."}

    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item["item_key"])
        if key not in unique or item.get("has_pdf"):
            unique[key] = item
    items = list(unique.values())

    outcomes: list[dict[str, str]] = []
    candidates: list[dict[str, str]] = []
    with _LOCK:
        _PROGRESS.update(done=0, total=sum(not it.get("has_pdf") for it in items))
    for it in items:
        if it.get("has_pdf"):
            outcomes.append({"item_key": str(it["item_key"]), "status": "skipped_has_pdf", "source": ""})
            continue
        outcome = _acquire(it)
        (candidates if outcome["status"].startswith("ready_") else outcomes).append(outcome)
        with _LOCK:
            _PROGRESS["done"] += 1

    if not candidates:
        return _summary(outcomes)

    changes = [
        {"id": index, "item_key": row["item_key"], "change_type": "add_attachment",
         "payload_json": {"source_path": row["path"], "filename": Path(row["path"]).name,
                          "source_url": row["source_url"], "title": _ATTACH_TITLE}}
        for index, row in enumerate(candidates)
    ]
    result = writer.apply_changes(changes, True)  # True = backup first
    applied = {int(value) for value in result.get("applied_ids") or []}
    for index, row in enumerate(candidates):
        row["status"] = f"attached_{row['status'][len('ready_'):]}" if index in applied else "write_failed"
        outcomes.append(row)
    return _summary(outcomes, result.get("backup_path"))


def fetch_fulltext_bulk(*, force: bool = False) -> dict[str, Any]:
    """Whole-library retry through the same acquisition + attachment engine."""
    reader = get_zotero_reader_or_raise()
    items = reader.get_all_items(include_abstract=False).get("items", [])
    urls = reader.get_field_values("url")
    dois = reader.get_field_values("DOI")
    enriched = [
        {"item_key": it["item_key"], "has_pdf": bool(it.get("has_pdf")),
         "url": urls.get(str(it["item_key"]), ""), "doi": dois.get(str(it["item_key"]), "")}
        for it in items
    ]
    return fetch_fulltext_for_items(enriched, force=force)


def start_bulk(*, force: bool = False) -> dict[str, Any]:
    """Kick the bulk fetch as a background job (single-flight). Pre-checks the
    connector guard so the UI can prompt for force without starting work."""
    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running() and not force:
        return {"error": "zotero_running", "requires_force": True,
                "message": "Zotero appears to be running; close Zotero or confirm force apply."}
    if not _try_start():
        return {"status": "running"}

    def _run() -> None:
        try:
            _finish(fetch_fulltext_bulk(force=force))
        except Exception as exc:  # noqa: BLE001 — surfaced via status, then re-raised
            _finish({"error": f"{type(exc).__name__}: {exc}"})
            raise

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


def status() -> dict[str, Any]:
    """In-memory bulk-job state (no Zotero read): ``{running, progress, result}``."""
    return {"running": is_running(), "progress": progress(), "result": last_result()}
