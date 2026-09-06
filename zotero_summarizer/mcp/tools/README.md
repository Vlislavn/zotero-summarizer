# mcp/tools — the MCP tool implementations

Each module groups related agent-callable tools. Tools build a request, call
`api_client`, and return normalized results — no direct DB/service access.

```
tool fn ── helpers.normalize ──> api_client.request() ──> /api/*
```

| file | tools |
|---|---|
| `search.py` | search/list papers, results, corpus items, zotero status |
| `pending.py` | inspect + apply pending Zotero changes; rejection is available in the UI, not MCP |
| `mutations.py` | immediate tag/priority/collection writes through the API (backup/force checks apply) |
| `triage.py` | start/inspect triage jobs + latest feedback |
| `status.py` | health / liveness probes |

**Boundaries:** same as `mcp/` — HTTP only, no `services/`/`api/`/`storage/`.

Pending application permits only `tag_changes`, `add_note`, `add_to_collection`,
and `remove_from_collection`; unknown/new types are blocked until reviewed.
Omitted IDs select all pending rows, while `[]` is a no-op and invalid IDs fail.
The registered MCP schema uses strict integers, without bool/string/float coercion.
Every requested ID must be found within the API's 5000-row history ceiling;
otherwise nothing is written. Restricted IDs are reported separately.
HTTP 200 with failed writes returns `ok=false` and preserves counts, failed IDs,
and backup paths in `error.details`. No automatic whole-batch retry is performed.
Later-chunk transport errors retain prior outcomes and identify the unconfirmed
IDs; Inbox-removal failures are also errors, not silently dropped side effects.

Search scans all matching source pages, enriches, filters and globally sorts
before applying `g:<offset>` pagination; `filtered_count` covers the complete set.
Old window cursors require a restart. Inputs/library/triage state must stay stable
between pages (this is not a snapshot). Missing metadata, stalled/duplicate pages,
or changing totals fail explicitly. More than 10,000 matches requires narrowing
the query, collection or tag. Enrichment uses at most eight concurrent requests;
`relevance` retains the backend's source order, not a semantic similarity score.

`get_job_status` forwards the API's `active_items` list (item key and title),
including parallel items still draining during cancellation. The old singular
`current_item_key`/`current_title` fields are removed; update client and API
together. Missing activity in the response is a contract error, not an invented
empty list. Historical/terminal jobs receive an explicit empty list from the API.
