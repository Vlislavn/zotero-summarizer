from __future__ import annotations

import asyncio
from typing import Any, Literal

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK

from zotero_summarizer.mcp.api_client import _api_request, _fetch_pending_rows, _fetch_triage_row
from zotero_summarizer.mcp.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, MAX_PENDING_FETCH
from zotero_summarizer.mcp.helpers import (
    _as_float,
    _as_int,
    _decode_search_cursor,
    _encode_search_cursor,
    _error,
    _extract_data_or_error,
    _extract_error_payload,
    _normalize_unique_strings,
    _ok,
    _require_non_empty_text,
)
from zotero_summarizer.mcp.parsers import (
    _paper_card_from_item,
    _parse_pending_change,
    _sort_paper_cards,
    _triage_from_result_row,
)
from zotero_summarizer.mcp.server import mcp


SEARCH_SOURCE_MULTIPLIER = 4
SEARCH_SOURCE_MAX_FETCH = 500
SEARCH_ENRICH_CONCURRENCY = 8
SEED_QUERY_TITLE_WORD_LIMIT = 8


def _clamp_score_bounds(
    score_min: float | None,
    score_max: float | None,
) -> tuple[float | None, float | None]:
    safe_score_min: float | None = None
    safe_score_max: float | None = None

    if score_min is not None:
        parsed = _as_float(score_min, 0.0)
        safe_score_min = max(0.0, min(5.0, parsed))
    if score_max is not None:
        parsed = _as_float(score_max, 5.0)
        safe_score_max = max(0.0, min(5.0, parsed))
    if safe_score_min is not None and safe_score_max is not None and safe_score_min > safe_score_max:
        safe_score_min, safe_score_max = safe_score_max, safe_score_min

    return safe_score_min, safe_score_max


async def _enrich_paper_card(
    item: dict[str, Any],
    semaphore: asyncio.Semaphore,
    triage_warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    async with semaphore:
        item_key = str(item.get("item_key") or "").strip()
        if not item_key:
            return _paper_card_from_item(item, None)

        triage_row, triage_error = await _fetch_triage_row(item_key)
        if triage_error:
            triage_warnings.append(
                {
                    "item_key": item_key,
                    "error": triage_error,
                }
            )

        triage_payload = _triage_from_result_row(triage_row)
        return _paper_card_from_item(item, triage_payload)


def _matches_search_filters(
    card: dict[str, Any],
    *,
    triaged: bool | None,
    normalized_priority: list[str],
    safe_score_min: float | None,
    safe_score_max: float | None,
) -> bool:
    triage_payload = card.get("triage") if isinstance(card.get("triage"), dict) else None

    if triaged is True and triage_payload is None:
        return False
    if triaged is False and triage_payload is not None:
        return False

    triage_priority = str((triage_payload or {}).get("reading_priority") or "")
    triage_score = _as_float((triage_payload or {}).get("composite_score"), -1.0)

    if normalized_priority and triage_priority not in normalized_priority:
        return False
    if safe_score_min is not None and triage_score < safe_score_min:
        return False
    if safe_score_max is not None and triage_score > safe_score_max:
        return False

    return True


def _next_search_cursor(
    *,
    source_offset: int,
    filtered_offset: int,
    safe_limit: int,
    filtered_count: int,
    source_count: int,
    source_total: int,
) -> str | None:
    next_filtered_offset = filtered_offset + safe_limit
    if next_filtered_offset < filtered_count:
        return _encode_search_cursor(source_offset, next_filtered_offset)

    if source_offset + source_count < source_total:
        return _encode_search_cursor(source_offset + source_count, 0)

    return None


@mcp.tool()
async def search_papers(
    query: str | None = None,
    collection: str | None = None,
    tag: str | None = None,
    priority: list[str] | None = None,
    score_min: float | None = None,
    score_max: float | None = None,
    triaged: bool | None = None,
    sort_by: Literal["relevance", "score", "priority", "recency", "title"] = "relevance",
    limit: int = DEFAULT_PAGE_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Search papers and optionally filter by triage metadata."""
    safe_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
    # Cursor encodes source_offset and intra-source filtered_offset as `source:filtered`.
    source_offset, filtered_offset = _decode_search_cursor(cursor)
    source_limit = min(SEARCH_SOURCE_MAX_FETCH, max(safe_limit * SEARCH_SOURCE_MULTIPLIER, safe_limit))
    normalized_priority = [
        value.lower()
        for value in _normalize_unique_strings(priority)
        if value.lower() in READING_PRIORITY_SORT_RANK
    ]
    safe_score_min, safe_score_max = _clamp_score_bounds(score_min, score_max)

    source_result = await _api_request(
        "GET",
        "/api/zotero/items",
        params={
            "collection": collection,
            "search": query,
            "tag": tag,
            "limit": source_limit,
            "offset": source_offset,
        },
    )
    source_data, source_error = _extract_data_or_error(source_result)
    if source_error is not None:
        return source_error
    source_items = list((source_data or {}).get("items") or [])
    source_total = _as_int((source_data or {}).get("total"), 0)

    semaphore = asyncio.Semaphore(SEARCH_ENRICH_CONCURRENCY)
    triage_warnings: list[dict[str, Any]] = []

    cards = await asyncio.gather(
        *[_enrich_paper_card(item, semaphore, triage_warnings) for item in source_items]
    )

    filtered = [
        card
        for card in cards
        if _matches_search_filters(
            card,
            triaged=triaged,
            normalized_priority=normalized_priority,
            safe_score_min=safe_score_min,
            safe_score_max=safe_score_max,
        )
    ]

    sorted_cards = _sort_paper_cards(filtered, sort_by)
    page_items = sorted_cards[filtered_offset : filtered_offset + safe_limit]

    next_cursor = _next_search_cursor(
        source_offset=source_offset,
        filtered_offset=filtered_offset,
        safe_limit=safe_limit,
        filtered_count=len(sorted_cards),
        source_count=len(source_items),
        source_total=source_total,
    )

    response_payload = {
        "items": page_items,
        "limit": safe_limit,
        "cursor": _encode_search_cursor(source_offset, filtered_offset),
        "next_cursor": next_cursor,
        "source_total": source_total,
        "filtered_count": len(sorted_cards),
    }
    if triage_warnings:
        response_payload["warnings"] = triage_warnings

    return _ok(**response_payload)


@mcp.tool()
async def get_paper(item_key: str) -> dict[str, Any]:
    """Get full paper detail, latest triage info, pending changes, and feedback."""
    safe_item_key, validation_error = _require_non_empty_text(item_key, "item_key")
    if validation_error is not None:
        return validation_error

    detail_result = await _api_request("GET", f"/api/zotero/items/{safe_item_key}")
    detail, detail_error = _extract_data_or_error(detail_result)
    if detail_error is not None:
        return detail_error

    triage_row, triage_error = await _fetch_triage_row(safe_item_key)
    triage_payload = _triage_from_result_row(triage_row)

    pending_rows, pending_error = await _fetch_pending_rows("all", MAX_PENDING_FETCH)
    pending_items: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if pending_error is None:
        pending_items = [
            _parse_pending_change(row)
            for row in pending_rows
            if str(row.get("item_key") or "").strip() == safe_item_key
        ]
    else:
        warnings.append(_extract_error_payload(pending_error))

    feedback_result = await _api_request(
        "GET",
        "/api/triage/feedback/latest",
        params={"item_keys": safe_item_key},
    )
    feedback: dict[str, Any] | None = None
    feedback_data, feedback_error = _extract_data_or_error(feedback_result)
    if feedback_error is None:
        feedback_items = list((feedback_data or {}).get("items") or [])
        feedback = feedback_items[0] if feedback_items else None
    else:
        warnings.append(_extract_error_payload(feedback_error))

    if triage_error:
        warnings.append(triage_error)

    payload = {
        "item": detail,
        "triage": triage_payload,
        "pending_changes": pending_items,
        "feedback": feedback,
    }
    if warnings:
        payload["warnings"] = warnings

    return _ok(**payload)


@mcp.tool()
async def find_similar_papers(
    item_key: str | None = None,
    query: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Return candidate similar papers using corpus metadata search."""
    safe_limit = max(1, min(int(limit), 50))
    safe_item_key = str(item_key or "").strip()
    safe_query = str(query or "").strip()

    if not safe_item_key and not safe_query:
        return _error("validation_error", "item_key or query is required")

    seed: dict[str, Any] | None = None
    if safe_item_key:
        seed_result = await _api_request("GET", f"/api/zotero/items/{safe_item_key}")
        seed, seed_error = _extract_data_or_error(seed_result)
        if seed_error is not None:
            return seed_error
        if seed and not safe_query:
            title = str(seed.get("title") or "").strip()
            safe_query = " ".join(title.split()[:SEED_QUERY_TITLE_WORD_LIMIT])

    corpus_result = await _api_request(
        "GET",
        "/api/corpus/items",
        params={
            "search": safe_query,
            "sort": "updated_at",
            "order": "desc",
            "limit": safe_limit + 5,
            "offset": 0,
        },
    )
    corpus_data, corpus_error = _extract_data_or_error(corpus_result)
    if corpus_error is not None:
        return corpus_error
    rows = list((corpus_data or {}).get("items") or [])

    similar: list[dict[str, Any]] = []
    for row in rows:
        candidate_key = str(row.get("item_id") or "").strip()
        if not candidate_key or candidate_key == safe_item_key:
            continue

        similar.append(
            {
                "item_key": candidate_key,
                "title": str(row.get("title") or ""),
                "collections": list(row.get("collections") or []),
                "tags": list(row.get("tags") or []),
                "engagement_weight": _as_float(row.get("engagement_weight"), 0.0),
                "updated_at": str(row.get("updated_at") or ""),
            }
        )

        if len(similar) >= safe_limit:
            break

    return _ok(
        seed={
            "item_key": safe_item_key,
            "title": str((seed or {}).get("title") or ""),
            "query": safe_query,
        },
        items=similar,
        total=_as_int((corpus_data or {}).get("total"), 0),
    )
