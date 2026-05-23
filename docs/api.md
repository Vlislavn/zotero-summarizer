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

## Today (Daily Slate) — Stage 1 cull

### `GET /api/daily?K=5&lookback_hours=168`

Assemble the role-mixed daily slate. Each paper carries provenance + scores +
any saved verdict:

```json
{
  "papers": [
    {
      "item_id": 13, "item_key": "http://arxiv.org/abs/2604.18349v1",
      "title": "...", "authors": "Smith J, Lee P", "venue": "...", "year": "2026",
      "role": "model", "feed_name": "bioRxiv — Bioinformatics",
      "composite_score": 2.93, "prestige_score": 0.46, "shap_top": [...],
      "role_value_verdict": null, "user_priority": null
    }
  ],
  "pool_size": 25, "capped_at": 25, "lookback_hours": 168,
  "empty_role_events": [], "fellback_to_recent": true
}
```

`role` is the allocation bucket (`model`/`surprise`/`audit`/`diversity`);
`feed_name` is the source RSS feed. `fellback_to_recent` is true when the
lookback window was empty and the slate fell back to recent rows.

### `POST /api/daily/add-to-library`

Body `{ "item_ids": [13, 15] }` (processed_feed_items PKs). Materializes each
into the Zotero **Inbox** collection and records a positive (`should_read`)
training label. Returns `{ "added": 2, "failed_count": 0, "failed": [] }`.
Empty `item_ids` → `422`.

### `POST /api/daily/trash`

Body `{ "item_ids": [14] }`. Records `dont_read` for each + marks the feed
items read. Returns `{ "trashed": 1, "marked_read": 1, "failed_count": 0, "failed": [] }`.

### `POST /api/daily/triage-backlog` · `GET /api/daily/triage-status`

Start a background drain of the un-triaged feed backlog (SOTA model via
`CUSTOM_*`), and poll its progress
(`{ "running": true, "triaged": 3, "gate_rejected": 21, "ticks": 1, ... }`).

### `POST /api/daily/verdict`

Record a `must/should/could/dont_read` label on a Today card. Writes
`label_verdicts` (keyed `feed:<feed_item_id>`) **and** appends the item to the
golden CSV so it trains. Body `{ "item_id": 13, "user_priority": "must_read",
"comment": "" }` where `item_id` is the `processed_feed_items` PK. Returns
`{ "id": <verdict_row_id>, "item_key": "feed:32448" }`.

### `POST /api/daily/role-verdict`

Record an after-reading `worth/waste/unknown` rating for a slot (rehydrated by
`GET /api/daily`). `item_key` travels in the **body** (feed keys are URL-shaped):
`{ item_key, role, verdict, composite_score?, surprise_score?, corpus_affinity? }`.
(Legacy path form `POST /api/daily/{item_key}/role-verdict` still exists.)

## Library — Stage 2 reading queue

### `GET /api/library/reading-queue?include_read=false&limit=30&refresh=false`

Unread library papers ranked by the gate's relevance score (background-computed
+ cached). `include_read=true` also lists already-read items; `refresh=true`
forces a rescore.

```json
{
  "status": "ready",
  "model_ready": true,
  "total_unread": 500,
  "read_hidden": 0,
  "items": [
    {
      "item_key": "3Q28D3J6", "title": "...", "authors": "...",
      "reading_priority": "", "has_pdf": true, "date_added": "2026-05-20",
      "read": false, "relevance_score": 2.88, "why_reason": "Topic match"
    }
  ]
}
```

`status` is `"computing"` while the background scoring job runs (poll until
`"ready"`). `model_ready=false` → the gate isn't loaded yet and items are
ordered by recency. `limit` must be 1–200 (else `422`). Each item's
`relevance_score` matches the `composite_score` shown by
`GET /api/golden/review-detail?item_key=<key>` for that paper.

## Corpus And Feedback

The corpus (SPECTER2 embeddings of your existing library) is populated
automatically from Zotero at startup — there is no HTTP import route.

### `GET /api/corpus/items`

Lists corpus metadata.

### `GET /api/corpus/item/{item_key}`

Returns one corpus metadata row when present.

### `POST /api/triage/results/{item_key}/feedback`

Request:

```json
{
  "verdict": "approve"
}
```

Queues corresponding Zotero approval/rejection tags.

### `GET /api/calibration/metrics`

Returns `last_7d`, `last_30d`, and `all_time` calibration metrics.

## Results

> The legacy `GET /results` dashboard page was removed in the 2026-05-15 UI
> redesign. The React SPA is served at `/` and any unknown non-`/api/` path
> falls through to it (client-side routing).

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

### `GET /api/results/{item_id}`

Returns one stored triage result.

## Annotate (golden labels)

### `GET /api/golden/provenance/list?priority=&flag=&limit=200`

The Annotate list. Each item carries `effective_priority` (the user's verdict
if any, else derived — **manual wins**), `derived_priority`, `user_priority`,
`is_user_override`, and `orphaned`. Filtering by `priority` uses
`effective_priority`. Orphaned verdicts (key not in the CSV) are appended.

### `GET /api/golden/review-detail?item_key=...`

Full detail for one paper. Dispatches by key prefix (`feed:` / `note:` /
8-char library). Returns a uniform shape with `source`, `authors`,
`scoring` (SHAP), `provenance` (null for rows not in the CSV), and `verdict`.
Never 404s a key that has a verdict — falls back to a CSV/stub payload.

### `POST /api/golden/verdict` · `DELETE /api/golden/verdict?item_key=...`

Save (UPSERT) or delete a manual verdict. Body:
`{ item_key, user_priority, comment }`. Saving works even for a key not in
the golden CSV (e.g. a Today feed item).

### `GET /api/golden/effective-labels` · `/api/golden/effective-labels/summary`

The merged hybrid ground-truth map (`source: "user" | "derived"`) and its
counts (`total_rows`, `user_verdicts`, `user_overrode_derivation`).

### `GET /api/golden/border-suggestions?top_k=50`

Active-learning suggestions, background-computed + sha-cached. Returns
`{ status: "computing" | "ready" | "error", items: [...], total }`. Poll
until `ready`. `top_k` must be 1–2000.

## Admin (Settings → Model lifecycle)

### `GET /api/admin/model`

The current trained gate's metadata: classifier, objective, `oof_spearman`,
`n_train`, `trained_at`, `git_commit`, golden-CSV sha, thresholds. Returns
`{ "model": null }` when none is trained yet.

### `POST /api/admin/refresh-labels`

Re-export `zotero-summarizer-golden.csv` from Zotero (synchronous; returns
counts). Manual verdicts in `label_verdicts` are unaffected and still win.

### `POST /api/admin/retrain`

Retrain the gate on the current hybrid ground truth as a background job.
Body: `{ classifier_name?: "logreg"|"lightgbm"|"tabpfn", n_folds?: 5 }`.
Returns `{ job_id, status }`; 409 if one is already running.

### `GET /api/admin/jobs` · `GET /api/admin/jobs/{job_id}`

List jobs / poll one retrain job (`status ∈ running|succeeded|failed`).

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
