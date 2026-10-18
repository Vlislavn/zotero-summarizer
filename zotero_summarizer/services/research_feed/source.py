"""Source-neutral candidate loading; MVP adapter reads the existing app RSS store."""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime
from itertools import islice
from typing import Any, Literal

from zotero_summarizer.domain import normalize_doi
from zotero_summarizer.models import ResearchCandidate


def _summary(
    row: dict[str, Any] | None, *, field: Literal["summary", "aux_context"] = "summary",
) -> dict[str, Any]:
    """Select a consumed object from the one stored-history JSON boundary."""
    if field not in {"summary", "aux_context"}:
        raise ValueError("invalid stored triage field")
    raw = (row or {}).get("shap_contribs_json")
    if raw is None:
        return {}
    if not isinstance(raw, str):
        raise ValueError("stored triage summary is invalid JSON")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("stored triage summary is invalid JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("stored triage summary must be a JSON object")
    summary = payload.get(field)
    if summary is None:
        return {}
    if not isinstance(summary, dict):
        raise ValueError(f"stored triage {field} field must be an object")
    return summary


def _number(value: object, *, default: float, maximum: float, minimum: float = 0.0) -> float:
    """Convert only a consumed raw number; None uses an operational default."""
    if value is None:
        return default
    if type(value) not in (int, float, str):
        raise ValueError("stored triage number is invalid")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError("stored triage number is invalid") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("stored triage number is invalid")
    return number


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


def _identity_reason(candidate: ResearchCandidate, row: dict[str, Any]) -> str | None:
    if row.get("stable_feed_key") != candidate.source_id:
        return "history_source_key_mismatch"
    if row.get("source_type") != "app_rss":
        return "history_source_type_unverifiable"
    if not candidate.title.strip() or row.get("title") != candidate.title:
        return "history_title_mismatch"
    stored_doi = row.get("doi")
    if stored_doi is not None and not isinstance(stored_doi, str):
        return "history_doi_mismatch"
    if (candidate.doi and candidate.doi.strip() and stored_doi and stored_doi.strip()
            and normalize_doi(candidate.doi) != normalize_doi(stored_doi)):
        return "history_doi_mismatch"
    return None


def _abstract_reason(rss: str, stored: object) -> str | None:
    if stored is not None and not isinstance(stored, str):
        return "history_abstract_unverifiable"
    if rss.strip() and stored and stored.strip() and rss != stored:
        return "history_abstract_conflict"
    return None


def _row_provenance(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row[key] if isinstance(row[key], str) else None
              for key in ("run_id", "model_version", "created_at", "updated_at") if key in row}
    if type(row.get("id")) is int:
        result["row_id"] = row["id"]
    return result


def admit_history(
    candidate: ResearchCandidate, row: dict[str, Any] | None,
) -> tuple[ResearchCandidate, dict[str, Any]]:
    """Admit one whole latest row, without attesting freshness or changing triage."""
    entry = {
        "history": "missing", "reason": "no_prior_history",
        "text_basis": "rss" if candidate.abstract.strip() else "not_available",
        "currentness": "not_attested", "content_version": "not_attested",
    }
    if row is None:
        return candidate, entry
    reason = _identity_reason(candidate, row) or _abstract_reason(candidate.abstract, row.get("abstract"))
    if reason:
        entry.update(history="withheld", reason=reason)
        return candidate, entry
    entry.update(history="accepted", reason="identity_compatible", **_row_provenance(row))
    stored = row.get("abstract") or ""
    if not candidate.abstract.strip() and stored.strip():
        entry.update(text_basis="stored", original_rss_abstract=candidate.abstract)
        candidate = candidate.model_copy(update={"abstract": stored})
    return candidate, entry


def _candidate_locations(records, missing, rejects, unqualified):
    for index, record in enumerate(records):
        yield record["candidate"], f"/cards/{index}/candidate"
    for index, candidate in enumerate(missing):
        yield candidate, f"/manual_full_text_required/{index}"
    for index, row in enumerate(unqualified):
        yield row["candidate"], f"/unqualified_reviews/{index}/candidate"
    for index, (candidate, _triage) in enumerate(rejects):
        yield candidate.model_dump(mode="json"), f"/rejected_near_threshold/{index}/candidate"


def source_history_metadata(candidates, history, records, missing, rejects, unqualified=()):
    """Give each admitted effective input one published body or an exact JSON pointer."""
    represented = list(_candidate_locations(records, missing, rejects, unqualified))
    for candidate in candidates:
        entry = history[candidate.source_id]
        body = candidate.model_dump(mode="json")
        reference = next((pointer for value, pointer in represented if value == body), None)
        if reference is None:
            entry["candidate"] = body
        else:
            entry["candidate_ref"] = reference
    return history


__all__ = ["load_candidates", "deduplicate", "admit_history", "source_history_metadata"]
