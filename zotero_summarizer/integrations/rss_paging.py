"""Page collection for RSS/Atom sources that cut a response off (arXiv API).

Pure helpers + one loop; the caller supplies the fetch/parse callable. Nothing is
sliced before dedupe: every entry at or after the cutoff is returned and the
storage upsert dedupes. Only a *confirmed* boundary (an older-than-cutoff entry,
an empty page or the source's reported total) lets the caller advance a watermark.
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ARXIV_PAUSE_SECS = 3.0  # arXiv API terms: one request per 3 seconds


def is_paginated_arxiv(url: str) -> bool:
    """arXiv API query sorted newest-first by submission: ``start`` paging is safe."""
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query))
    return (parts.hostname or "").endswith("arxiv.org") and parts.path == "/api/query" \
        and q.get("sortBy") == "submittedDate" and q.get("sortOrder") == "descending"


def page_url(url: str, start: int) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != "start"] + [("start", str(start))]
    return urlunsplit(parts._replace(query=urlencode(q)))


def entry_time(entry: Any) -> str:
    """UTC ISO second of the entry's published/updated time, '' when undated."""
    tm = entry.get("published_parsed") or entry.get("updated_parsed")
    if not tm:
        return ""
    return datetime.fromtimestamp(calendar.timegm(tm), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cutoff_iso(watermark: str | None, backfill_days: int) -> str:
    horizon = (datetime.now(timezone.utc) - timedelta(days=max(1, int(backfill_days)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    return max(horizon, watermark or "")


def collect_pages(
    fetch_page: Callable[[str], tuple[list[Any], int | None]],
    url: str,
    first: tuple[list[Any], int | None],
    *,
    cutoff: str,
    max_pages: int,
    pause: float = ARXIV_PAUSE_SECS,
) -> tuple[list[Any], bool]:
    """Collect entries newest-first until ``cutoff``; returns ``(entries, complete)``.

    ``first`` is the already-fetched first page ``(entries, total_results)``. A
    page error after the first keeps what was gathered and reports incomplete.
    Entries older than the cutoff are dropped (the horizon), equal times kept."""
    entries, total = first
    kept = [e for e in entries if not entry_time(e) or entry_time(e) >= cutoff]
    got = len(entries)
    if not entries or any(entry_time(e) and entry_time(e) < cutoff for e in entries):
        return kept, True
    for _ in range(max(0, max_pages - 1)):
        if total is not None and got >= total:
            return kept, True
        time.sleep(pause)
        try:
            page, total = fetch_page(page_url(url, got))
        except Exception:  # noqa: BLE001 — keep progress; caller flags incomplete
            return kept, False
        if not page:
            return kept, True
        kept += [e for e in page if not entry_time(e) or entry_time(e) >= cutoff]
        got += len(page)
        if any(entry_time(e) and entry_time(e) < cutoff for e in page):
            return kept, True
    return kept, total is not None and got >= total
