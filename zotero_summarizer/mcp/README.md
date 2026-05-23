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
| `server.py` | MCP server entrypoint over stdio (`zotero-summarizer mcp`) |
| `api_client.py` | thin httpx client with retry/backoff against the local API |
| `config.py` | base URL, timeouts, retryable status/error codes |
| `helpers.py` · `parsers.py` | request shaping + response normalization |
| `tools/` | the actual MCP tools (see tools/README.md) |

**Boundaries:** import only stdlib, `httpx`, `models`/`contracts` (for shapes),
and other `mcp/` modules. Never `services/`, `api/`, or `storage/` (enforced).
