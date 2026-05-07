from __future__ import annotations

import json
from typing import Any

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK, normalize_reading_priority

from zotero_summarizer.mcp.helpers import _as_float, _as_int, _normalize_authors


def _parse_response_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _parse_pending_change(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    return {
        "change_id": _as_int(row.get("id")),
        "item_key": str(row.get("item_key") or ""),
        "item_title": str(row.get("item_title") or ""),
        "change_type": str(row.get("change_type") or ""),
        "payload": payload,
        "status": str(row.get("status") or ""),
        "error_message": str(row.get("error_message") or ""),
        "created_at": str(row.get("created_at") or ""),
        "applied_at": str(row.get("applied_at") or ""),
    }


def _triage_from_result_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None

    response_json = _parse_response_json(row.get("response_json"))
    priority = str(
        row.get("forced_priority")
        or row.get("reading_priority")
        or response_json.get("reading_priority")
        or ""
    ).strip()
    priority = normalize_reading_priority(priority) if priority else ""

    composite_score = _as_float(
        row.get("composite_score"),
        _as_float(response_json.get("composite_relevance_score"), 0.0),
    )
    triage_confidence = _as_float(
        row.get("confidence"),
        _as_float(response_json.get("triage_confidence"), 0.0),
    )

    return {
        "relevance_score": _as_int(
            row.get("relevance_score"),
            _as_int(response_json.get("relevance_score"), 0),
        ),
        "composite_score": composite_score,
        "reading_priority": priority,
        "confidence": triage_confidence,
        "matched_goal": str(response_json.get("matched_goal") or ""),
        "corpus_affinity": _as_float(response_json.get("corpus_affinity_score"), 0.0),
        "dimensions": response_json.get("triage_dimensions") or {},
        "triaged_at": str(row.get("created_at") or ""),
        "summary": {
            "executive_summary": str(response_json.get("executive_summary") or ""),
            "key_findings": response_json.get("key_findings") or [],
            "methods": str(response_json.get("methods") or ""),
            "limitations": str(response_json.get("limitations") or ""),
            "triage_rationale": str(response_json.get("triage_rationale") or ""),
        },
    }


def _paper_card_from_item(item: dict[str, Any], triage: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "item_key": str(item.get("item_key") or ""),
        "title": str(item.get("title") or "Untitled"),
        "authors": _normalize_authors(item.get("authors")),
        "abstract": str(item.get("abstract") or ""),
        "publication_date": str(item.get("publication_date") or ""),
        "tags": list(item.get("tags") or []),
        "collections": list(item.get("collections") or []),
        "has_pdf": bool(item.get("has_pdf")),
        "reading_priority": str(item.get("reading_priority") or ""),
        "date_added": str(item.get("date_added") or ""),
        "date_modified": str(item.get("date_modified") or ""),
        "triage": triage,
    }


def _sort_paper_cards(cards: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    if sort_by == "score":
        return sorted(
            cards,
            key=lambda card: _as_float((card.get("triage") or {}).get("composite_score"), -1.0),
            reverse=True,
        )

    if sort_by == "priority":
        return sorted(
            cards,
            key=lambda card: (
                READING_PRIORITY_SORT_RANK.get(
                    str((card.get("triage") or {}).get("reading_priority") or ""),
                    0,
                ),
                _as_float((card.get("triage") or {}).get("composite_score"), -1.0),
            ),
            reverse=True,
        )

    if sort_by == "recency":
        return sorted(cards, key=lambda card: str(card.get("date_modified") or ""), reverse=True)

    if sort_by == "title":
        return sorted(cards, key=lambda card: str(card.get("title") or "").casefold())

    return cards
