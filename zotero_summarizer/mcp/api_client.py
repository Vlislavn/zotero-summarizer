from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Literal

import httpx

from zotero_summarizer.mcp.config import API_BASE_URL, REQUEST_TIMEOUT_SECONDS
from zotero_summarizer.mcp.helpers import (
    _as_int,
    _clean_query_params,
    _error,
    _extract_data_or_error,
    _extract_retry_after,
    _is_retryable,
    _now_iso,
    _ok,
)


async def _fetch_pending_rows(status: str, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    pending_result = await _api_request(
        "GET",
        "/api/pending",
        params={"status": status, "limit": limit},
    )
    pending_data, pending_error = _extract_data_or_error(pending_result)
    if pending_error is not None:
        return [], pending_error
    rows = list((pending_data or {}).get("items") or [])
    return rows, None


def _base_item_update_payload(data: dict[str, Any], fallback_item_key: str) -> dict[str, Any]:
    return {
        "item_key": str(data.get("item_key") or fallback_item_key),
        "updated": _as_int(data.get("updated"), 0),
        "message": str(data.get("message") or ""),
        "item": data.get("item") if isinstance(data.get("item"), dict) else None,
    }


async def _resource_json_from_api(path: str, builder: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    api_result = await _api_request("GET", path)
    data, error = _extract_data_or_error(api_result)
    if error is not None:
        return json.dumps(error, ensure_ascii=False, indent=2)
    payload = builder(data or {})
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_application_error_code_message(raw_error: Any, parsed: dict[str, Any]) -> tuple[str, str]:
    if isinstance(raw_error, dict):
        code = str(raw_error.get("code") or "application_error")
        message = str(raw_error.get("message") or parsed.get("message") or "Request failed")
        return code, message

    code = str(raw_error)
    message = str(parsed.get("message") or "Request failed")
    return code, message


def _merge_application_error_details(raw_error: Any, parsed: dict[str, Any]) -> dict[str, Any] | None:
    details = parsed.get("details") if isinstance(parsed.get("details"), dict) else None

    if isinstance(raw_error, dict):
        nested_details = raw_error.get("details")
        if isinstance(nested_details, dict):
            details = {**(details or {}), **nested_details}

    if "requires_force" in parsed:
        details = {**(details or {}), "requires_force": bool(parsed.get("requires_force"))}

    return details


def _extract_application_error(parsed: dict[str, Any], status_code: int) -> dict[str, Any] | None:
    raw_error = parsed.get("error")
    if raw_error in (None, ""):
        return None

    # Some mutation endpoints return HTTP 200 with requires_force and an error payload.
    # Treat only those force-confirmation envelopes as errors to avoid misclassifying
    # valid status payloads that include informational `error` fields.
    if "requires_force" not in parsed and not isinstance(raw_error, dict):
        return None

    code, message = _parse_application_error_code_message(raw_error, parsed)
    details = _merge_application_error_details(raw_error, parsed)

    return _error(
        code,
        message,
        retryable=_is_retryable(code, status_code),
        status_code=status_code,
        details=details,
    )


async def _api_request(
    method: Literal["GET", "POST", "PUT"],
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{API_BASE_URL}{path}"
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    cleaned_params = _clean_query_params(params or {})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, params=cleaned_params, json=payload)
    except httpx.TimeoutException:
        return _error(
            "backend_timeout",
            "The Zotero summarizer API timed out",
            retryable=True,
            retry_after_sec=30,
        )
    except httpx.HTTPError as exc:
        return _error(
            "backend_unreachable",
            str(exc),
            retryable=True,
            retry_after_sec=10,
        )

    parsed: dict[str, Any]
    if response.content:
        try:
            decoded = response.json()
            parsed = decoded if isinstance(decoded, dict) else {"value": decoded}
        except ValueError:
            parsed = {"raw_text": "Non-JSON response from backend"}
    else:
        parsed = {}

    if response.status_code >= 400:
        code = str(parsed.get("error") or f"http_{response.status_code}")
        message = str(parsed.get("message") or parsed.get("raw_text") or "Request failed")
        details = parsed.get("details") if isinstance(parsed.get("details"), dict) else None
        retry_after = _extract_retry_after(response)
        return _error(
            code,
            message,
            retryable=_is_retryable(code, response.status_code),
            retry_after_sec=retry_after,
            status_code=response.status_code,
            details=details,
        )

    application_error = _extract_application_error(parsed, response.status_code)
    if application_error is not None:
        return application_error

    return _ok(data=parsed)


def _snapshot_data_or_warn(
    result: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if result.get("ok"):
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    warnings.append(result.get("error") or {})
    return None


async def _fetch_triage_row(item_key: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    result = await _api_request("GET", f"/api/results/{item_key}")
    if result.get("ok"):
        data = result.get("data")
        if isinstance(data, dict):
            return data, None
        return None, None

    error = result.get("error")
    if not isinstance(error, dict):
        return None, {
            "code": "backend_error",
            "message": "Unexpected triage response error format",
            "retryable": True,
        }

    if _as_int(error.get("status_code")) == 404 or str(error.get("code") or "") == "not_found":
        return None, None

    return None, error


async def _collect_status_snapshot() -> dict[str, Any]:
    status_result, pending_result, jobs_result, calibration_result = await asyncio.gather(
        _api_request("GET", "/api/zotero/status"),
        _api_request("GET", "/api/pending/count", params={"status": "pending"}),
        _api_request("GET", "/api/triage/jobs", params={"limit": 25}),
        _api_request("GET", "/api/calibration/metrics"),
    )

    snapshot: dict[str, Any] = {
        "generated_at": _now_iso(),
        "api_base_url": API_BASE_URL,
    }

    warnings: list[dict[str, Any]] = []

    status_data = _snapshot_data_or_warn(status_result, warnings)
    if status_data is not None:
        snapshot["zotero"] = status_data

    pending_data = _snapshot_data_or_warn(pending_result, warnings)
    if pending_data is not None:
        snapshot["pending_changes_count"] = _as_int((pending_data or {}).get("count"))

    jobs_data = _snapshot_data_or_warn(jobs_result, warnings)
    if jobs_data is not None:
        jobs = list((jobs_data or {}).get("items") or [])
        running_job = next(
            (job for job in jobs if str((job or {}).get("status") or "").lower() == "running"),
            None,
        )
        snapshot["active_job"] = running_job

    calibration_data = _snapshot_data_or_warn(calibration_result, warnings)
    if calibration_data is not None:
        snapshot["calibration"] = (calibration_data or {}).get("periods") or {}

    if warnings:
        snapshot["warnings"] = warnings

    return snapshot
