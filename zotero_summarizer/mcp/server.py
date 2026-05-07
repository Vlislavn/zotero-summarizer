from __future__ import annotations

from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - handled at runtime if dependency is missing
    class FastMCP:  # type: ignore[no-redef]
        """Fallback stub so the module can be imported before MCP is installed."""

        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def tool(self, *_: Any, **__: Any):
            def decorator(fn):
                return fn

            return decorator

        def resource(self, *_: Any, **__: Any):
            def decorator(fn):
                return fn

            return decorator

        def run(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("Install the 'mcp' package to run the MCP server")


mcp = FastMCP("zotero-librarian")


def main() -> None:
    from zotero_summarizer.mcp.tools import register_tools

    register_tools()
    mcp.run(transport="stdio")
