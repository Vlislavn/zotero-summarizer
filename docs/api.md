# API Schemas

Canonical routes are package-only. Old root routes such as `/health`, `/summarize`, `/batch_summarize`, and `/dashboard` are intentionally not supported.

## Health

### `GET /api/health`

Response:

```json
{
  "status": "ok",
  "config_loaded": true,
  "draft_model": "GPT-OSS-120B",
  "refine_model": "GPT-OSS-120B",
  "api_base": "https://api.openai.com/v1"
}
```

## Summaries

### `POST /api/summaries`

Optional query parameter: `item_id=<zotero_item_id>`. If present, the result is persisted to SQLite.

Request:

```json
{
  "title": "Example Paper",
  "doi": "10.1000/example",
  "pdf_path": "/absolute/path/to/paper.pdf",
  "abstract": "Optional abstract text"
}
```

Response fields:

| Field | Type | Description |
|---|---|---|
| `executive_summary` | string | Main research note |
| `should_deep_read` | string | Reading recommendation |
| `key_sections_to_read` | string[] | Specific sections worth reading |
| `relevance_to_research` | string | Connection to configured goals |
| `controversial_points` | string | Debatable claims |
| `industry_academy_impact` | string | Practical implications |
| `unknown_unknowns` | string | Non-obvious implications |
| `implementation_quickstart` | string | Implementation guidance |
| `key_findings` | string[] | Main findings and metrics |
| `methods` | string | Methodology summary |
| `limitations` | string | Caveats |
| `relevance_score` | int | LLM relevance score, `1..5` |
| `composite_relevance_score` | float | Final score, `0..5` |
| `reading_priority` | string | `must_read`, `should_read`, `could_read`, `dont_read` |
| `tags` | string[] | Specific topic/method tags |
| `triage_rationale` | string | Score explanation |
| `triage_dimensions` | object/null | Goal alignment, novelty, rigor, actionability, evidence |
| `triage_confidence` | float | `0..1` confidence |
| `corpus_affinity_score` | float | `-1..1` net corpus affinity |
| `matched_goal` | string | Closest configured research goal |
| `suggested_collections` | string[] | Candidate Zotero collections |
| `top_similar_items` | string[] | Similar existing corpus items |

### `POST /api/summaries/batch`

Request:

```json
{
  "items": [
    {
      "item_id": "ABCD1234",
      "request": {
        "title": "Example Paper",
        "doi": "10.1000/example",
        "pdf_path": "/absolute/path/to/paper.pdf",
        "abstract": "Optional abstract"
      }
    }
  ]
}
```

Response:

```json
{
  "batch_id": "batch_20260507_120000_000000",
  "total_items": 1,
  "ranked_items": [
    {
      "batch_id": "batch_20260507_120000_000000",
      "item_id": "ABCD1234",
      "title": "Example Paper",
      "summary": {},
      "normalized_score": 82.5,
      "percentile": 100.0,
      "rank": 1,
      "forced_priority": "must_read"
    }
  ],
  "failed_items": []
}
```

## Zotero

### `GET /api/zotero/status`

Returns whether local Zotero read/write adapters are available.

### `GET /api/zotero/collections`

Returns Zotero collection tree rows.

### `GET /api/zotero/tags?limit=500`

Returns tags with usage counts.

### `GET /api/zotero/items`

Query parameters:

| Parameter | Description |
|---|---|
| `collection` | Collection key filter |
| `search` | Title/creator search |
| `tag` | Tag filter |
| `limit` | Page size |
| `offset` | Page offset |

### `GET /api/zotero/items/{item_key}`

Returns full local item detail, including PDF path when available.

### `POST /api/zotero/items/{item_key}/priority`

Request:

```json
{
  "priority": "must_read",
  "force": false
}
```

Direct Zotero updates require Zotero to be closed unless `force` is true.

### `POST /api/zotero/items/{item_key}/tags`

Request:

```json
{
  "add_tags": ["topic:agents"],
  "remove_tags": ["topic:old"],
  "force": false
}
```

### `POST /api/zotero/items/{item_key}/collections`

Request:

```json
{
  "add": [{"collection_path": "Research > Agents"}],
  "remove": [],
  "force": false
}
```

## Triage Jobs

### `POST /api/triage/run`

Request:

```json
{
  "item_keys": ["ABCD1234", "EFGH5678"],
  "queue_changes": true
}
```

Response:

```json
{
  "job_id": "job_20260507_120000_deadbeef",
  "status": "running",
  "total": 2
}
```

### `GET /api/triage/jobs`

Returns recent persisted jobs.

### `GET /api/triage/jobs/{job_id}`

Returns job progress, item results, and item errors.

### `POST /api/triage/jobs/{job_id}/cancel`

Requests cancellation. Running item work is allowed to finish before the job settles.

## Pending Changes

### `GET /api/pending?status=pending&limit=500`

Returns pending, applied, rejected, failed, or all changes.

### `PUT /api/pending/change/{change_id}`

Edits a pending payload before apply.

### `POST /api/pending/override-priority`

Queues a priority tag override instead of writing immediately.

### `POST /api/pending/reject`

Request:

```json
{
  "change_ids": [1, 2, 3],
  "force": false
}
```

### `POST /api/pending/apply`

Applies approved changes to Zotero and creates a Zotero SQLite backup.

## Corpus And Feedback

### `POST /api/corpus/import`

Request:

```json
{
  "items": [
    {
      "item_id": "ABCD1234",
      "title": "Example Paper",
      "abstract": "Optional abstract",
      "tags": ["topic:agents"],
      "collections": ["Research > Agents"],
      "annotation_count": 4,
      "manual_note_count": 1,
      "created_at": "2026-05-07T10:00:00Z"
    }
  ]
}
```

Response:

```json
{
  "imported_items": 1,
  "updated_items": 0
}
```

### `GET /api/corpus/items`

Lists corpus metadata.

### `GET /api/corpus/item/{item_key}`

Returns one corpus metadata row when present.

### `POST /api/feedback`

Stores explicit feedback events.

### `GET /api/feedback`

Lists recent feedback events.

### `POST /api/triage/results/{item_key}/feedback`

Request:

```json
{
  "verdict": "approve"
}
```

Queues corresponding Zotero approval/rejection tags.

### `POST /api/triage/results/{item_key}/override-dimensions`

Overrides one or more triage dimensions and recalculates score/priority.

### `GET /api/calibration/metrics`

Returns `last_7d`, `last_30d`, and `all_time` calibration metrics.

## Results

### `GET /results`

Serves the dashboard page.

### `GET /api/results`

Query parameters:

| Parameter | Description |
|---|---|
| `scope` | `latest`, `all`, `batch`, `compare` |
| `batch_id` | Single batch ID |
| `batch_ids` | Comma-separated IDs for compare |
| `sort` | Sort column |
| `order` | `asc` or `desc` |
| `limit` | Max rows |
| `offset` | Pagination offset |

### `GET /api/batches`

Returns recent batch runs.

### `GET /api/results/{item_id}`

Returns one stored triage result.

## Errors

Error response schema:

```json
{
  "error": "validation_error",
  "message": "Invalid request payload",
  "details": {}
}
```

Common errors:

| Status | Error | Cause |
|---|---|---|
| 403 | `path_not_allowed` | PDF path outside `PDF_ROOT` |
| 404 | `file_not_found` | PDF does not exist |
| 404 | `not_found` | Requested item/job/result does not exist |
| 422 | `validation_error` | Invalid request payload |
| 422 | `extraction_failed` | PDF extraction returned empty content |
| 503 | `zotero_unavailable` | Zotero adapter is not configured or readable |
| 504 | `llm_timeout` | LLM call exceeded timeout |
| 500 | `internal_error` | Unexpected server error |
