from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from zotero_summarizer.integrations.zotero_read import ZoteroReadError
from zotero_summarizer.integrations.zotero_write import ZoteroWriteError
from zotero_summarizer.models import ErrorResponse


LOGGER = logging.getLogger("zotero_summarizer")


class APIError(Exception):
    def __init__(self, error: str, message: str, status_code: int, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.error = error
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ExtractionError(Exception):
    pass


class LLMTimeoutError(Exception):
    pass


async def api_error_handler(_, exc: APIError) -> JSONResponse:
    payload = ErrorResponse(error=exc.error, message=exc.message, details=exc.details)
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


async def file_not_found_handler(_, exc: FileNotFoundError) -> JSONResponse:
    payload = ErrorResponse(
        error="file_not_found",
        message="PDF file not found",
    )
    return JSONResponse(status_code=404, content=payload.model_dump())


async def extraction_error_handler(_, exc: ExtractionError) -> JSONResponse:
    payload = ErrorResponse(error="extraction_failed", message="PDF extraction failed")
    return JSONResponse(status_code=422, content=payload.model_dump())


async def timeout_error_handler(_, exc: LLMTimeoutError) -> JSONResponse:
    payload = ErrorResponse(error="llm_timeout", message="Model request timed out")
    return JSONResponse(status_code=504, content=payload.model_dump())


async def zotero_read_error_handler(_, exc: ZoteroReadError) -> JSONResponse:
    payload = ErrorResponse(error="zotero_unavailable", message="Zotero is unavailable")
    return JSONResponse(status_code=503, content=payload.model_dump())


async def zotero_write_error_handler(_, exc: ZoteroWriteError) -> JSONResponse:
    payload = ErrorResponse(error="zotero_write_failed", message="Zotero write failed")
    return JSONResponse(status_code=503, content=payload.model_dump())


def is_database_busy(exc: BaseException) -> bool:
    """True for sqlite's transient 'database is locked/busy' (a writer outlasted busy_timeout)."""
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("database is locked" in text or "database is busy" in text)


async def database_busy_handler(_, exc: sqlite3.OperationalError) -> JSONResponse:
    """A lock timeout is transient, not a bug: 503 + Retry-After instead of an unhandled 500."""
    if not is_database_busy(exc):
        raise exc  # any other OperationalError stays an unexpected 500 (generic handler)
    LOGGER.warning("Database busy: %s", exc)
    payload = ErrorResponse(error="database_busy", message="The app was busy; try again in a moment.")
    return JSONResponse(status_code=503, content=payload.model_dump(), headers={"Retry-After": "2"})


async def validation_error_handler(_, exc: RequestValidationError) -> JSONResponse:
    errors = [{"loc": error["loc"], "type": error["type"]} for error in exc.errors()]
    LOGGER.warning("Request validation failed: %s", errors)
    payload = ErrorResponse(error="validation_error", message="Invalid request payload", details={"errors": errors})
    return JSONResponse(status_code=422, content=payload.model_dump())


async def generic_error_handler(_, exc: Exception) -> JSONResponse:
    LOGGER.exception("Unhandled exception in API", exc_info=exc)
    payload = ErrorResponse(error="internal_error", message="Unexpected server error")
    return JSONResponse(status_code=500, content=payload.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(FileNotFoundError, file_not_found_handler)
    app.add_exception_handler(ExtractionError, extraction_error_handler)
    app.add_exception_handler(LLMTimeoutError, timeout_error_handler)
    app.add_exception_handler(ZoteroReadError, zotero_read_error_handler)
    app.add_exception_handler(ZoteroWriteError, zotero_write_error_handler)
    app.add_exception_handler(sqlite3.OperationalError, database_busy_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
