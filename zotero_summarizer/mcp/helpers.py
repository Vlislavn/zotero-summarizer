from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from zotero_summarizer.mcp.config import RETRYABLE_ERROR_CODES, RETRYABLE_STATUS_CODES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


def _encode_cursor(offset: int) -> str:
    return str(max(0, int(offset)))


def _decode_search_cursor(cursor: str | None) -> tuple[int, int]:
    """Decode search cursor as `source_offset[:filtered_offset]`."""
    if not cursor:
        return 0, 0
    if ":" not in cursor:
        return _decode_cursor(cursor), 0

    source_raw, filtered_raw = cursor.split(":", 1)
    return _decode_cursor(source_raw), _decode_cursor(filtered_raw)


def _encode_search_cursor(source_offset: int, filtered_offset: int = 0) -> str:
    safe_source = _decode_cursor(str(source_offset))
    safe_filtered = _decode_cursor(str(filtered_offset))
    if safe_filtered <= 0:
        return _encode_cursor(safe_source)
    return f"{safe_source}:{safe_filtered}"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_unique_strings(values: list[str] | None) -> list[str]:
    if not values:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        folded = text.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(text)
    return normalized


def _normalize_authors(raw_authors: Any) -> list[str]:
    if isinstance(raw_authors, list):
        return [str(author).strip() for author in raw_authors if str(author).strip()]
    if isinstance(raw_authors, str):
        return [part.strip() for part in raw_authors.split(";") if part.strip()]
    return []


def _is_retryable(code: str, status_code: int | None) -> bool:
    if code in RETRYABLE_ERROR_CODES:
        return True
    return bool(status_code in RETRYABLE_STATUS_CODES)


def _extract_retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        retry_after = int(value)
    except ValueError:
        return None
    return max(0, retry_after)


def _error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    retry_after_sec: int | None = None,
    status_code: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": bool(retryable),
        },
    }
    if retry_after_sec is not None:
        payload["error"]["retry_after_sec"] = int(retry_after_sec)
    if status_code is not None:
        payload["error"]["status_code"] = int(status_code)
    if details:
        payload["error"]["details"] = details
    return payload


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _require_non_empty_text(value: str | None, field_name: str) -> tuple[str | None, dict[str, Any] | None]:
    safe_value = str(value or "").strip()
    if safe_value:
        return safe_value, None
    return None, _error("validation_error", f"{field_name} is required")


def _extract_data_or_error(result: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not result.get("ok"):
        return None, result

    data = result.get("data")
    if isinstance(data, dict):
        return data, None
    return {}, None


def _extract_error_payload(result: dict[str, Any]) -> dict[str, Any]:
    error = result.get("error")
    return error if isinstance(error, dict) else {}


def _clean_query_params(params: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    return cleaned
