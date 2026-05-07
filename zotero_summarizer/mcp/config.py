from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


API_BASE_URL = os.getenv("ZOTERO_SUMMARIZER_API_BASE", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = _env_float("ZOTERO_MCP_TIMEOUT_SECONDS", 60.0)
MAX_TRIAGE_ITEM_KEYS = _env_int("ZOTERO_MCP_MAX_TRIAGE_ITEMS", 200)
DEFAULT_TRIAGE_SECONDS_PER_ITEM = _env_int("ZOTERO_MCP_SECONDS_PER_ITEM", 120)
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
