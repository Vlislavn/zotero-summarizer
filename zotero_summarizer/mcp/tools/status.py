from __future__ import annotations

import json
from typing import Any

from zotero_summarizer.mcp.api_client import _collect_status_snapshot, _resource_json_from_api
from zotero_summarizer.mcp.helpers import _now_iso, _ok
from zotero_summarizer.mcp.server import mcp


@mcp.tool()
async def get_library_status() -> dict[str, Any]:
    """Get high-level library status, active job, and calibration snapshot."""
    snapshot = await _collect_status_snapshot()
    return _ok(**snapshot)


@mcp.resource("library://status")
async def resource_library_status() -> str:
    """Resource containing runtime status for the library service."""
    snapshot = await _collect_status_snapshot()
    return json.dumps(snapshot, ensure_ascii=False, indent=2)


@mcp.resource("library://collections")
async def resource_library_collections() -> str:
    """Resource containing the Zotero collection tree."""
    return await _resource_json_from_api(
        "/api/zotero/collections",
        lambda data: {
            "generated_at": _now_iso(),
            "collections": list((data or {}).get("items") or []),
        },
    )


@mcp.resource("library://goals")
async def resource_library_goals() -> str:
    """Resource containing research goals and triage configuration."""
    return await _resource_json_from_api(
        "/api/config",
        lambda config: {
            "generated_at": _now_iso(),
            "research_goals": list((config or {}).get("research_goals") or []),
            "triage_criteria": list((config or {}).get("triage_criteria") or []),
            "relevance_scale": (config or {}).get("relevance_scale") or {},
            "reading_priority_scale": (config or {}).get("reading_priority_scale") or {},
            "output_language": str((config or {}).get("output_language") or ""),
        },
    )
