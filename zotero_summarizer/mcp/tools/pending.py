from __future__ import annotations

from typing import Any, Literal, TypedDict

from zotero_summarizer.mcp.api_client import _api_request, _fetch_pending_rows
from zotero_summarizer.mcp.config import MAX_PENDING_FETCH
from zotero_summarizer.mcp.helpers import _as_int, _decode_cursor, _encode_cursor, _error, _extract_data_or_error, _ok
from zotero_summarizer.mcp.parsers import _parse_pending_change
from zotero_summarizer.mcp.server import mcp


BACKEND_PENDING_MAX_LIMIT = 5000
APPLY_BATCH_SIZE = 1000


class _PendingTotalResult(TypedDict):
    total: int | None
    error: dict[str, Any] | None


def _normalize_change_ids(change_ids: list[int] | None) -> list[int]:
    normalized_ids: list[int] = []
    if not change_ids:
        return normalized_ids

    seen: set[int] = set()
    for change_id in change_ids:
        numeric = _as_int(change_id, 0)
        if numeric <= 0 or numeric in seen:
            continue
        seen.add(numeric)
        normalized_ids.append(numeric)
    return normalized_ids


async def _pending_total(status: str) -> _PendingTotalResult:
    if status == "all":
        return {"total": None, "error": None}

    count_result = await _api_request(
        "GET",
        "/api/pending/count",
        params={"status": status},
    )
    count_data, count_error = _extract_data_or_error(count_result)
    if count_error is not None:
        return {"total": None, "error": count_error}
    return {"total": max(0, _as_int((count_data or {}).get("count"), 0)), "error": None}


def _chunk_ids(change_ids: list[int], size: int) -> list[list[int]]:
    safe_size = max(1, int(size))
    return [change_ids[index : index + safe_size] for index in range(0, len(change_ids), safe_size)]


async def _collect_pending_ids_for_apply(change_ids: list[int] | None) -> tuple[list[int], dict[str, Any] | None]:
    normalized_ids = _normalize_change_ids(change_ids)
    if normalized_ids:
        return normalized_ids, None

    pending_total_result = await _pending_total("pending")
    pending_total = pending_total_result["total"]
    pending_error = pending_total_result["error"]
    if pending_error is not None:
        return [], pending_error
    if pending_total == 0:
        return [], None

    pending_rows, fetch_error = await _fetch_pending_rows(
        "pending",
        min(BACKEND_PENDING_MAX_LIMIT, max(1, pending_total or MAX_PENDING_FETCH)),
    )
    if fetch_error is not None:
        return [], fetch_error

    normalized_ids = _normalize_change_ids([_as_int(row.get("id"), 0) for row in pending_rows])
    if pending_total and len(normalized_ids) < pending_total:
        return [], _error(
            "pending_fetch_incomplete",
            "Cannot apply all pending changes because backend list limit was reached",
            details={
                "pending_total": pending_total,
                "retrieved": len(normalized_ids),
                "max_limit": BACKEND_PENDING_MAX_LIMIT,
            },
        )

    return normalized_ids, None


async def _apply_pending_chunks(change_ids: list[int], force: bool) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    total_applied = 0
    total_failed = 0
    total_inbox_removed = 0
    failed_items: list[Any] = []
    backup_paths: list[str] = []
    chunk_count = 0

    for chunk in _chunk_ids(change_ids, APPLY_BATCH_SIZE):
        chunk_count += 1
        apply_result = await _api_request(
            "POST",
            "/api/pending/apply",
            payload={"change_ids": chunk, "force": bool(force)},
        )
        data, apply_error = _extract_data_or_error(apply_result)
        if apply_error is not None:
            error_payload = apply_error.get("error")
            if isinstance(error_payload, dict):
                details = error_payload.get("details") if isinstance(error_payload.get("details"), dict) else {}
                details.update(
                    {
                        "processed_chunks": chunk_count - 1,
                        "partial_applied": total_applied,
                        "partial_failed": total_failed,
                    }
                )
                error_payload["details"] = details
            return None, apply_error

        total_applied += _as_int(data.get("applied"), 0)
        total_failed += _as_int(data.get("failed"), 0)
        total_inbox_removed += _as_int(data.get("inbox_removed"), 0)
        failed_items.extend(list(data.get("failed_items") or []))

        backup_path = data.get("backup_path")
        if isinstance(backup_path, str) and backup_path:
            backup_paths.append(backup_path)

    return {
        "applied": total_applied,
        "failed": total_failed,
        "backup_path": backup_paths[0] if backup_paths else None,
        "backup_paths": backup_paths,
        "failed_items": failed_items,
        "inbox_removed": total_inbox_removed,
        "batches": chunk_count,
    }, None


@mcp.tool()
async def list_pending_changes(
    status: Literal["pending", "applied", "rejected", "failed", "all"] = "pending",
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List pending or historical queued changes with cursor pagination."""
    safe_status = str(status or "pending").strip().lower() or "pending"
    safe_limit = max(1, min(int(limit), 200))
    offset = _decode_cursor(cursor)

    total_result = await _pending_total(safe_status)
    total_count = total_result["total"]
    count_error = total_result["error"]
    if count_error is not None:
        return count_error
    if total_count == 0:
        return _ok(
            items=[],
            total=0,
            limit=safe_limit,
            cursor=_encode_cursor(offset),
            next_cursor=None,
        )

    fetch_limit = max(safe_limit, offset + safe_limit + 1)
    if total_count is not None:
        fetch_limit = min(total_count, fetch_limit)
    fetch_limit = min(BACKEND_PENDING_MAX_LIMIT, fetch_limit)

    rows, pending_error = await _fetch_pending_rows(safe_status, fetch_limit)
    if pending_error is not None:
        return pending_error

    parsed = [_parse_pending_change(row) for row in rows]
    warnings: list[dict[str, Any]] = []

    page_items = parsed[offset : offset + safe_limit]
    if total_count is None:
        total = len(parsed)
        has_more = len(parsed) > offset + safe_limit
        if fetch_limit >= BACKEND_PENDING_MAX_LIMIT and len(parsed) >= BACKEND_PENDING_MAX_LIMIT:
            warnings.append(
                {
                    "code": "pending_pagination_truncated",
                    "message": "Pending pagination is limited by backend maximum page size",
                    "details": {"max_limit": BACKEND_PENDING_MAX_LIMIT},
                }
            )
            has_more = False
    else:
        total = total_count
        available = len(parsed)
        if total_count > BACKEND_PENDING_MAX_LIMIT and fetch_limit >= BACKEND_PENDING_MAX_LIMIT:
            total = BACKEND_PENDING_MAX_LIMIT
            warnings.append(
                {
                    "code": "pending_pagination_truncated",
                    "message": "Backend pending endpoint is capped by maximum page size",
                    "details": {
                        "reported_total": total_count,
                        "reachable_total": total,
                        "max_limit": BACKEND_PENDING_MAX_LIMIT,
                    },
                }
            )
        if available < min(total_count, fetch_limit):
            total = available
            warnings.append(
                {
                    "code": "pending_pagination_truncated",
                    "message": "Backend pending endpoint capped results below reported count",
                    "details": {
                        "reported_total": total_count,
                        "available": available,
                        "max_limit": BACKEND_PENDING_MAX_LIMIT,
                    },
                }
            )
        has_more = offset + safe_limit < total and bool(page_items)

    next_cursor = _encode_cursor(offset + safe_limit) if has_more else None

    response = _ok(
        items=page_items,
        total=total,
        limit=safe_limit,
        cursor=_encode_cursor(offset),
        next_cursor=next_cursor,
    )
    if warnings:
        response["warnings"] = warnings
    return response


@mcp.tool()
async def apply_pending_changes(
    change_ids: list[int] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Apply queued changes to Zotero. If change_ids is empty, applies all pending."""
    normalized_ids, collect_error = await _collect_pending_ids_for_apply(change_ids)
    if collect_error is not None:
        return collect_error

    if not normalized_ids:
        return _ok(applied=0, failed=0, message="No pending changes to apply")

    apply_summary, apply_error = await _apply_pending_chunks(normalized_ids, bool(force))
    if apply_error is not None:
        return apply_error

    summary = apply_summary or {}
    return _ok(requested_change_ids=normalized_ids, **summary)
