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

## Feed Review (Phase 1.14)

Endpoints for the review-mode workflow (`feeds run` default — and
`feeds run --gate-only`). Two row states are surfaced for human action:

- `awaiting_review` — items the classifier gate kept (the main queue).
- `gate_rejected` — items the gate dropped pre-LLM. Surface for spot-checking
  false negatives and bulk-confirming positives via the UI.

### `GET /api/feeds/review?state=awaiting_review&since_hours=720&limit=1000`

Lists rows in the requested state. `state` defaults to `awaiting_review`;
pass `state=gate_rejected` to see the gate-rejected pile. Each item embeds
the parsed payload from `shap_contribs_json`.

Response (top-level field `state` echoes the query):

```json
{
  "state": "awaiting_review",
  "count": 1,
  "items": [
    {
      "id": 294,
      "feed_library_id": 99,
      "feed_item_id": 8001,
      "title": "Multi-agent self-improving research assistants",
      "doi": null,
      "arxiv_id": "2605.02366v1",
      "guid": "http://arxiv.org/abs/2605.02366v1",
      "reading_priority": "should_read",
      "composite_score": 3.4,
      "decision": "awaiting_review",
      "shap": [
        {"feature": "corpus_affinity",         "contribution":  0.42},
        {"feature": "semantic_match_specter2", "contribution":  0.30},
        {"feature": "prestige_score",          "contribution":  0.08},
        {"feature": "bias",                    "contribution": -0.55}
      ],
      "aux_context": {
        "max_author_h_index": 88,
        "venue_works_count": 12345,
        "cited_by_count": 3
      },
      "summary": {
        "executive_summary": "...",
        "reading_priority": "should_read",
        "triage_rationale": "...",
        "tags": ["..."],
        "suggested_collections": []
      }
    }
  ]
}
```

`shap` is non-null only when the classifier gate ran with a LightGBM model
(TreeSHAP via `predict_proba(X, pred_contrib=True)`). The 768 SPECTER2
embedding dimensions are aggregated into a single `semantic_match_specter2`
bucket; the 7 named tabular extras and the bias term are surfaced
individually. The list is sorted by `|contribution|` descending and capped
at 8 entries.

### `POST /api/feeds/review/{processed_id}/approve`

Flips `decision` to `user_approved`. Does **not** write to Zotero yet and
does **not** queue `pending_changes` — feed items don't exist in the user's
library until materialised, so the library-centric pending_changes pipeline
("apply a tag/note to existing item X") would fail with "Item not found".
Use `POST /api/feeds/review/apply-all` later in the session to materialise
the batch via `apply_feed_materialization` (the daemon-direct create path).

Only accepts rows in state `awaiting_review`. Synthetic gate-rejected items
that need to be "promoted to Zotero" should use the `relabel` endpoint
instead (it synthesises a `SummarizeResponse` on the fly).

Response:

```json
{ "processed_id": 294, "state": "user_approved" }
```

Errors:
- `404 not_found` — `processed_id` doesn't exist
- `422 validation_error` — row is not in `awaiting_review`, or the persisted
  payload is missing the LLM summary (predates Phase 1.14)

### `POST /api/feeds/review/{processed_id}/reject`

Flips `decision` to `user_rejected`. Optionally appends a `dont_read` row
to `zotero-summarizer-golden.csv` so the next classifier retrain learns
from the rejection.

Request (all fields optional):

```json
{ "write_to_golden": true }
```

Response:

```json
{ "processed_id": 294, "golden_csv_row_added": true }
```

`golden_csv_row_added: false` means the same `item_key` (formatted
`feed:<feed_item_id>`) was already in the CSV — idempotent on duplicate.

### `POST /api/feeds/review/{processed_id}/relabel`

Override the predicted priority. Accepts rows in either `awaiting_review`
or `gate_rejected`. The chosen label is persisted both in the row's
`shap_contribs_json` payload (so `apply-all` can use the right priority for
the Zotero note) and as a new row in `zotero-summarizer-golden.csv` (for
the next retrain). For gate-rejected items with no stored LLM summary, a
minimal `SummarizeResponse` is synthesised on the fly so materialisation
still has a valid payload.

- `new_priority = must_read | should_read | could_read` → row → `user_approved`,
  golden CSV appended, materialisation deferred to `apply-all`.
- `new_priority = dont_read` → row → `user_rejected`, golden CSV appended
  with `dont_read`. For a gate_rejected source row this means "user
  confirmed the gate was right".

Request:

```json
{ "new_priority": "must_read" }
```

Response:

```json
{
  "processed_id": 294,
  "state": "user_approved",
  "golden_csv_row_added": true
}
```

Errors:
- `422 validation_error` — `new_priority` not in `{must_read, should_read, could_read, dont_read}`, or row not in an actionable state

### `POST /api/feeds/review/apply-all`

Materialise every `user_approved` row in the last 30 days to Zotero. For
each row, calls `ZoteroWriter.apply_feed_materialization` (daemon-direct
create — NOT `pending_changes`, which would fail on items the user's
library doesn't yet contain). On success the row transitions to:

- `decision = selected`
- `decision_reason = materialized_via_review_ui`
- `materialized_zotero_key = <new key>`
- `outcome_eligible_at = now + 7 days` (Phase 1.5 feedback loop)

Per-row failures are caught and reported so one bad row (e.g. Zotero locked)
doesn't block the rest of the batch.

Request: empty body (`{}`).

Response:

```json
{
  "applied": 38,
  "failed_count": 0,
  "failed": []
}
```

When `failed_count > 0` the `failed` array carries the first 20 errors:
`[{"id": <int>, "title": <str>, "error": <str>}, ...]`.

### `POST /api/feeds/review/confirm-gate-rejected`

Bulk-append a `dont_read` row to the golden CSV for every `gate_rejected`
item in the last 30 days that doesn't already have an `item_key` in the
CSV. Semantics: *"no click = I confirm the model was right"*. The row's
`decision` stays `gate_rejected` (the user didn't act — just confirmed it).

Idempotent: subsequent calls only append items that have been added since
the previous call. Triggers a model retrain via sha mismatch on the next
`feeds run` start.

Request: empty body (`{}`).

Response:

```json
{
  "appended": 415,
  "skipped_duplicate": 12,
  "skipped_no_feed_id": 0,
  "total_considered": 427
}
```

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
