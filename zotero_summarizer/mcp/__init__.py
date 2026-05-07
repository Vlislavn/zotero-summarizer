from __future__ import annotations

from zotero_summarizer.mcp.api_client import _base_item_update_payload
from zotero_summarizer.mcp.helpers import (
    _decode_cursor,
    _decode_search_cursor,
    _encode_search_cursor,
    _extract_data_or_error,
    _require_non_empty_text,
)
from zotero_summarizer.mcp.parsers import _parse_pending_change, _parse_response_json, _triage_from_result_row
from zotero_summarizer.mcp.server import main, mcp
from zotero_summarizer.mcp.tools import register_tools

# Import-time registration keeps tool decorators active for package-based startup.
register_tools()

__all__ = [
    "main",
    "mcp",
    "_base_item_update_payload",
    "_decode_cursor",
    "_decode_search_cursor",
    "_encode_search_cursor",
    "_extract_data_or_error",
    "_parse_pending_change",
    "_parse_response_json",
    "_require_non_empty_text",
    "_triage_from_result_row",
]
