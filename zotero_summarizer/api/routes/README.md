# api/routes — HTTP endpoints (thin)

`GET /api/daily/members?item_id=…` accepts1–20 positive SQLite PKs and returns
one current-state record per distinct ID, including explicit unavailable records.
The sibling `_daily_members` route performs no selection or write. It retains the
existing authenticated API boundary; browser snapshots never authorize mutations.
Current note-delivery metadata excludes private note bodies. Existing daily slate
and action endpoints retain their shared contracts.

`GET /api/daily` also returns `unreviewed` (the Today shelf, slimmed in `daily._shelf_payload`).

`POST /api/daily/events` (`_daily_events`) appends browser Today events (shown, dwell,
review/link opens) through `services/triage/today_exposure`; the request model is the
whole validation (enums, ids, ≤50 events, bounded durations). `GET /api/daily` also
returns the logged `slate_id`; saving a review note logs its length, not its text.

`POST /api/daily/verdict` and `POST /api/daily/verdict/undo` (`_daily_verdict`) record /
retract ONE card's decision (must/should/could/dont_read) plus optional canonical reason
chips (#37). The chip ids are validated against a fixed allowlist and stored as
`{chips[], surface, v}` in `label_verdicts.reasons_json`; omitted/empty chips → `reasons_json`
NULL (a decision with no reason), and a fresh verdict without chips re-clears it. Reasons
are per item — never set in a batch. Positive priorities still pass the shipped
`require_review` gate (a write to the owner's Zotero), so the UI offers "Generate a review
first" rather than writing an unreviewed paper.<br>
`conditions[]` is reserved in the payload for the later «depends-on» capture and is not
populated yet.

Search replies use each session domain's own projection: Discovery applies its
context/privacy view; ordinary Search retains its existing serialization boundary.
No private Discovery snapshot is exposed through screen or session GET.

Search screen optionally accepts confirmed `constraints` (required/excluded phrases
and publication types). Missing constraints never authorize model-imposed filters;
legacy sessions disclose unknown origin. Review status adds typed stage/recovery
and metadata-only attempt diagnostics without exposing sensitive capture through HTTP.

`GET /api/daily` runs its slate, preparation status and awaiting count in one
`runtime.request_memo` scope (currentness and pool computed once). It serves the analyzed policy (`selection_policy="analyzed-today-v1"`):
only papers whose current review passes `daily_select._recommendation`, each with a
`recommendation` (`eligible`, `reason`, `priority`, `consumption`), plus a top-level
`preparation` status (`running`, `ready`, `left`) from `today_preparation`. The interleave
experiment, reachable only from this GET, no longer collects. `/api/daily/members`
facts carry the same current `recommendation`; membership and Add readiness are unchanged.

`GET /api/daily` adds the resolved `selection_policy` (also for empty slates)
and each card's `selection_evidence`: stored-score presence/priority, current
review join, cohort key and actual picker/bonus observations. Interleave exposes
only a blinded evidence marker, never new per-arm diagnostics. These observations
neither authorize Add nor change ranking, labels or scores; absent/stale/failed
reviews remain unknown on a current join miss.

`POST /api/golden/verdict` checks a new positive feed verdict for a current,
usable full-text review **before** writing its label. Missing/empty/failed
reviews return HTTP 409 `review_required`; negative verdicts remain immediate.
Both `/api/daily/verdict` and `/api/golden/verdict` gate a new positive feed
label before it can hide a paper from Today. Today Add reports the same code
per blocked item while processing eligible batch members. The server, not the frontend badge, enforces this invariant.
Replacing a hosted credential through `/api/setup/ai-credential` invalidates
prior Doctor inference success without returning the key to the browser.
`POST /api/setup/doctor/cancel` requires the current `run_id`; stale IDs return
409. API task cancellation abandons that same attempt. Neither response claims
remote termination; callback exclusion and unresolved receipts remain service-owned.

Search screen also accepts `evidence_policy: baseline|question_evidence` (default
baseline). The explicit experimental choice persists through the existing review
worker/session API. Responses include session `evidence_status` and per-candidate
`question_evidence` with original ranges, fingerprints, capability flags and failures.
No evidence endpoint or native-write authority is added; Add remains explicit.

`llm.py` also exposes `/api/llm/connections`: GET context/revision; POST explicit
inactive registration; `/{uuid}/catalog` metadata; `/impact` then `/apply` explicit
`targets` assignment (default-only when omitted); `/{uuid}` exact persisted-effect readback. `/credential` previews/writes
shared authorization separately, and `/credential/{token}` reads its stored/unknown
receipt. `/test` is separately explicit: one bounded fixed-prompt probe of the saved
default, with response/refusal/UNKNOWN and no stage fallback. `/test-tasks` accepts
`revision` plus unique named `stages`, and returns their saved identities, ordered
results and currentness through the same bounded attempt owner. Neither includes
Telegram or certifies real-paper processing. Non-test connection endpoints never
infer/download/open authorization. Crossed routes require explicit
choices plus refreshed impact before apply. Blocking catalog/probe/lock work runs
in FastAPI's sync worker pool, reusing the existing Setup attempt owner. GET `/api/config` adds `_revision`;
PUT must return it (428 missing, 409 changed), including legacy full-config clients.
The legacy Setup credential PUT accepts an optional expected revision and returns
a lock-bound post-write revision for its own draft; it cannot bypass shared-slot
preview. Task IDs/credential slots are not operator-entered names. Uncertain acknowledgements
never authorize automatic mutation replay. See `services/llm/README.md` for limits.

One module per resource. Each registers its paths on a router; `__init__.py`
collects them via `include_routes(app)`. Handlers parse/validate and delegate
to `services/`; they raise `APIError` for failures.

```
__init__.include_routes(app)
   └─ for module in (health, corpus, results, zotero, triage, pending,
                     review, relabel_audit, daily, golden, admin, config, library, llm, setup, sync,
                     research_feed):
          app.include_router(module.router)
```

| file | endpoints (prefix) |
|---|---|
| `daily.py` | `/api/daily*` — the Today slate (cards ordered by the shared relevance×goal×prestige blend; payload: `papers` incl. per-card `goal_sim`, `pool_size`, `low_relevance_hidden`/`weak_slate` weak-week banner signals — the model role hides dont_read-band picks, no more `capped_at` — the pool is no longer truncated before role allocation), add-to-library (optional `target_collection_key` from the collection picker; None → Inbox)/trash, backlog drain (uses the configured `backlog` stage provider; the default gate-only drain now FAIL-FASTs via `readiness.require("classifier_gate")` → `503` with the real reason instead of a doomed background spin), `rescore-slate` (re-score the current slate in place with the live gate after a model upgrade). The keep/trash actions retain their `human_feedback` event; deliberate label writes additionally flow through `golden.label_verdicts` so an actual prior→new label transition is preserved. The per-card `verdict`/`verdict/undo` endpoints live in `_daily_verdict` (above) |
| `llm.py` | `POST /api/admin/llm-check` — manual operational probe of each pipeline stage's provider (returns per-stage operational\|fail); `GET /api/admin/llm-reachability` — cheap proactive reachability of each stage (`GET /models`, no tokens; returns per-stage `reachable` + `base_url`), polled by the deep-review surface to warn before a run; `GET /api/llm/checklist-engine` (is a cloud key saved) and `POST /api/llm/checklist-engine/test` (Settings → Checklist engine: saves a given cloud key to the OS keyring, never echoed, then asks the chosen engine one tiny question); `GET /api/llm/step-history` — median time and last success per step from existing run logs (Settings models table); `POST /api/admin/llm-models` — list a provider's available model ids for the Settings model-picker. Remote secret discovery requires a built-in preset or a current exact connection-task approval; saved membership alone and arbitrary draft bodies are insufficient. Local legacy listing never receives a stored secret; explicit none never resolves one |
| `golden.py` | `/api/golden*` — labels, verdicts, review-detail, review-note, effective labels. `POST /verdict` resolves one real Zotero mirror target before side effects: a positive Today-feed verdict auto-materializes into Inbox and returns its new key; any already-materialized feed verdict (including `dont_read`) reuses the existing key; an unmaterialized negative stays local and never creates a rejected item. Label and comment mirror to that real key, never the synthetic `feed:` key. Newly-created items revalidate current label intent after materialization; an already-correct tag requires no physical write. `POST /review-note` `{item_key, note}` saves the user's free-text "My notes" jot to the `review_notes` table (always durable) then best-effort mirrors it to the Zotero item under `USER_NOTE_MARKER` (`note_written`/`note_error` in the reply; unmaterialized feed/note keys skip the mirror, refuses while Zotero is open) — decoupled from the verdict; `review-detail` returns the saved note as `user_note`. The local `label_verdicts` row is always the durable write, and `zotero_unavailable` mirror failures are swallowed (other write failures are reported in `label_error`/`note_error`). `GET /verdicts` takes an optional `source` provenance filter (e.g. `source=auto_quality` to list the auto quality-gate's hides for one-tap restore, without paging every `dont_read`). Submitting/deleting a verdict retains the backward-compatible `human_feedback` event and also records an idempotent `label_transition` assignment/change/retraction through the golden command seam |
| `library.py` | `/api/library*` — whole-library reading queue (+ score `distribution`; `limit`≤10000; rows carry `abstract_preview` (260 chars), `decided` (owner's own verdict → sorted after undecided) and a plain-sentence `why_reason`, the response `decided_count`; `semantic=true` + `search` → HYBRID search ranking: BM25 + dense embeddings + local cross-encoder rerank, response adds `semantic`/`reranked`/`reranker_loading`/`semantic_unavailable`), `reading-queue/status` (cheap in-memory job state, no Zotero read — polled while a Rescore computes), `review-fleet/run` + `review-fleet/status` — kick off / poll the background fleet that PRE-DECIDES a `proposed_verdict` for the chosen Read-next picks (explicit `item_keys` when the client pins its cool must/should-read set, else the top-`top_k` undecided picks — so the fleet reviews the SAME rows the UI counts, not a band-agnostic slice; reuses cached deep reviews; suggestions the user Confirms/Overrides, never auto-applied; status distinguishes `no_fetchable_source`, attempted `needs_library_login`, and setup-only `browser_extra_unavailable`; only login failures populate `needs_login_items` links), `university-access/status` + `university-login` — readiness + the one-time HEADED library login for the fleet's browser PDF fetch (non-arXiv/paywalled, via the optional `browser` extra), `fetch-fulltext` (+ `/status`) — acquire arXiv/Unpaywall/PMC/OpenAlex/direct OA PDFs and attach them natively to Zotero (background job; non-interactive, typed per-item outcomes, backup-first + connector-guarded), deep-review (the **per-paper** `item_key` run passes `acquire_missing=True` — a pick with no Zotero PDF gets one fetched first via `_pdf_acquire` (OA/PMC/library session) and reviewed from it, instead of being flagged `needs_pdf`; the top-`top_k` run doesn't, since the fleet pre-acquires), PDF stream, `section-summaries/{item_key}` (POST, idempotent) — Paper-map one-liners built on first open (`running`→`ready`/`unavailable`; sidecar, see `services/library/README.md`), `render/{item_key}` status (flags `stale` when the renderer revision changed) + `render/{item_key}/build` (can acquire a missing PDF via the OA/browser chain when the item has no Zotero attachment) + `/presentation` (served **inline** so the reader pane embeds it in an iframe) + `render/{item_key}/pdf` (serves the source PDF the brief was built from — acquired-cache or Zotero-attached — `inline`) + `/figures/{name}` — paper-brief artifacts written next to the PDF (single-file HTML brief + figures + audit; no automatic Markdown; arXiv source download only with explicit consent), `ask` (POST) — grounded per-paper Q&A in three `Literal`-validated modes (`comprehensive` uses structured review + PDF body; `full_text`; `retrieval`) with whole-document count answers and quote-grounded abstention (`answer=null` = honest abstain), `sync-rel-tags` (write `zs:rel/<band>` relevance tags → filter in Zotero), `sync-score-ranks` (stamp a whole-library goal-blended rank into every paper's Zotero Call Number → sort the entire library in Zotero; preserves users' own Call Numbers); both whole-library + backup-first |
| `research_feed.py` | Read-only `GET /api/research-feed/runs` (latest 50 by publication time across sources; `limit` 1–100; unavailable/undated bundles use saved filesystem time), `/runs/{run_id}`, and `/runs/{run_id}/download?format=json\|md`. Reuses published v1 RSS bundles. Missing files are 404; malformed/unsupported JSON is 422; list entries mark unavailable reports. RSS reads perform no generation/model work or Zotero writes. Telegram source-specific v1/v2 bundles use this same report reader. `GET/PUT /api/research-feed/telegram/sources` read/save only Telegram profile fields; `GET /telegram/status` reads safe historical action/verification status. Explicit `POST /telegram/runs` requires literal consent and runs the bounded dedicated collection child followed by the selected local review, under one cross-process lock. No HTTP auth, auto-join, cloud fallback or hidden work on reads/saves. Downloads snapshot attachment bytes before responding; JSON validation uses the exact bytes returned, without a deferred file reopen. |
| `search.py` | `/api/search*` — Targeted Search (query-driven pull). `POST /screen` `{query, questions}` runs intent→plan→federate→rank, saves a `ResearchSession`, then **auto-starts** the review and returns the session reloaded (status already `reviewing`; the client just polls GET). The background worker runs the agentic PRF refinement rounds first when enabled (`services.search.refine`), then the light+deep review (a failure is recorded on the session, not swallowed); `POST /{id}/review` is kept as an explicit re-trigger — both paths single-flight through an atomic `screened`→`reviewing` claim so they can't stack workers; `GET /{id}` polls it (status `screened`→`reviewing`→`reviewed`/`error`); `GET /` lists sessions; `DELETE /{id}`; `POST /{id}/materialize` `{candidate_id, collection_key}` files one candidate into the chosen Zotero collection (None → Inbox) — the domain's ONLY Zotero write, always an explicit user action. Thin over `services.search.pipeline`; sessions persist one-JSON-each under `data/search/` |
| `review.py` | `/api/feeds/review*` — Phase 1.14 feed-review workflow; bulk-confirm requires the exact displayed `processed_ids` (max 500) and cannot label an unseen backend superset |
| `pending.py` | `/api/pending*` — review + apply queued Zotero changes. `POST /api/pending/apply` takes `retry` (default False): when True it re-applies FAILED rows (instead of PENDING) via the same writer path, and success transitions those rows to APPLIED. HTTP success can still contain per-row failures; the UI surfaces that as an error with the Failed-tab recovery path |
| `zotero.py` | `/api/zotero*` — read library items/collections/tags, set tags; `items/{key}/priority` writes the human `label:<priority>` ground-truth tag; `items/{key}/figures` attaches the paper's extracted figure PNGs as child image attachments (dedup by filename, connector force-gated) |
| `triage.py` | `/api/triage*` — run/list/cancel triage jobs |
| `admin.py` | `/api/admin*` — refresh-labels, retrain, model card. `retrain` now **hot-swaps** the freshly-trained gate into the live runtime + re-scores the Today slate (via `feeds.install_gate`), so it takes effect without a server restart; the job result carries `hot_swapped` + `rescored`. `retrain` now claims the single-flight `_RETRAIN_LOCK` **synchronously** (non-blocking acquire) before spawning the worker — the old `locked()` precheck raced (the worker acquires later, on its own thread), so a fast double-click double-trained + double-hot-swapped; the worker releases it on every exit path. The model-card handler (`model_card`) lives in `services/model/model_card.py` and is **re-exported** here (layering: no api→api import); route registration is unchanged |
| `setup.py` | `/api/setup*` — readiness, Zotero/path setup, validation, calibration, AI presets/credentials, and `GET/POST doctor`. Presets include the hardware-evaluated local catalog; `llm.enabled=false` is a valid ML-only state, so Doctor skips only AI checks by choice. Credential responses remain redacted. |
| `sync.py` | `/api/sync/*` protocol v1 — `pull?since=` compact working-set snapshots + monotonic changes, ordered typed `push` mutations with UUID replay safety/per-field conflicts, and status. Device and item identities are trimmed and blank values rejected. Applied verdicts/notes run the same idempotent training/materialization/Zotero effects as online saves, including recovery on UUID replay. The client never sees table names or database files; the default is loopback; remote access requires the explicit owner-authenticated HTTPS/private-Unix gateway |
| `feedback_analysis.py` | `/api/feedback-analysis*` — Ops "Your decisions": `POST run` (one background analysis, 409 if running), `GET status\|runs\|latest` (plain-English summary), `PUT weekly` (on/off). Thin layer over `services/golden/feedback_analysis` |
| `relabel_audit.py` | `/api/relabel-audit*` — test-retest reliability study |
| `results.py` · `corpus.py` · `config.py` · `health.py` | dashboard/corpus/config/health |
| `_golden_helpers.py` | pure (non-HTTP) helpers for `golden.py` |
| `_golden_border.py` | the `/api/golden/border-suggestions` active-learning endpoint, split out of `golden.py` to keep it under the 500-LOC cap; mounted on `golden.router` via `include_router` |

`GET /api/library/related/{item_key}?collection=&tag=` returns metadata-hybrid
saved-paper connections, comment availability and the configured processing
boundary, without a semantic call. `POST /api/library/related/{item_key}/assess`
requires that boundary's `consent_token`; optional `retry` explicitly recovers a
failed/interrupted attempt. It persists/coalesces contextual assessments through
the existing session store, never Search auto-review or Add. Full private seed
and candidate notes stay out of provider search queries. Unknown, failed,
interrupted, stale and over-budget assessments are distinct from genuine empty.
See [feature boundary](../../../docs/features.md#related-saved-papers).

Discovery GET/strict POST: `/api/library/related/{item_key}/discover`.
All session responses use controlled views; raw snapshots are storage-only.
Discovery cannot auto-review; explicit Review/new Add preflight freshness.
See [Discovery](../../../docs/features.md#saved-paper-discovery).

Ask Paper additionally accepts up to 20 typed prior turns. The library service
keeps a recent tail, compacts older evidence to extraction-versioned handles,
and returns separate claimed/quote-verified/location-verified citation state.

Review-fleet forwards explicit selections unchanged, including `[]` (no job,
`accepted=False`). Only absent/null keys enable automatic top-K selection. The
shared fleet service validates paths and rejects feed/note namespaces before
scheduling, and deduplicates keys in first-seen order for both HTTP and direct callers.
The narrower route-only stable-feed filter is removed.

**Boundaries:** import `services/` + `models`; never the reverse.

Search's shared request schema enforces a nonblank topic up to 4,000 characters,
at most ten nonblank questions, each up to 1,000 characters, before dependencies
are constructed. Strict offline rejects both screen and review kickoff before
claiming a session. Candidate IDs are persisted and included in all session
responses; plan responses include the server's ordered `display` projection.

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
409 without changing either subscription. RSS POST accepts optional `create_only`
(default false): an existing normalized subscription is returned unchanged, even
when disabled. Preset adds use this mode; custom upsert semantics stay unchanged. Refresh network/document failures
return 502 after the reader records last_error, never a successful empty refresh.
Each `GET /api/rss/feeds` row also carries `item_count` (stored items; 0 when none). The `last_error` of each `GET /api/rss/feeds` row is last-source-issue text, not a failure boolean:
`RssNamespaceWarning:` explicitly marks nonfatal unbound-prefix compatibility.
That successful attempt reports committed counts and no `errors[]` failure; zero
items remain zero. Other malformed documents still reject and roll back, and a
clean refresh clears the issue. The validation copy supplies no semantic namespace
URI or item content.

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

Before the first golden export, an absent CSV means no derived provenance, not
a missing paper/PDF. The shared HTTP provenance loader returns an empty set so
first verdicts and their review details remain usable; current labels live in
SQLite. Existing-file read/parse failures still propagate, and the explicit
file-loading service retains its missing-file error contract.

Review-note saves now deliver current committed intent, not the request's text,
so an older delayed request cannot overwrite a newer mirror. Genuine delivery
errors propagate as HTTP failures after the local save; retry is safe. Successful
replies keep `saved/note_written/note_error` (the latter is null); unconfigured
Zotero and feed/note keys keep their local-only success. This supersedes the
review-note best-effort failure behavior above, not verdict-comment enrichment.

Sync v1 additionally admits explicit `keep_server`: a conflict UUID and compared
field revision, with null value/comment rather than an echoed canonical body.
Missing/wrong references refuse; a changed field conflicts again. This records a
no-op app receipt without a field/history/native effect, not authorization to
write or acknowledge old native work. Set/delete DTO caps remain unchanged;
canonical conflict readouts may contain longer valid server text. Older servers
must reject an unsupported operation rather than reinterpret it.

Sync push accepts at most 100 mutations and 100 optional `predecessors` UUIDs.
These reference applied receipts for cross-batch causality; they never alter a
mutation's immutable UUID/body identity. Unknown/conflict receipts return 422
before writes. Existing clients may omit this field for a single batch.
Verdict rationale delivery now also uses current intent and propagates real
mirror failures after the durable local save, superseding the old soft-error
description above; the explicit unconfigured-Zotero boundary remains local-first.

`GET /api/pending?item_key=...` passes the item filter through to the indexed
storage query. `GET /api/triage/jobs?active_only=true` returns persisted running
and cancelling jobs without limiting the lookup to newest terminal jobs.
