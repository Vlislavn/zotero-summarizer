"""Fetch full-text PDFs from arXiv and attach them natively to Zotero items.

Two entry points share one engine:
  * ``fetch_fulltext_for_items`` — attach PDFs for a known set of items (used by
    the initial-add hook for just-materialized papers).
  * ``start_bulk`` / ``fetch_fulltext_bulk`` — scan the WHOLE library for papers
    that have an arXiv link but no PDF yet and attach them (the Library button),
    run as a single background job with progress.

Reuses the proven ``pdf_fetch.resolve_pdf_url`` (arXiv-first) + ``fetch_pdf``
(streams to cache, %PDF-checked, size-capped). Idempotent: items that already
have a PDF are skipped. Writes go through ``ZoteroWriter.apply_changes(create_
backup=True)`` (WAL-consistent backup) and are connector-guarded — the library
syncs to zotero.org, so we never write while Zotero is open. arXiv fetches run at
bounded concurrency to stay polite.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from zotero_summarizer.integrations import pdf_fetch
from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi
from zotero_summarizer.services.zotero.zotero import (
    get_zotero_reader_or_raise,
    get_zotero_writer_or_raise,
)

# Hosts in the arXiv ecosystem whose URLs embed an arXiv id but may dodge the
# stricter ``arxiv.org/(abs|pdf)`` matcher — e.g. ar5iv (HTML5 renderings) and
# ``arxiv.org/html/<id>``. Matching the host CLASS (not a specific id) keeps the
# fetch arXiv-only while covering these forms.
_ARXIV_HOSTS = ("arxiv.org", "ar5iv")
_ARXIV_ID_IN_URL = re.compile(r"(\d{4}\.\d{4,5}(?:v\d+)?)")

_FETCH_WORKERS = 8  # concurrent arXiv downloads — I/O-bound; bounded to stay polite
_ATTACH_TITLE = "arXiv Full Text PDF"

# Single-flight background-job state for the bulk run (mirrors reading_queue).
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


def _arxiv_pdf_url(url: str, doi: str) -> str | None:
    """The arXiv PDF URL for a paper, or None when it has no arXiv link. arXiv
    only (the goal) — we do NOT fall back to Unpaywall/raw URLs here."""
    arxiv_id = _arxiv_id_from_url_or_doi(url or "", doi or "")
    if not arxiv_id and url:
        # ar5iv / arxiv.org/html URLs embed the id but dodge the abs|pdf matcher.
        low = url.lower()
        if any(host in low for host in _ARXIV_HOSTS):
            m = _ARXIV_ID_IN_URL.search(url)
            if m:
                arxiv_id = m.group(1)
    if not arxiv_id:
        return None
    return pdf_fetch.resolve_pdf_url(doi=None, arxiv_id=arxiv_id, url=None)


def fetch_fulltext_for_items(items: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    """Fetch + attach arXiv PDFs for ``items`` (each ``{item_key, url, doi,
    has_pdf}``). Skips items that already have a PDF or no arXiv link. Returns
    ``{attached, skipped_has_pdf, no_arxiv, failed_count, backup_path}`` or a
    ``{requires_force: True}`` notice when Zotero is running."""
    writer = get_zotero_writer_or_raise()
    if writer.is_connector_running() and not force:  # fast-fail before downloading
        return {"error": "zotero_running", "requires_force": True,
                "message": "Zotero appears to be running; close Zotero or confirm force apply."}

    candidates: list[tuple[str, str]] = []  # (item_key, arxiv_pdf_url)
    skipped_has_pdf = no_arxiv = 0
    for it in items:
        if it.get("has_pdf"):
            skipped_has_pdf += 1
            continue
        url = _arxiv_pdf_url(str(it.get("url") or ""), str(it.get("doi") or ""))
        if not url:
            no_arxiv += 1
            continue
        candidates.append((str(it["item_key"]), url))

    with _LOCK:
        _PROGRESS.update(done=0, total=len(candidates))

    failed: list[dict[str, str]] = []
    fetched: list[tuple[str, str, str]] = []  # (item_key, source_path, source_url)

    if candidates:
        with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(candidates))) as pool:
            # Submit all; consume in COMPLETION order so progress reflects what's
            # actually finished (pool.map would yield strictly in submission order
            # — one slow download would stall the counter while others complete).
            futures = {pool.submit(pdf_fetch.fetch_pdf, url): (key, url) for key, url in candidates}
            for fut in as_completed(futures):
                key, url = futures[fut]
                path = fut.result()  # fetch_pdf returns None on failure (no raise)
                with _LOCK:
                    _PROGRESS["done"] += 1
                if path is None:
                    failed.append({"item_key": key, "error": "arXiv fetch failed (404/non-PDF/timeout)"})
                else:
                    fetched.append((key, str(path), url))

    if not fetched:
        return {"attached": 0, "skipped_has_pdf": skipped_has_pdf, "no_arxiv": no_arxiv,
                "failed_count": len(failed), "backup_path": None}

    changes = [
        {"id": 0, "item_key": key, "change_type": "add_attachment",
         "payload_json": {"source_path": path, "filename": url.rsplit("/", 1)[-1],
                          "source_url": url, "title": _ATTACH_TITLE}}
        for key, path, url in fetched
    ]
    result = writer.apply_changes(changes, True)  # True = backup first
    return {
        "attached": len(result.get("applied_ids") or []),
        "skipped_has_pdf": skipped_has_pdf,
        "no_arxiv": no_arxiv,
        "failed_count": len(failed) + len(result.get("failed") or []),
        "backup_path": result.get("backup_path"),
    }


def fetch_fulltext_bulk(*, force: bool = False) -> dict[str, Any]:
    """Whole-library: attach arXiv PDFs to every paper with an arXiv link and no
    PDF. Reads the library once (items + url/DOI fields), delegates to
    :func:`fetch_fulltext_for_items`."""
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
