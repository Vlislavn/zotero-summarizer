from __future__ import annotations

import math
import os


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    return value


def _bounded(value: int | float, name: str, minimum: int | float, maximum: int | float):
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


API_BASE_URL = os.getenv("ZOTERO_SUMMARIZER_API_BASE", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = _bounded(_env_float("ZOTERO_MCP_TIMEOUT_SECONDS", 60.0), "ZOTERO_MCP_TIMEOUT_SECONDS", 0.1, 3600.0)
MAX_TRIAGE_ITEM_KEYS = _bounded(_env_int("ZOTERO_MCP_MAX_TRIAGE_ITEMS", 200), "ZOTERO_MCP_MAX_TRIAGE_ITEMS", 1, 500)
DEFAULT_TRIAGE_SECONDS_PER_ITEM = _bounded(_env_int("ZOTERO_MCP_SECONDS_PER_ITEM", 120), "ZOTERO_MCP_SECONDS_PER_ITEM", 1, 86400)
DEFAULT_PAGE_LIMIT = 25
MAX_PAGE_LIMIT = 100
MAX_PENDING_FETCH = 500

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
RETRYABLE_ERROR_CODES = frozenset(
    {
        "zotero_db_locked",
        "llm_timeout",
        "llm_rate_limit",
        "job_already_running",
        "zotero_unavailable",
    }
)
