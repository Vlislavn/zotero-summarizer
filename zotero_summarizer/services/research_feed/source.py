"""Source-neutral candidate loading; MVP adapter reads the existing app RSS store."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime
from itertools import islice

from zotero_summarizer.models import ResearchCandidate


def _parse_date(value: object) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text) if text else None


def _candidates(rows: Iterable[sqlite3.Row], venue: str) -> Iterator[ResearchCandidate]:
    for row in rows:
        publication = row["publication_title"] or row["feed_name"] or ""
        if venue.casefold() not in publication.casefold():
            continue
        yield ResearchCandidate(
            source_id=row["stable_feed_key"], source="app_rss", title=row["title"],
            abstract=row["abstract"] or "", url=row["canonical_url"] or row["url"] or "",
            doi=row["doi"] or None, published_at=_parse_date(row["publication_date"]),
            updated_at=_parse_date(row["updated_at"]),
            authors=[value.strip() for value in str(row["authors"] or "").split(";") if value.strip()],
            venue=publication or None,
        )


def load_candidates(db_path, *, start: datetime, end: datetime, limit: int, venue: str) -> list[ResearchCandidate]:
    """Newest unique venue matches; the result limit cannot be spent on duplicates."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT ri.*, rf.name AS feed_name
            FROM rss_items ri JOIN rss_feeds rf ON rf.id = ri.rss_feed_id
            WHERE datetime(COALESCE(NULLIF(ri.publication_date, ''), ri.created_at))
                  BETWEEN datetime(?) AND datetime(?)
            ORDER BY datetime(COALESCE(NULLIF(ri.publication_date, ''), ri.created_at)) DESC, ri.id DESC
            """,
            (start.isoformat(), end.isoformat()),
        )
        # ponytail: stream until the unique-result cap; large date windows may still need an indexed scan.
        return list(islice(deduplicate(_candidates(rows, venue)), limit))
    finally:
        conn.close()


def deduplicate(candidates: Iterable[ResearchCandidate]) -> Iterator[ResearchCandidate]:
    """DOI/source-id first, normalized title second; stable first-seen ordering."""
    seen: set[str] = set()
    for candidate in candidates:
        title = " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in candidate.title).split())
        key = (candidate.doi or candidate.source_id or title).strip().lower()
        fallback = f"title:{title}"
        if key in seen or fallback in seen:
            continue
        seen.update({key, fallback})
        yield candidate


__all__ = ["load_candidates", "deduplicate"]
