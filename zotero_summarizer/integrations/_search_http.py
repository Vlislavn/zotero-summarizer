"""Owned search requests with optional, request-local transport observations."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar
import xml.etree.ElementTree as ET

import httpx

from zotero_summarizer.integrations._rate_limiter import RateLimiter

T = TypeVar("T")


def search_hits(
    query: str, url: str, params: dict, parse: Callable[[httpx.Response, bool], list[T | None]],
    limiter: RateLimiter, *, timeout: float, http_client: httpx.Client | None = None,
    outcome: dict | None = None,
) -> list[T]:
    if outcome is not None:
        outcome.update(status="unknown", count=None, error_code=None)
    if not query:
        return []
    limiter.acquire()
    client = http_client or httpx.Client(timeout=timeout)
    try:
        try:
            response = client.get(url, params=params, headers={"User-Agent": "zotero-summarizer/1.0"})
        except (httpx.HTTPError, OSError):
            if outcome is not None:
                outcome.update(status="failed", error_code="transport_error")
            return []
        if response.status_code != 200:
            if outcome is not None:
                outcome.update(status="failed", error_code="rate_limited" if response.status_code == 429 else "http_error")
            return []
        try:
            hits = parse(response, outcome is not None)
            if outcome is not None and any(hit is None for hit in hits):
                raise ValueError("Search response contained an unusable result")
            hits = [hit for hit in hits if hit is not None]
        except (ET.ParseError, ValueError):
            if outcome is not None:
                outcome.update(status="failed", error_code="parse_error")
            return []
        except (AttributeError, TypeError, OverflowError):
            if outcome is None:
                raise
            outcome.update(status="failed", error_code="parse_error")
            return []
        if outcome is not None:
            outcome.update(status="success_nonempty" if hits else "success_empty", count=len(hits))
        return hits
    finally:
        if http_client is None:
            client.close()


def json_rows(response: httpx.Response, *path: str, strict: bool) -> list[dict]:
    value = response.json()
    for index, key in enumerate(path):
        if strict and (not isinstance(value, dict) or key not in value):
            raise ValueError("Search response omitted its result list")
        value = (value if index == 0 else value or {}).get(key)
    if strict and (not isinstance(value, list) or any(not isinstance(row, dict) for row in value)):
        raise ValueError("Search response result rows must be objects")
    return [row for row in value or [] if isinstance(row, dict)]
