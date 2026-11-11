"""Plain-words text for a failed triage job item or drain run (never a bare exception class name)."""
from __future__ import annotations

from zotero_summarizer.api.errors import LLMTimeoutError, is_database_busy


def plain_error(exc: BaseException, fallback: str | None = None) -> str:
    """Known transient failures in plain words; anything else keeps ``fallback`` or its own message."""
    if isinstance(exc, (TimeoutError, LLMTimeoutError)):
        return "The model did not answer in time; try again."
    if is_database_busy(exc):
        return "The database was busy; run again."
    return fallback if fallback is not None else str(exc) or type(exc).__name__
