from __future__ import annotations

import logging
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
        details={"pdf_path": str(exc)},
    )
    return JSONResponse(status_code=404, content=payload.model_dump())


async def extraction_error_handler(_, exc: ExtractionError) -> JSONResponse:
    payload = ErrorResponse(error="extraction_failed", message=str(exc))
    return JSONResponse(status_code=422, content=payload.model_dump())


async def timeout_error_handler(_, exc: LLMTimeoutError) -> JSONResponse:
    payload = ErrorResponse(error="llm_timeout", message=str(exc))
    return JSONResponse(status_code=504, content=payload.model_dump())


async def zotero_read_error_handler(_, exc: ZoteroReadError) -> JSONResponse:
    payload = ErrorResponse(error="zotero_unavailable", message=str(exc))
    return JSONResponse(status_code=503, content=payload.model_dump())


async def zotero_write_error_handler(_, exc: ZoteroWriteError) -> JSONResponse:
    payload = ErrorResponse(error="zotero_write_failed", message=str(exc))
    return JSONResponse(status_code=503, content=payload.model_dump())


async def validation_error_handler(_, exc: RequestValidationError) -> JSONResponse:
    LOGGER.warning("Request validation failed: %s", exc.errors())
    payload = ErrorResponse(error="validation_error", message="Invalid request payload", details={"errors": exc.errors()})
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
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
