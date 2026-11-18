"""Conferences service: overview, preflight, the explicit refresh, and paged reads.

Nothing here runs on page load except local file reads; the only network call is
``refresh``, started by the owner's click. Records live only under
``Settings.conferences_dir`` — no other store reads or receives them.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import now_iso_z, settings, state
from zotero_summarizer.services.conferences import store
from zotero_summarizer.services.conferences.catalogue import LATE_BREAKING, RECIPES, UNKNOWN, Recipe, classify
from zotero_summarizer.services.conferences.config import Conference, Edition, load_config
from zotero_summarizer.services.conferences.crossref_supplement import HOST, download, estimate_requests
from zotero_summarizer.settings import offline_requested

_refresh_lock = threading.Lock()
_NUMBER_PARTS = re.compile(r"(\d+)")


def _find(conf_id: str, year: int) -> tuple[Conference, Edition]:
    for conference in load_config(settings().conferences_config_path).conferences:
        if conference.id == conf_id:
            for edition in conference.editions:
                if edition.year == year:
                    return conference, edition
    raise APIError("conference_not_found", f"No conference {conf_id} {year} in the conferences config", status_code=404)


def _edition_summary(conference: Conference, edition: Edition) -> dict[str, Any]:
    data = store.load_complete(conference.id, edition.year)
    return {"year": edition.year, "from": edition.start.isoformat(), "until": edition.end.isoformat(),
            "downloaded_at": data.get("fetched_at") if data else None,
            "count": len(data["records"]) if data else 0,
            "partial": store.partial_summary(conference.id, edition.year)}


def overview() -> dict[str, Any]:
    config = load_config(settings().conferences_config_path)
    return {
        "config_exists": config.exists,
        "errors": [{"line": e.line, "message": e.message} for e in config.errors],
        "conferences": [{"id": c.id, "label": RECIPES[c.recipe].label,
                         "editions": [_edition_summary(c, e) for e in c.editions]} for c in config.conferences],
    }


def preflight(conf_id: str, year: int) -> dict[str, Any]:
    conference, edition = _find(conf_id, year)
    return {"source": "Crossref", "host": HOST, "login": False, "offline": offline_requested(),
            "requests": estimate_requests(RECIPES[conference.recipe], edition)}


def _mailto() -> str | None:
    config = getattr(getattr(state(), "app_state", None), "config", None)
    return getattr(getattr(config, "prestige", None), "user_agent_email", "") or None


def _failure_message(result: Any, estimate: int, kept: bool, partial_count: int) -> str:
    where = f"page {result.pages + 1} of ≈{estimate}"
    retry = ""
    if result.retry_after is not None:
        retry = f" · try after {(datetime.now() + timedelta(seconds=result.retry_after)).strftime('%H:%M')}"
    saved = f"; {partial_count} received so far saved apart" if partial_count else ""
    previous = "the previous list is kept" if kept else "no complete list was saved"
    return f"Download stopped at {where} ({result.failure}); {previous}{saved}{retry}"


def refresh(conf_id: str, year: int, *, http_client: Any = None) -> dict[str, Any]:
    """Download the edition. Failures come back as ``ok: False`` with the reason; the
    complete file is replaced only by a complete download."""
    conference, edition = _find(conf_id, year)
    if offline_requested():
        raise APIError("strict_offline", "Offline mode is on", status_code=409)
    if not _refresh_lock.acquire(blocking=False):
        raise APIError("refresh_in_progress", "An update is already running", status_code=409)
    try:
        recipe: Recipe = RECIPES[conference.recipe]
        estimate = estimate_requests(recipe, edition)
        result = download(recipe, edition, mailto=_mailto(), http_client=http_client)
        kept = store.load_complete(conf_id, year) is not None
        envelope = {"version": 1, "conference": conf_id, "year": year, "source": "crossref",
                    "window": {"from": edition.start.isoformat(), "until": edition.end.isoformat()},
                    "requests": result.pages, "reported_total": result.total, "skipped": result.skipped,
                    "records": result.records}
        if result.failure:
            message = _failure_message(result, estimate, kept, len(result.records))
            if result.records:
                store.save_partial(conf_id, year, {**envelope, "saved_at": now_iso_z(), "message": message})
            return {"ok": False, "message": message, "kept_previous": kept, "requests": result.pages}
        if not result.records:
            return {"ok": False, "message": "No papers found", "kept_previous": kept, "requests": result.pages}
        store.save_complete(conf_id, year, {**envelope, "fetched_at": now_iso_z()})
        return {"ok": True, "message": f"Saved {len(result.records)} abstracts", "kept_previous": kept,
                "requests": result.pages}
    finally:
        _refresh_lock.release()


def _number_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(int(p) if p.isdigit() else p.lower() for p in _NUMBER_PARTS.split(row["number"]))


def _row(recipe: Recipe, record: dict[str, Any]) -> dict[str, Any]:
    level, why = classify(recipe, record["number"])
    return {"doi": record["doi"], "number": record["number"], "title": record["title"], "authors": record["authors"],
            "level": level, "why": why, "url": f"https://doi.org/{record['doi']}",
            "has_abstract": bool(record["abstract"])}


def edition_view(conf_id: str, year: int, *, page: int, page_size: int, query: str) -> dict[str, Any]:
    """Late-breaking rows, then every row paged by title; ``query`` filters both by title words.
    Abstract text is not included (see ``abstract_of``)."""
    conference, edition = _find(conf_id, year)
    summary = _edition_summary(conference, edition)
    data = store.load_complete(conf_id, year)
    if data is None:
        return {"summary": summary, "downloaded": False}
    recipe = RECIPES[conference.recipe]
    rows = [_row(recipe, r) for r in data["records"]]
    words = query.casefold().split()
    shown = [r for r in rows if all(w in r["title"].casefold() for w in words)]
    shown.sort(key=lambda r: r["title"].casefold())
    pages = max(1, -(-len(shown) // page_size))
    page = min(max(page, 1), pages)
    return {
        "summary": summary, "downloaded": True, "skipped": data.get("skipped", 0),
        "counts": {"total": len(rows), "late_breaking": sum(r["level"] == LATE_BREAKING for r in rows),
                   "unknown": sum(r["level"] == UNKNOWN for r in rows)},
        "late_breaking": sorted((r for r in shown if r["level"] == LATE_BREAKING), key=_number_key),
        "items": shown[(page - 1) * page_size: page * page_size],
        "matching": len(shown), "page": page, "pages": pages,
    }


def abstract_of(conf_id: str, year: int, doi: str) -> str:
    _find(conf_id, year)
    for record in (store.load_complete(conf_id, year) or {}).get("records", []):
        if record["doi"] == doi:
            return record["abstract"]
    raise APIError("abstract_not_found", "No such abstract in this edition", status_code=404)
