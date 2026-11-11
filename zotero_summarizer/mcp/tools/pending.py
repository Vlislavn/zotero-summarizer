from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from zotero_summarizer.mcp.api_client import _api_request, _fetch_pending_rows
from zotero_summarizer.mcp.helpers import _as_int, _decode_cursor, _encode_cursor, _error, _extract_data_or_error, _ok
from zotero_summarizer.mcp.parsers import _parse_pending_change
from zotero_summarizer.mcp.server import mcp


BACKEND_PENDING_MAX_LIMIT = 5000
APPLY_MAX_IDS = 1000
_SHA256 = r"^[0-9a-f]{64}$"


class _ApprovalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    database_path: str = Field(min_length=1)
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    library_id: int = Field(ge=0)
    library_type: Literal["user"]


class _ApprovalPreview(BaseModel):
    """Wire envelope only; MCP neither constructs nor hashes approval authority."""
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    change_id: int = Field(gt=0)
    item_key: str = Field(min_length=1)
    change_type: str = Field(min_length=1)
    payload: dict[str, Any]
    edit_revision: int = Field(ge=0)
    target: _ApprovalTarget
    generated_preview_sha256: str | None = Field(pattern=_SHA256, max_length=64)
    preview_sha256: str = Field(pattern=_SHA256, max_length=64)

    @field_validator("item_key", "change_type")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Approval identity must be nonblank")
        return value


class _PendingTotalResult(TypedDict):
    total: int | None
    error: dict[str, Any] | None


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


def _validate_apply_selection(
    change_ids: list[int], previews: dict[str, str],
) -> tuple[list[int], dict[str, Any] | None]:
    if not isinstance(change_ids, list) or any(type(value) is not int or value <= 0 for value in change_ids):
        return [], _error("validation_error", "change_ids must be an explicit list of positive integers")
    if len(change_ids) > APPLY_MAX_IDS:
        return [], _error("validation_error", "At most 1000 change IDs may be applied; no automatic chunking")
    normalized = list(dict.fromkeys(change_ids))
    if (not isinstance(previews, dict) or set(previews) != {str(rid) for rid in normalized}
            or any(type(value) is not str or re.fullmatch(_SHA256, value) is None for value in previews.values())):
        return [], _error("validation_error", "previews must map exactly the selected IDs to presented SHA256 hashes")
    return normalized, None


def _verify_approval_preview(row: dict[str, Any], supplied_hash: str) -> dict[str, Any] | None:
    raw = row.get("approval_preview")
    try:
        if not isinstance(raw, dict) or type(raw.get("version")) is not int:
            raise ValueError("Missing versioned approval envelope")
        preview = _ApprovalPreview.model_validate(raw)
        payload = row.get("payload_json")
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if (preview.change_id != row["id"] or preview.item_key != row.get("item_key")
                or preview.change_type != row.get("change_type") or not isinstance(payload, dict)
                or json.dumps(preview.payload, sort_keys=True, allow_nan=False)
                != json.dumps(payload, sort_keys=True, allow_nan=False)):
            raise ValueError("Approval envelope does not bind the listed row")
    except (ValidationError, ValueError, TypeError):
        return _error("backend_contract_error", "Backend must supply a complete row-bound approval envelope",
                      details={"change_id": row["id"]})
    if preview.preview_sha256 != supplied_hash:
        return _error("pending_write_conflict", "Presented approval is stale; inspect and explicitly reselect",
                      details={"change_id": row["id"]})
    return None


async def _apply_pending_batch(
    change_ids: list[int], previews: dict[str, str], force: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    apply_result = await _api_request(
        "POST", "/api/pending/apply",
        payload={"change_ids": change_ids, "previews": {str(rid): previews[str(rid)] for rid in change_ids},
                 "force": bool(force)},
    )
    data, apply_error = _extract_data_or_error(apply_result)
    if apply_error is None and any(type(data.get(key)) is not int or data[key] < 0 for key in ("applied", "failed")):
        apply_error = _error("backend_contract_error", "Apply response must report non-negative applied/failed counts")
    if apply_error is not None:
        apply_error["error"].setdefault("details", {}).update(unconfirmed_change_ids=change_ids)
        apply_error["error"]["retryable"] = False
        return None, apply_error
    backup = data.get("backup_path")
    backup_paths = [backup] if isinstance(backup, str) and backup else []
    return {
        "applied": data["applied"], "failed": data["failed"],
        "backup_path": backup_paths[0] if backup_paths else None,
        "backup_paths": backup_paths, "failed_items": list(data.get("failed_items") or []), "batches": 1,
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


# Unreviewed operations stay human-only, including new writer types.
MCP_ALLOWED_CHANGE_TYPES = frozenset({"tag_changes", "add_note", "add_to_collection", "remove_from_collection"})


def _is_restricted_change_type(change_type: str) -> bool:
    return str(change_type or "").strip().lower() not in MCP_ALLOWED_CHANGE_TYPES


async def _preflight_apply(
    change_ids: list[int], previews: dict[str, str],
) -> tuple[list[int], list[dict[str, Any]], dict[str, Any] | None]:
    """Verify all requested rows before POST, without deriving new authority from GET."""
    pending_rows, fetch_error = await _fetch_pending_rows("all", BACKEND_PENDING_MAX_LIMIT)
    if fetch_error is not None:
        return [], [], fetch_error
    if any(not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 for row in pending_rows):
        return [], [], _error("backend_contract_error", "Pending rows must have positive integer IDs")
    rows_by_id = {row["id"]: row for row in pending_rows}
    if len(rows_by_id) != len(pending_rows):
        return [], [], _error("backend_contract_error", "Pending rows must have unique IDs")
    missing = [rid for rid in change_ids if rid not in rows_by_id]
    if missing:
        return [], [], _error(
            "pending_fetch_incomplete",
            "Cannot verify every requested change; nothing was applied",
            details={"missing_change_ids": missing, "max_limit": BACKEND_PENDING_MAX_LIMIT},
        )
    allowed: list[int] = []
    blocked: list[dict[str, Any]] = []
    for rid in change_ids:
        row = rows_by_id[rid]
        if _is_restricted_change_type(row.get("change_type")):
            blocked.append({"id": rid, "change_type": row.get("change_type")})
        else:
            allowed.append(rid)
    for rid in allowed:
        error = _verify_approval_preview(rows_by_id[rid], previews[str(rid)])
        if error is not None:
            return allowed, blocked, error
    return allowed, blocked, None


@mcp.tool()
async def apply_pending_changes(
    change_ids: list[StrictInt],
    previews: dict[str, str],
    force: bool = False,
) -> dict[str, Any]:
    """Apply explicit IDs with caller-presented approval_preview hashes (maximum 1000).

    Empty IDs plus an empty map writes nothing. GET verifies current envelopes;
    it never supplies approval. Only tags, add_note and collection membership
    are allowed. No discovery, chunking or automatic retry; inspect uncertainty
    and use the UI for freshly reviewed failed-row retries. Force preserves hashes.
    """
    normalized_ids, selection_error = _validate_apply_selection(change_ids, previews)
    if selection_error is not None:
        return selection_error

    if not normalized_ids:
        return _ok(applied=0, failed=0, message="No pending changes to apply")

    safe_ids, blocked, preflight_error = await _preflight_apply(normalized_ids, previews)
    if preflight_error is not None:
        preflight_error["error"].setdefault("details", {}).update(
            requested_change_ids=safe_ids, mcp_blocked_change_ids=blocked,
        )
        return preflight_error
    if not safe_ids:
        return _error(
            "mcp_restricted",
            "All requested changes are outside the MCP write allowlist. "
            "Use the CLI or web UI to apply.",
            details={"blocked": blocked},
        )

    summary, apply_error = await _apply_pending_batch(safe_ids, previews, bool(force))
    if apply_error is not None:
        apply_error["error"].setdefault("details", {}).update(
            requested_change_ids=safe_ids, mcp_blocked_change_ids=blocked,
        )
        return apply_error

    response = _ok(requested_change_ids=safe_ids, **summary)
    if blocked:
        # Surface the skips so the user (or agent) knows why some IDs didn't apply.
        response["mcp_blocked_change_ids"] = blocked
    if summary["failed"]:
        return _error(
            "pending_apply_failed",
            "Application was incomplete; inspect failed_items before explicitly retrying in the UI",
            details={key: value for key, value in response.items() if key != "ok"},
        )
    return response
