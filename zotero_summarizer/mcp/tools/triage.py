from __future__ import annotations

from typing import Any, Literal

from zotero_summarizer.mcp.api_client import _api_request
from zotero_summarizer.mcp.config import DEFAULT_TRIAGE_SECONDS_PER_ITEM, MAX_TRIAGE_ITEM_KEYS
from zotero_summarizer.mcp.helpers import (
    _as_int,
    _error,
    _extract_data_or_error,
    _normalize_unique_strings,
    _now_iso,
    _ok,
    _require_non_empty_text,
)
from zotero_summarizer.mcp.server import mcp


@mcp.tool()
async def start_triage(item_keys: list[str], queue_changes: bool = True) -> dict[str, Any]:
    """Start async triage job for one or more Zotero item keys."""
    normalized_keys = _normalize_unique_strings(item_keys)
    if not normalized_keys:
        return _error("validation_error", "item_keys must contain at least one non-empty key")
    if len(normalized_keys) > MAX_TRIAGE_ITEM_KEYS:
        return _error(
            "batch_too_large",
            f"Too many item_keys: {len(normalized_keys)}. Max is {MAX_TRIAGE_ITEM_KEYS}",
        )

    run_result = await _api_request(
        "POST",
        "/api/triage/run",
        payload={"item_keys": normalized_keys, "queue_changes": bool(queue_changes)},
    )
    data, run_error = _extract_data_or_error(run_result)
    if run_error is not None:
        return run_error

    total_items = _as_int(data.get("total"), len(normalized_keys))
    return _ok(
        job_id=str(data.get("job_id") or ""),
        status=str(data.get("status") or "running"),
        total_items=total_items,
        estimated_seconds=total_items * DEFAULT_TRIAGE_SECONDS_PER_ITEM,
        created_at=_now_iso(),
    )


@mcp.tool()
async def get_job_status(job_id: str) -> dict[str, Any]:
    """Get triage job status and progress by job_id."""
    safe_job_id, validation_error = _require_non_empty_text(job_id, "job_id")
    if validation_error is not None:
        return validation_error

    job_result = await _api_request("GET", f"/api/triage/jobs/{safe_job_id}")
    data, job_error = _extract_data_or_error(job_result)
    if job_error is not None:
        return job_error

    total = max(0, _as_int(data.get("total"), 0))
    completed = max(0, _as_int(data.get("completed"), 0))
    progress_percent = round((completed / total) * 100.0, 1) if total else 0.0

    return _ok(
        job_id=str(data.get("job_id") or safe_job_id),
        status=str(data.get("status") or ""),
        total_items=total,
        completed_items=completed,
        progress_percent=progress_percent,
        current_item_key=str(data.get("current_item_key") or ""),
        current_title=str(data.get("current_title") or ""),
        results=list(data.get("results") or []),
        errors=list(data.get("errors") or []),
        started_at=str(data.get("started_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
    )


@mcp.tool()
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel a running triage job."""
    safe_job_id, validation_error = _require_non_empty_text(job_id, "job_id")
    if validation_error is not None:
        return validation_error

    cancel_result = await _api_request("POST", f"/api/triage/jobs/{safe_job_id}/cancel")
    data, cancel_error = _extract_data_or_error(cancel_result)
    if cancel_error is not None:
        return cancel_error

    return _ok(
        job_id=str(data.get("job_id") or safe_job_id),
        status=str(data.get("status") or ""),
        cancelled=bool(data.get("cancelled")),
        already_done=bool(data.get("already_done")),
    )


@mcp.tool()
async def submit_feedback(item_key: str, verdict: Literal["approve", "reject"]) -> dict[str, Any]:
    """Submit explicit triage feedback for one paper."""
    safe_item_key, validation_error = _require_non_empty_text(item_key, "item_key")
    if validation_error is not None:
        return validation_error

    feedback_result = await _api_request(
        "POST",
        f"/api/triage/results/{safe_item_key}/feedback",
        payload={"verdict": verdict},
    )
    data, feedback_error = _extract_data_or_error(feedback_result)
    if feedback_error is not None:
        return feedback_error

    return _ok(
        item_key=str(data.get("item_key") or data.get("item_id") or safe_item_key),
        verdict=str(data.get("verdict") or verdict),
        signal=str(data.get("signal") or ""),
        queued=_as_int(data.get("queued"), 0),
    )
