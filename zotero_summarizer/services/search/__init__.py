"""Targeted Search execution policy."""
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.settings import offline_requested


def require_online() -> None:
    if offline_requested():
        raise APIError("strict_offline", "Targeted Search needs external literature sources", status_code=409)
