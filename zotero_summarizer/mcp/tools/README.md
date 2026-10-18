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

**Boundaries:** same as `mcp/` — HTTP only, no app `models`/`services`/`api`/`storage` imports.

Pending application permits only `tag_changes`, `add_note`, `add_to_collection`,
and `remove_from_collection`; unknown/new types are blocked until reviewed.
`apply_pending_changes(change_ids, previews, force=False)` requires both explicit
arguments. IDs are strict positive integers (no bool/string/float coercion), at
most 1000 before deduplication or HTTP; duplicates retain first occurrence.
`previews` must have exactly the normalized selected string IDs and 64-lowercase-
hex caller-presented common hashes. `[]` plus `{}` alone is a no-op. No omitted-ID
discovery or chunking occurs, and force never supplies or changes approval.

Every requested ID must be found within the existing API's 5000-row history
ceiling; otherwise nothing is written. GET checks the allowlist and complete
version-1 backend approval envelopes for all allowed rows before POST. Each
must bind the listed ID/item/type/payload, nonnegative edit revision, complete
native target and digest fields. Missing/null/malformed envelopes refuse with
nonretryable `backend_contract_error` (including unsupported old backends);
a well-shaped digest unequal to the supplied hash refuses with nonretryable
`pending_write_conflict`. GET is verification only: no locally minted/recomputed
hash, hidden refresh of approval, or old generated-inner-hash alias. The backend
rechecks the snapshot under its own confirmation boundary; preflight is not CAS.
List and `get_paper` pass the backend `approval_preview` through unchanged.

One POST forwards only the allowed IDs and their original caller hashes.
Restricted IDs are reported separately. HTTP 200 with failed writes returns
`ok=false` and preserves actual counts, failed IDs and backup paths in
`error.details`. Lost/error replies identify unconfirmed IDs, remain nonretryable
and never trigger another POST. Inspect applying/uncertain state without blind
redelivery; failed retries require freshly presented review in the UI. There is
no implicit Inbox-removal effect or Inbox-only receipt/aggregation. Explicitly
queued `remove_from_collection` remains allowed; automatic Inbox cleanup is
intentionally lost and still needs human workflow acceptance. Deploy the matching
backend/UI/MCP contract together, without compatibility placeholders.

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

`find_similar_papers` ranks unread library papers with semantic hybrid search,
with explicit unavailable feedback when the reranker cannot serve the query. `get_paper` gets
pending/history rows using the item-key filter, so unrelated queue traffic does
not consume its result window.
