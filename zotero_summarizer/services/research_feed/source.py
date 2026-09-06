"""Source-neutral candidate loading; MVP adapter reads the existing app RSS store."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Protocol

from zotero_summarizer.models import ResearchCandidate


class CandidateSource(Protocol):
    def load(self, *, start: datetime, end: datetime, limit: int) -> list[ResearchCandidate]: ...


def _parse_date(value: object) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


class RssCandidateSource:
    """Bounded, reproducible adapter over app-owned ``rss_items``."""

    def __init__(self, db_path):
        self.db_path = db_path

    def load(self, *, start: datetime, end: datetime, limit: int) -> list[ResearchCandidate]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT ri.*, rf.name AS feed_name
                FROM rss_items ri JOIN rss_feeds rf ON rf.id = ri.rss_feed_id
                WHERE datetime(COALESCE(NULLIF(ri.publication_date, ''), ri.created_at))
                      BETWEEN datetime(?) AND datetime(?)
                ORDER BY datetime(COALESCE(NULLIF(ri.publication_date, ''), ri.created_at)), ri.id
                LIMIT ?
                """,
                (start.isoformat(), end.isoformat(), max(1, min(limit, 5000))),
            ).fetchall()
        finally:
            conn.close()
        return [ResearchCandidate(
            source_id=row["stable_feed_key"], source="app_rss", title=row["title"],
            abstract=row["abstract"] or "", url=row["canonical_url"] or row["url"] or "",
            doi=row["doi"] or None, published_at=_parse_date(row["publication_date"]),
            updated_at=_parse_date(row["updated_at"]),
            authors=[value.strip() for value in str(row["authors"] or "").split(";") if value.strip()],
            venue=row["publication_title"] or row["feed_name"] or None,
        ) for row in rows]


def deduplicate(candidates: list[ResearchCandidate]) -> list[ResearchCandidate]:
    """DOI/source-id first, normalized title second; stable first-seen ordering."""
    seen: set[str] = set()
    kept: list[ResearchCandidate] = []
    for candidate in candidates:
        title = " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in candidate.title).split())
        key = (candidate.doi or candidate.source_id or title).strip().lower()
        fallback = f"title:{title}"
        if key in seen or fallback in seen:
            continue
        seen.update({key, fallback})
        kept.append(candidate)
    return kept


__all__ = ["CandidateSource", "RssCandidateSource", "deduplicate"]
