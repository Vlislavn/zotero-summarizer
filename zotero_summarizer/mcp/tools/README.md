# mcp/tools — the MCP tool implementations

Each module groups related agent-callable tools. Tools build a request, call
`api_client`, and return normalized results — no direct DB/service access.

```
tool fn ── helpers.normalize ──> api_client.request() ──> /api/*
```

| file | tools |
|---|---|
| `search.py` | search/list papers, results, corpus items, zotero status |
| `pending.py` | inspect + apply/reject pending Zotero changes |
| `mutations.py` | tag/priority/collection mutations (queued, not direct writes) |
| `triage.py` | start/inspect triage jobs + latest feedback |
| `status.py` | health / liveness probes |

**Boundaries:** same as `mcp/` — HTTP only, no `services/`/`api/`/`storage/`.

`get_job_status` forwards the API's `active_items` list (item key and title),
including parallel items still draining during cancellation. The old singular
`current_item_key`/`current_title` fields are removed; update client and API
together. Missing activity in the response is a contract error, not an invented
empty list. Historical/terminal jobs receive an explicit empty list from the API.
