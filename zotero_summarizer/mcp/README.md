# mcp — Model Context Protocol server

Exposes the app's capabilities as MCP tools for AI agents. It is a **standalone
HTTP client of the running API** — it does not import `services/`, `api/`, or
`storage/`; it speaks to `/api/*` over the wire. This keeps the agent surface
decoupled from internals.

```
AI agent ⇄ mcp/server.py (stdio)
                 └─ tools/* ──HTTP──> api_client.py ──> http://127.0.0.1:8000/api/*
```

| file | responsibility |
|---|---|
| `server.py` | MCP server entrypoint over stdio (`zotero-summarizer mcp`); ships a no-op `FastMCP` fallback stub when the `mcp` package is absent |
| `api_client.py` | single-attempt httpx client; retryability metadata is advice to callers, not automatic retry/backoff |
| `config.py` | base URL, timeouts, retryable status/error codes |
| `helpers.py` · `parsers.py` | request shaping + response normalization |
| `tools/` | the actual MCP tools (see tools/README.md) |

**Boundaries:** import only stdlib, `httpx`, `pydantic` (strict tool arguments), `models`/`contracts` (for shapes),
and other `mcp/` modules. Never `services/`, `api/`, or `storage/` (enforced).

Path identifiers reject separators/dot segments/control characters and are
percent-encoded on the wire. Raw identity is retained in payloads/query parameters.
Mutations are never automatically retried: a lost response may follow a completed write.

`find_similar_papers` uses the library hybrid-search endpoint to rank unread
papers and reports when semantic search is unavailable, rather than treating
title substring matches as similarity. `get_paper` requests pending/history rows scoped by its item key.
Library status returns `ok=false` when every status subrequest fails, and the
active-job snapshot uses the API's status-filtered lookup instead of a newest-N
window. MCP numeric environment settings are validated at import: timeout is
0.1–3600 seconds, item batches 1–500 (matching the API), and per-item estimates
1–86400 seconds.
