"""Bounded CDP response capture and proxy authentication."""
from __future__ import annotations

import base64
from typing import Any, NamedTuple

from zotero_summarizer.integrations.pdf_fetch import _PDF_MAGIC

_STREAM_CHUNK_BYTES = 64_000


class ResponseCapture(NamedTuple):
    """One navigation's PDF bodies, callback errors, and status-confirmed PDF misses."""

    pdf_bodies: list[bytes]
    errors: list[Exception]
    missing_pdf_statuses: list[int]


def looks_pdf(body: bytes, *, max_bytes: int) -> bool:
    return bool(body) and len(body) <= max_bytes and body[: len(_PDF_MAGIC)] == _PDF_MAGIC


def _read_chunk(cdp: Any, handle: str, size: int) -> tuple[bytes, bool]:
    result = cdp.send("IO.read", {"handle": handle, "size": size})
    chunk = base64.b64decode(result["data"], validate=True) if result.get("base64Encoded") else result["data"].encode()
    return chunk, result["eof"]


def _read_limited_stream(cdp: Any, handle: str, max_bytes: int) -> bytes:
    body = bytearray()
    while True:
        size = min(_STREAM_CHUNK_BYTES, max_bytes - len(body) + 1)
        chunk, eof = _read_chunk(cdp, handle, size)
        if len(body) + len(chunk) > max_bytes:
            raise ValueError("Browser response exceeds max_bytes")
        body.extend(chunk)
        if eof:
            return bytes(body)
        if not chunk:
            raise ValueError("Browser stream made no progress")


def _read_document_stream(cdp: Any, handle: str, pdf_limit: int, text_limit: int) -> bytes:
    body = bytearray()
    is_pdf = None
    while True:
        if is_pdf is None:
            size = len(_PDF_MAGIC) - len(body)
        else:
            limit = pdf_limit if is_pdf else text_limit
            size = min(_STREAM_CHUNK_BYTES, limit - len(body) + 1)
        chunk, eof = _read_chunk(cdp, handle, size)
        if is_pdf is None:
            if len(chunk) > size:
                raise ValueError("Browser stream exceeded requested read size")
            body.extend(chunk)
            if not _PDF_MAGIC.startswith(body):
                is_pdf = False
            elif len(body) == len(_PDF_MAGIC):
                is_pdf = True
            elif eof:
                is_pdf = False
            if is_pdf is not None:
                limit = pdf_limit if is_pdf else text_limit
                if len(body) > limit:
                    raise ValueError("Browser response exceeds max_bytes")
        else:
            if len(body) + len(chunk) > limit:
                raise ValueError("Browser response exceeds max_bytes")
            body.extend(chunk)
        if eof:
            return bytes(body)
        if not chunk:
            raise ValueError("Browser stream made no progress")


def read_stream(cdp: Any, handle: str, max_bytes: int, *, document_limit: int | None = None) -> bytes:
    """Read bounded response bytes; optionally sniff documents before choosing a cap."""
    try:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        if document_limit is None:
            return _read_limited_stream(cdp, handle, max_bytes)
        if type(document_limit) is not int or document_limit < 0:
            raise ValueError("document_limit must be a non-negative integer")
        return _read_document_stream(cdp, handle, max_bytes, min(max_bytes, document_limit))
    finally:
        cdp.send("IO.close", {"handle": handle})


def authenticate_proxy(cdp: Any, event: dict, proxy: dict, attempted: set[str]) -> None:
    challenge = event["authChallenge"]
    response = {"response": "CancelAuth"}
    if (challenge["source"] == "Proxy" and challenge["origin"].rstrip("/") == proxy["server"]
            and event["requestId"] not in attempted):
        attempted.add(event["requestId"])
        response = {"response": "ProvideCredentials", "username": proxy["username"], "password": proxy["password"]}
    cdp.send("Fetch.continueWithAuth", {"requestId": event["requestId"], "authChallengeResponse": response})


def capture_response(
    cdp: Any, event: dict, capture: ResponseCapture, max_bytes: int, frame_id: str,
    document_limit: int | None = None,
) -> None:
    request = {"requestId": event["requestId"]}
    headers = event.get("responseHeaders", [])
    is_pdf = any(h["name"].lower() == "content-type" and "application/pdf" in h["value"].lower() for h in headers)
    is_document = event.get("frameId") == frame_id and event["resourceType"] == "Document"
    code = event.get("responseStatusCode", 0)
    if is_pdf and is_document and code >= 400 and code != 407:
        capture.missing_pdf_statuses.append(code)
        cdp.send("Fetch.failRequest", {**request, "errorReason": "Aborted"})
        return
    if code in {0, 401, 407} or 300 <= code < 400 or not (is_pdf or is_document):
        cdp.send("Fetch.continueRequest", request)
        return
    if capture.pdf_bodies:
        cdp.send("Fetch.failRequest", {**request, "errorReason": "Aborted"})
        return
    capture.pdf_bodies.append(b"")  # Reserve the capture while synchronous CDP pumps other events.
    try:
        stream = cdp.send("Fetch.takeResponseBodyAsStream", request)
        body = read_stream(
            cdp, stream["stream"], max_bytes,
            document_limit=document_limit if is_document else None,
        )
    except Exception:
        capture.pdf_bodies.clear()
        cdp.send("Fetch.failRequest", {**request, "errorReason": "Aborted"})
        raise
    if 200 <= code < 300 and looks_pdf(body, max_bytes=max_bytes):
        capture.pdf_bodies[0] = body
        cdp.send("Fetch.failRequest", {**request, "errorReason": "Aborted"})
        return
    capture.pdf_bodies.clear()
    headers = [h for h in headers if h["name"].lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
    cdp.send("Fetch.fulfillRequest", {
        **request, "responseCode": code, "responseHeaders": headers,
        "body": base64.b64encode(body).decode("ascii"),
    })


def install_response_capture(
    cdp: Any, network: dict, max_bytes: int, document_limit: int | None = None,
) -> ResponseCapture:
    capture = ResponseCapture([], [], [])
    cdp.on("error", lambda error: capture.errors.append(error))
    frame_id = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
    attempted: set[str] = set()
    cdp.on("Fetch.authRequired", lambda event: authenticate_proxy(cdp, event, network["proxy"], attempted))
    cdp.on("Fetch.requestPaused", lambda event: capture_response(
        cdp, event, capture, max_bytes, frame_id, document_limit,
    ))
    cdp.send("Fetch.enable", {"handleAuthRequests": True, "patterns": [
        {"urlPattern": "*", "requestStage": stage} for stage in ("Request", "Response")
    ]})
    return capture
