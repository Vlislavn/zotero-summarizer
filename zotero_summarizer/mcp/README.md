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

**Boundaries:** HTTP only. Pending tools import stdlib, `httpx`, `pydantic`
(strict tool arguments and wire envelopes), and other `mcp/` modules; never app
`models`, `services`, `api`, or `storage`. The existing parser uses the shared
pure reading-priority domain helpers, not application state.

Path identifiers reject separators/dot segments/control characters and are
percent-encoded on the wire. Raw identity is retained in payloads/query parameters.
Mutations are never automatically retried: a lost response may follow a completed write.

Pending Apply requires explicit strict positive IDs and a `previews` map from
string IDs to caller-presented `approval_preview.preview_sha256` values. Both
arguments are required; `[]` plus `{}` is a no-op. More than 1000 IDs fails before
HTTP. There is no apply-all discovery or automatic chunking. Pending list and
paper-detail results pass through the complete backend `approval_preview`,
including null for display/rejection-only history. Inspect the full payload and
native target; hash equality is not authentication or evidence of human attention.

The existing GET preflight verifies every selected row is present, retains the
four-operation allowlist, validates each allowed version-1 envelope (ID, item,
type, payload, edit revision, native target and digests), and compares its digest
to the caller's supplied hash. Missing/malformed envelopes are
`backend_contract_error`; stale supplied hashes are `pending_write_conflict`.
Both refuse before any POST and are nonretryable. MCP never computes, replaces
or upgrades approval from GET; force forwards the same captured hashes. Backend,
UI and MCP require coordinated deployment; unsupported old backends are refused.
The backend remains responsible for atomic whole-selection validation at dispatch.

Apply receipts retain actual counts, failed IDs, backups and unconfirmed IDs on
lost/error replies. Uncertain delivery requires passive inspection, not blind
redelivery; known failed-row retries require fresh explicit review in the UI.
Implicit Inbox cleanup and its receipt fields are absent. Queue an explicit
`remove_from_collection` change when wanted; automatic Inbox cleanup is not part
of Apply. Human acceptance of that workflow loss remains separate.

`find_similar_papers` uses the library hybrid-search endpoint to rank unread
papers and reports when semantic search is unavailable, rather than treating
title substring matches as similarity. `get_paper` requests pending/history rows scoped by its item key.
Library status returns `ok=false` when every status subrequest fails, and the
active-job snapshot uses the API's status-filtered lookup instead of a newest-N
window. MCP numeric environment settings are validated at import: timeout is
0.1–3600 seconds, item batches 1–500 (matching the API), and per-item estimates
1–86400 seconds.
