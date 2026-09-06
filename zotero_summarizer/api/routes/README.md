# api/routes — HTTP endpoints (thin)

One module per resource. Each registers its paths on a router; `__init__.py`
collects them via `include_routes(app)`. Handlers parse/validate and delegate
to `services/`; they raise `APIError` for failures.

```
__init__.include_routes(app)
   └─ for module in (health, corpus, results, zotero, triage, pending,
                     review, relabel_audit, daily, golden, admin, config, library, llm, setup, sync):
          app.include_router(module.router)
```

| file | endpoints (prefix) |
|---|---|
| `daily.py` | `/api/daily*` — the Today slate (cards ordered by the shared relevance×goal×prestige blend; payload: `papers` incl. per-card `goal_sim`, `pool_size`, `low_relevance_hidden`/`weak_slate` weak-week banner signals — the model role hides dont_read-band picks, no more `capped_at` — the pool is no longer truncated before role allocation), add-to-library (optional `target_collection_key` from the collection picker; None → Inbox)/trash, backlog drain (uses the configured `backlog` stage provider; the default gate-only drain now FAIL-FASTs via `readiness.require("classifier_gate")` → `503` with the real reason instead of a doomed background spin), `rescore-slate` (re-score the current slate in place with the live gate after a model upgrade). The `verdict` (must/should/could/don't) and keep/trash actions retain their `human_feedback` event; deliberate label writes additionally flow through `golden.label_verdicts` so an actual prior→new label transition is preserved |
| `llm.py` | `POST /api/admin/llm-check` — manual operational probe of each pipeline stage's provider (returns per-stage operational\|fail); `GET /api/admin/llm-reachability` — cheap proactive reachability of each stage (`GET /models`, no tokens; returns per-stage `reachable` + `base_url`), polled by the deep-review surface to warn before a run; `POST /api/admin/llm-models` — list a provider's available model ids for the Settings model-picker. Remote discovery accepts only a built-in preset or an already-saved provider identity; loopback listing never receives a stored secret |
| `golden.py` | `/api/golden*` — labels, verdicts, review-detail, review-note, effective labels. `POST /verdict` resolves one real Zotero mirror target before side effects: a positive Today-feed verdict auto-materializes into Inbox and returns its new key; any already-materialized feed verdict (including `dont_read`) reuses the existing key; an unmaterialized negative stays local and never creates a rejected item. Label and comment mirror to that real key, never the synthetic `feed:` key. Newly-created items revalidate current label intent after materialization; an already-correct tag requires no physical write. `POST /review-note` `{item_key, note}` saves the user's free-text "My notes" jot to the `review_notes` table (always durable) then best-effort mirrors it to the Zotero item under `USER_NOTE_MARKER` (`note_written`/`note_error` in the reply; unmaterialized feed/note keys skip the mirror, refuses while Zotero is open) — decoupled from the verdict; `review-detail` returns the saved note as `user_note`. The local `label_verdicts` row is always the durable write, and `zotero_unavailable` mirror failures are swallowed (other write failures are reported in `label_error`/`note_error`). `GET /verdicts` takes an optional `source` provenance filter (e.g. `source=auto_quality` to list the auto quality-gate's hides for one-tap restore, without paging every `dont_read`). Submitting/deleting a verdict retains the backward-compatible `human_feedback` event and also records an idempotent `label_transition` assignment/change/retraction through the golden command seam |
| `library.py` | `/api/library*` — whole-library reading queue (+ score `distribution`; `limit`≤10000; `semantic=true` + `search` → HYBRID search ranking: BM25 + dense embeddings + local cross-encoder rerank, response adds `semantic`/`reranked`/`reranker_loading`/`semantic_unavailable`), `reading-queue/status` (cheap in-memory job state, no Zotero read — polled while a Rescore computes), `review-fleet/run` + `review-fleet/status` — kick off / poll the background fleet that PRE-DECIDES a `proposed_verdict` for the chosen Read-next picks (explicit `item_keys` when the client pins its cool must/should-read set, else the top-`top_k` undecided picks — so the fleet reviews the SAME rows the UI counts, not a band-agnostic slice; reuses cached deep reviews; suggestions the user Confirms/Overrides, never auto-applied; status distinguishes `no_fetchable_source`, attempted `needs_library_login`, and setup-only `browser_extra_unavailable`; only login failures populate `needs_login_items` links), `university-access/status` + `university-login` — readiness + the one-time HEADED library login for the fleet's browser PDF fetch (non-arXiv/paywalled, via the optional `browser` extra), `fetch-fulltext` (+ `/status`) — acquire arXiv/Unpaywall/PMC/OpenAlex/direct OA PDFs and attach them natively to Zotero (background job; non-interactive, typed per-item outcomes, backup-first + connector-guarded), deep-review (the **per-paper** `item_key` run passes `acquire_missing=True` — a pick with no Zotero PDF gets one fetched first via `_pdf_acquire` (OA/PMC/library session) and reviewed from it, instead of being flagged `needs_pdf`; the top-`top_k` run doesn't, since the fleet pre-acquires), PDF stream, `render/{item_key}` status (flags `stale` when the renderer revision changed) + `render/{item_key}/build` (can acquire a missing PDF via the OA/browser chain when the item has no Zotero attachment) + `/presentation` (served **inline** so the reader pane embeds it in an iframe) + `render/{item_key}/pdf` (serves the source PDF the brief was built from — acquired-cache or Zotero-attached — `inline`) + `/figures/{name}` — paper-brief artifacts written next to the PDF (single-file HTML brief + figures + audit; no automatic Markdown; arXiv source download only with explicit consent), `ask` (POST) — grounded per-paper Q&A in three `Literal`-validated modes (`comprehensive` uses structured review + PDF body; `full_text`; `retrieval`) with whole-document count answers and quote-grounded abstention (`answer=null` = honest abstain), `sync-rel-tags` (write `zs:rel/<band>` relevance tags → filter in Zotero), `sync-score-ranks` (stamp a whole-library goal-blended rank into every paper's Zotero Call Number → sort the entire library in Zotero; preserves users' own Call Numbers); both whole-library + backup-first |
| `search.py` | `/api/search*` — Targeted Search (query-driven pull). `POST /screen` `{query, questions}` runs intent→plan→federate→rank, saves a `ResearchSession`, then **auto-starts** the review and returns the session reloaded (status already `reviewing`; the client just polls GET). The background worker runs the agentic PRF refinement rounds first when enabled (`services.search.refine`), then the light+deep review (a failure is recorded on the session, not swallowed); `POST /{id}/review` is kept as an explicit re-trigger — both paths single-flight through an atomic `screened`→`reviewing` claim so they can't stack workers; `GET /{id}` polls it (status `screened`→`reviewing`→`reviewed`/`error`); `GET /` lists sessions; `DELETE /{id}`; `POST /{id}/materialize` `{candidate_id, collection_key}` files one candidate into the chosen Zotero collection (None → Inbox) — the domain's ONLY Zotero write, always an explicit user action. Thin over `services.search.pipeline`; sessions persist one-JSON-each under `data/search/` |
| `review.py` | `/api/feeds/review*` — Phase 1.14 feed-review workflow; bulk-confirm requires the exact displayed `processed_ids` (max 500) and cannot label an unseen backend superset |
| `pending.py` | `/api/pending*` — review + apply queued Zotero changes. `POST /api/pending/apply` takes `retry` (default False): when True it re-applies FAILED rows (instead of PENDING) via the same writer path, and success transitions those rows to APPLIED. HTTP success can still contain per-row failures; the UI surfaces that as an error with the Failed-tab recovery path |
| `zotero.py` | `/api/zotero*` — read library items/collections/tags, set tags; `items/{key}/priority` writes the human `label:<priority>` ground-truth tag; `items/{key}/figures` attaches the paper's extracted figure PNGs as child image attachments (dedup by filename, connector force-gated) |
| `triage.py` | `/api/triage*` — run/list/cancel triage jobs |
| `admin.py` | `/api/admin*` — refresh-labels, retrain, model card. `retrain` now **hot-swaps** the freshly-trained gate into the live runtime + re-scores the Today slate (via `feeds.install_gate`), so it takes effect without a server restart; the job result carries `hot_swapped` + `rescored`. `retrain` now claims the single-flight `_RETRAIN_LOCK` **synchronously** (non-blocking acquire) before spawning the worker — the old `locked()` precheck raced (the worker acquires later, on its own thread), so a fast double-click double-trained + double-hot-swapped; the worker releases it on every exit path. The model-card handler (`model_card`) lives in `services/model/model_card.py` and is **re-exported** here (layering: no api→api import); route registration is unchanged |
| `setup.py` | `/api/setup*` — readiness, Zotero/path setup, validation, calibration, AI presets/credentials, and `GET/POST doctor`. Presets include the hardware-evaluated local catalog; `llm.enabled=false` is a valid ML-only state, so Doctor skips only AI checks by choice. Credential responses remain redacted. |
| `sync.py` | `/api/sync/*` protocol v1 — `pull?since=` compact working-set snapshots + monotonic changes, ordered typed `push` mutations with UUID replay safety/per-field conflicts, and status. Applied verdicts/notes run the same idempotent training/materialization/Zotero effects as online saves, including recovery on UUID replay. The client never sees table names or database files; the endpoint is for the default loopback PWA, not an authenticated remote-mobile deployment |
| `relabel_audit.py` | `/api/relabel-audit*` — test-retest reliability study |
| `results.py` · `corpus.py` · `config.py` · `health.py` | dashboard/corpus/config/health |
| `_golden_helpers.py` | pure (non-HTTP) helpers for `golden.py` |
| `_golden_border.py` | the `/api/golden/border-suggestions` active-learning endpoint, split out of `golden.py` to keep it under the 500-LOC cap; mounted on `golden.router` via `include_router` |

Ask Paper additionally accepts up to 20 typed prior turns. The library service
keeps a recent tail, compacts older evidence to extraction-versioned handles,
and returns separate claimed/quote-verified/location-verified citation state.

**Boundaries:** import `services/` + `models`; never the reverse.

`GET /api/calibration/metrics` is registered by `results.py` and delegates to
the feedback reporting service, not corpus metadata. Its path/response fields
are unchanged: periods describe latest explicit feedback and saved at-decision
triage priorities. They are not random-sample or classifier-gate audit metrics.

Setup calibration validates its 1–10 paper budget before starting a job.

Admin retrain covers setup, training, installation and rescoring with one failure
boundary: the job records `failed`, `finished_at` and the exception, then re-raises
to the thread hook. Thread-start failures also finish the registered job and
release the single-flight lock. No hot-swap error-as-success adapter remains.
Failure does not roll back a model already saved or installed before rescoring;
the UI's existing failed-job view displays the error and stops polling.

RSS add/update rejects malformed URLs with 422; a duplicate URL update returns
409 without changing either subscription. Refresh network/document failures
return 502 after the reader records last_error, never a successful empty refresh.

Feed-review `sort=recent|border` is passed through to storage, where ordering
precedes the limit. The route no longer re-sorts a truncated score-ranked page.
Apply-all delegates an uncapped approval snapshot with explicit user labels.
Missing Zotero returns pending-sync status; unexpected writer/row errors raise
through the HTTP error boundary rather than returning a successful batch reply.
Bulk gate confirmation accepts 1–500 positive integer row IDs and returns only
`confirmed/skipped`; duplicates are processed once, missing IDs return 404 before
any write, and concurrent state conflicts return 422. CSV-append counters are
removed: confirmation is a durable user verdict even for existing training rows.
Individual approve/reject/relabel also return only `processed_id/state`; the
obsolete `golden_csv_row_added` field is removed. Reject's existing
`write_to_golden` switch now controls durable training-sample enrollment, not a
physical CSV append. The decision, label and sample share one SQLite commit.

Today Add/Trash require a nonempty list of strict positive SQLite integer PKs
(no bool/float/string coercion). Their shared service preflight deduplicates in
first-seen order and returns 404 for a missing ID before any batch side effect;
mixed valid/missing selections are rejected together. Successful counts refer
to unique rows; no new endpoint or response field.

Golden DELETE keeps `{deleted: bool}` and also delivers the committed label
retraction through the shared golden command. Retrying when the local row is
already gone can finish an undelivered Zotero removal; newer local labels are
preserved. Missing configuration remains local-first, while connector/backup/
writer errors propagate. Reconciliation can retry the durable deletion later.

`GET /api/golden/verdicts` filters one complete, newest-first local snapshot by
priority/source. Its unchanged `total` equals the number of returned matches,
including older auto-quality rows beyond the former 500-row cutoff. The SQLite
read runs off the event loop; no paging parameters or new response fields.

Relabel-audit registers literal commands before `/{item_key}`. `POST /reset`
needs no verdict body and deletes only the configured session, including an
idempotent repeat; ordinary paper submissions keep their existing body contract.

Relabel metrics require at least two paired responses before computation: zero
or one returns HTTP 400 `insufficient_responses`, without invoking the metric
libraries or modifying the session. The existing metric formulas are unchanged.

`VerdictRequest.item_key` strips surrounding whitespace before its nonempty-string
check. Provenance lookup, storage, events and effects therefore receive the same
key; the comment is untouched. Missing CSV provenance delegates prior-model
selection to the golden command instead of repeating its read/fallback in the
route. The feedback event uses the stored original priority before effects run.
