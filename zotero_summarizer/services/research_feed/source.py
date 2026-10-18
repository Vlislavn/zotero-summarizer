"""Source-neutral candidate loading; MVP adapter reads the existing app RSS store."""
from __future__ import annotations

import sqlite3
import json
import math
from collections.abc import Iterable, Iterator
from datetime import datetime
from itertools import islice

from zotero_summarizer.models import ResearchCandidate
from zotero_summarizer.domain import normalize_doi
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader


class CandidateReader(AppLibraryReader):
    """Explicit weekly selection, including RSS papers without processed history."""
    def __init__(self, db_path, candidates):
        super().__init__(db_path)
        self.candidates = {candidate.source_id: candidate for candidate in candidates}

    def get_item_detail(self, item_key):
        candidate = self.candidates[item_key]
        return {**(super().get_item_detail(item_key) or {}), "item_key": item_key,
                "title": candidate.title, "abstract": candidate.abstract, "doi": candidate.doi or "",
                "url": candidate.url, "authors": candidate.authors, "pdf_path": None, "has_pdf": False}


def number(value, default=0.0, minimum=0.0, maximum=5.0):
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("boolean is not a score")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError("stored score out of range")
    return result


def summary(row, field="summary"):
    payload = json.loads((row or {}).get("shap_contribs_json") or "{}")
    if not isinstance(payload, dict):
        raise ValueError("stored triage payload must be an object")
    value = payload.get(field, {})
    if not isinstance(value, dict):
        raise ValueError("stored triage field must be an object")
    return value


def admit_history(candidate, row):
    if row is None:
        return candidate
    if (row.get("stable_feed_key") != candidate.source_id or row.get("source_type") != "app_rss"
            or not candidate.title.strip() or row.get("title") != candidate.title):
        raise ValueError("triage history source/title mismatch")
    stored_doi, abstract = row.get("doi"), row.get("abstract")
    if stored_doi is not None and not isinstance(stored_doi, str):
        raise ValueError("triage history DOI invalid")
    if stored_doi and candidate.doi and normalize_doi(stored_doi) != normalize_doi(candidate.doi):
        raise ValueError("triage history DOI mismatch")
    if abstract is not None and not isinstance(abstract, str):
        raise ValueError("triage history abstract invalid")
    if abstract and candidate.abstract.strip() and abstract != candidate.abstract:
        raise ValueError("triage history abstract changed")
    return candidate.model_copy(update={"abstract": candidate.abstract or abstract or ""})


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
