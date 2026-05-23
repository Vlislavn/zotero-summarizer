# How It Works

Zotero Summarizer is a local FastAPI app backed by SQLite, with a React
single-page UI served at `/`. There are two everyday workflows:

- **Today (cull)** — feed papers are triaged (scored), ranked, and shown as
  a daily slate. You make a binary, batch call per paper: **Add to library**
  (materialize into the Zotero *Inbox*, positive training signal) or **Trash**
  (negative signal, marked read). No fine label here.
- **Library → Read next (read)** — your *unread* library papers ranked by the
  gate's relevance score (with a one-line reason); read-already items (emoji
  tags) are hidden by default. Opening one shows its "Why this score?"
  waterfall and lets you set the fine label in `Annotate`.
- **Annotate** — set/override the fine `must`/`should`/`could`/`don't` label
  on any golden-CSV or library/feed row. Your manual verdict always wins
  (display, filters, and training).

Plus a library-triage path (Power tools → Library → Browse / Triage): select
PDF-backed Zotero items, run a triage job, review queued Zotero changes, then
explicitly apply or reject them.

The app is intentionally local-first. It reads from the local Zotero SQLite database, writes its own triage/corpus state to local SQLite files, and writes to Zotero only through the pending-change apply flow.

## The big picture in five sentences

1. **Feeds in** — Zotero's RSS `feedItems` are scored by a cheap LightGBM
   gate, then survivors by an LLM, producing `processed_feed_items` rows.
2. **Today** ranks those rows into a daily slate; if nothing is fresh it
   auto-runs a background backlog drain (SOTA model via the `CUSTOM_*`
   provider) and falls back to recent rows so it's never empty.
3. **You decide** — on Today you Add/Trash (coarse, → `should_read`/`dont_read`);
   in Annotate (reached via Library → Read next) you set the fine label. Both
   are stored in `label_verdicts` and appended to `zotero-summarizer-golden.csv`.
4. **Hybrid ground truth** overlays your manual labels on top of the
   derived CSV labels — manual always wins — for both display and training.
5. **Retrain** rebuilds the gate on that hybrid ground truth, closing the
   loop so tomorrow's slate is sharper.

## Package Layout

| Path | Purpose |
|---|---|
| `zotero_summarizer/api/` | FastAPI app factory, error handlers, and thin route modules |
| `zotero_summarizer/services/` | Business logic for summarization, triage jobs, pending changes, Zotero actions, corpus, results, and config |
| `zotero_summarizer/storage/` | SQLite migrations, repositories, and embedding corpus cache |
| `zotero_summarizer/integrations/` | Zotero reader/writer, PDF extraction adapter, and LLM adapter protocols |
| `zotero_summarizer/mcp/` | MCP server and tools that call the local API |
| `frontend/` | React single-page UI (Vite + Tailwind), built to `frontend/dist` and served by FastAPI at `/`. Replaced the old Alpine `web/ui.html` in the 2026-05-15 UI redesign. |
| `zotero_summarizer/settings.py` | Explicit settings loader from `.env`, environment, and project root |
| `goals.yaml` | Research goals, triage criteria, model names, prompt templates, and corpus settings |
| `data/` | All app-generated state (gitignored): `triage_history.db`, `corpus_cache.db`, the personal `zotero-summarizer-golden.csv`/`.jsonl`, logs, and ML eval/run artifacts. Every path is derived from `Settings.data_dir`. |

There are no supported root-module compatibility imports. Use the package modules and CLI.

## Runtime Lifecycle

`zotero_summarizer.api.app:create_app()` builds the FastAPI app. During lifespan startup, `services.lifecycle.startup()`:

1. Loads settings and `goals.yaml`.
2. Builds the LLM client and PDF extractor.
3. Opens or creates local SQLite stores.
4. Initializes the embedding corpus cache.
5. Initializes Zotero reader/writer adapters when `ZOTERO_DATA_DIR` is available.
6. Marks interrupted triage jobs and resumes eligible ones.
7. Starts background corpus import when enabled.

Service modules access runtime dependencies through `runtime.AppContext`, not through import-time FastAPI globals.

## Triage Pipeline

For each selected Zotero item:

1. `ZoteroReader` loads item metadata and local PDF path.
2. `summarization.run_pipeline()` extracts PDF text through the PDF adapter.
3. `corpus.run_corpus_match()` compares the paper against the local corpus.
4. Low-affinity papers can be fast-rejected before LLM calls.
5. The refine prompt produces a structured research note.
6. The triage prompt produces score, priority, tags, dimensions, and confidence.
7. `scoring.compute_composite_score()` combines LLM dimensions, corpus affinity, and (optional) OpenAlex prestige.
8. The result is persisted to `triage_history.db` (including a new `prestige_score` column).
9. `pending.queue_changes_for_item()` creates pending Zotero changes for review.

Long PDFs are split into two chunks before the final refine prompt. LLM and SQLite operations run in worker threads so the FastAPI event loop remains responsive.

### Phase 1.8 — Prestige + two-stage triage

When `prestige.enabled: true` in `goals.yaml`, `_triage_one()` calls OpenAlex
(via `services.model.prestige.lookup_prestige`) after the LLM summary and re-computes
the composite score with the h-index/venue/citations signal blended in (default
weight 0.15). Results are cached in `corpus_cache.db` (table `openalex_cache`,
TTL 30 days), so re-runs are essentially free.

When `full_text_refine.enabled: true`, after plateau selection picks the daily
1–2 papers, `_refine_with_full_text()` resolves an OA PDF URL (arXiv → Unpaywall
→ URL fallback), streams the PDF to `~/.cache/zotero-summarizer/pdfs/` with a
size cap and `%PDF-` magic-byte check, and re-runs the full-text
`summarization.run_pipeline()`. The richer `SummarizeResponse` replaces the
abstract-derived note before materialization.

### OpenAI vs vLLM/OnPrem provider toggle

`services._adapters.build_llm()` accepts an optional `extra_body` dict that is
forwarded to the OpenAI-compatible client only when truthy. Real OpenAI
(`api.openai.com`) requires this to be empty; vLLM-served reasoning models
typically need `{"chat_template_kwargs": {"enable_thinking": false}}`. The value
lives in `goals.yaml` under `llm.extra_body`.

## Storage

`triage_history.db` stores:

- `triage_results`
- `batch_runs`
- `user_feedback`
- `pending_changes`
- `triage_jobs`
- `processed_feed_items` (the RSS feed decision log; Phase 1.14 adds the
  `shap_contribs_json` column carrying the JSON-serialised TreeSHAP
  contributions, OpenAlex author/venue snapshot, and full `SummarizeResponse`
  used by the review-mode Approve path)
- `label_verdicts` (one row per item: the user's manual priority override,
  the original derived priority it overrode, and a free-text comment. This
  is the source of truth for "manual wins" — see [Hybrid Ground
  Truth](#hybrid-ground-truth-manual-labels-win))
- `role_value_verdicts` (legacy per-slot "Time well spent / Wasted my time"
  ratings; the table + `/api/daily/role-verdict` endpoint remain, but the
  Today UI no longer collects this signal after the two-stage redesign)
- manual dimension overrides

Concurrent writers (the API + the feed daemon) are made safe by a
`busy_timeout` PRAGMA set before the WAL-mode switch in
`repositories._connect_to`, so a write waits for the lock instead of raising
"database is locked".

`corpus_cache.db` stores corpus metadata, embeddings, OpenAlex prestige
cache, and schema migration metadata.

Both stores are local runtime data and should not be committed.

## Zotero Write Safety

Triage jobs do not mutate Zotero. They queue reviewed changes:

- `tag_changes`
- `add_note`
- `add_to_collection`
- `remove_from_collection`

Only `POST /api/pending/apply` writes to Zotero. The writer checks whether Zotero is running, supports explicit force apply, wraps changes in SQLite savepoints, and creates a Zotero database backup before applying changes.

After successful apply, the app refreshes corpus metadata for affected items and removes applied items from the `Inbox` collection when possible.

## Feedback Loop

Feedback comes from three sources:

- Explicit approve/reject actions in the UI.
- Inferred corpus signals from tags, notes, annotations, and stale untouched papers.
- **Phase 1.5 outcome detection** (feeds daemon): 7 days after a paper lands
  in Inbox via the daemon, the system queries Zotero to see whether the user
  kept it (kept_inbox, weak negative), filed it to a real collection
  (moved_collection, weak positive), deleted it from all collections
  (deleted_all, **strong negative**), trashed it (trashed, **strong negative**),
  or tagged it 🧠/👀 (engaged, strong positive). The asymmetric weights
  (delete:ignore = 6:1) follow Schnabel et al. *Recommendations as Treatments*
  (ICML 2016).

Feedback is stored in `user_feedback` and is used by corpus matching and calibration metrics. Calibration compares model-positive priorities (`must_read`, `should_read`) against user-positive feedback (`approve`).

## Hybrid Ground Truth (manual labels win)

The golden CSV's `gold_priority_final` is **derived** by
`services/goldenset.py:_infer_label` (emoji tags + annotation/note counts +
age decay). The user can override any label via the UI; overrides are stored
in the `label_verdicts` table (one row per item, UPSERT), never in the CSV.

`services/hybrid_gt.py` is the single place these merge:

- `load_hybrid_labels(csv_path, db_path)` → `{item_key: {derived_priority,
  user_priority, effective_priority, source, ...}}`. `source="user"` when a
  verdict exists (it wins); otherwise `"derived"`. Verdicts whose key is no
  longer in the CSV are kept (flagged orphaned), so a manual label is never
  lost.
- `apply_hybrid(rows, db_path)` overlays the verdict onto each CSV row's
  `gold_priority_final` **and** `gold_inferred_relevance` (the regressor's
  continuous target), so retraining learns from the manual label.

Consumers: `api/routes/golden.py:list_all` (the Annotate list shows + filters
by `effective_priority`), `effective-labels` endpoints, and
`classifier_persistence.train_and_save(..., triage_db_path=...)` (training).
This is why a manual label survives a Refresh-labels re-export: the CSV is
re-derived, but the verdict is re-applied on top.

## Today Slate + In-UI Backlog Triage

`GET /api/daily` assembles a role-mixed daily slate via
`services/daily_select`. It reads `triaged_pending` / `awaiting_review` rows
from `processed_feed_items`. Two robustness features keep "Today" useful:

- **Never empty**: if the lookback window has no rows,
  `assemble_daily_slate` falls back to the most-recent scored rows
  regardless of age (`fellback_to_recent=True`).
- **On-demand backlog drain**: `services/triage_backlog.py` runs
  `run_daemon_tick` in a loop on a background thread until the unread feed
  backlog is drained, scoring survivors with a SOTA model. The triage LLM
  provider is injected (no global mutation) by threading `triage_llm`
  through `run_daemon_tick → _triage_one → summarization.run_abstract_pipeline`
  (`llm_override`). The client is built by
  `services/_adapters.build_triage_llm("sota")` from `CUSTOM_BASE_URL` +
  `CUSTOM_API_KEY`. Endpoints: `POST /api/daily/triage-backlog` (start),
  `GET /api/daily/triage-status` (poll). The cheap gate still runs first,
  so only survivors cost a SOTA call.

Acting on cards is **binary + batched** (`services/daily_actions.py`):
`POST /api/daily/add-to-library` materializes each selected card into the
Zotero *Inbox* (reusing `review.materialize_row`) and records a positive
`should_read` label; `POST /api/daily/trash` records `dont_read` and marks the
feed items read. Both write `label_verdicts` (keyed `feed:<feed_item_id>`) +
append to the golden CSV (`services/review.py:append_to_golden`), so acted
papers train the next gate **and** drop out of the slate (the slate excludes
any item that already has a verdict — `daily_select.fetch_handled_keys`).

## Library Reading Queue (Read next)

`GET /api/library/reading-queue` (`services/reading_queue.py`) ranks the user's
**unread** library items by the gate's relevance score so "what to read next"
is explainable. Like border suggestions it is **background-computed and
disk-cached**, but keyed by the *loaded gate's* `golden_csv_sha256` (so adding
papers from Today doesn't invalidate it — only a real retrain does) and scored
**incrementally** in 50-item batches. Read-status is applied **live** at request
time from Zotero emoji tags (🧠/👀/veto), so reading a paper removes it without a
rescore. Each row carries a `relevance_score` + a one-line `why_reason`.
`build_library_detail` reuses the same cached score (`get_cached_scoring`, else
a live single-item score) so the annotation detail's "Why this score?" waterfall
matches the queue ranking. Gate off → recency fallback (`model_ready=false`).

## Active-Learning Border Suggestions

`GET /api/golden/border-suggestions` ranks library rows by how close the
model's score sits to a class threshold (re-labelling those gives the most
training value per click). Because scoring every row is expensive, it is
**background-computed and disk-cached by golden-CSV sha** in
`services/border_cache.py`; the endpoint returns `{status: "computing"}`
and the UI polls until `status: "ready"`. It reuses the persisted gate model
(`classifier_persistence.load_or_train`) rather than retraining per call.

## RSS Feed Daemon

A second subsystem — see [feeds.md](feeds.md) — runs alongside the library
triage pipeline. It consumes Zotero's `feedItems` table (RSS aggregator state)
rather than the user library. Three operating modes:

**Daemon** (`feeds serve`): long-running background process.
- Every 5 minutes: triage K=5 unread items round-robin across feeds, mark them
  read in Zotero, resolve any due outcomes from prior materializations.
- Once per day at a configured local time (or every 24 h elapsed if no clock
  target is set): plateau-select 1–2 best items from the rolling 24-hour
  triaged pool and materialize them directly into the **Inbox** collection
  (bypassing the pending-changes queue — feed creates are low-blast-radius).
- Acquires a PID lock (`feeds.lock`) so only one instance runs at a time.
- DB lock resilience: write operations retry up to 3× when Zotero is syncing.

**Review-mode one-shot** (`feeds run --feeds "Name"`, Phase 1.14 default):
exhausts ALL unread items from a specific feed (`batch_size=None`) but parks
them as `awaiting_review` and SKIPS daily selection. The user clicks through
the Feed Review tab in the web UI to approve / reject / relabel. See
[Phase 1.14 review state machine](#phase-114-review-state-machine) below.

**Gate-only one-shot** (`feeds run --feeds "Name" --gate-only`, Phase 1.14):
same as review-mode but skips the LLM entirely. Each survivor of the
classifier gate is recorded with a synthesised `SummarizeResponse`
(placeholder rationale). Useful for bootstrapping golden-CSV labels through
the UI before the gate is good enough to filter aggressively — pure
classifier-driven loop with SHAP attribution doing the explaining.

**Auto-materialize one-shot** (`feeds run --feeds "Name" --auto-materialize`):
the pre-1.14 behaviour — exhausts the feed and forces immediate daily
selection scoped to that feed's own `triaged_pending` pool.

All modes accept `--feeds` (name substring or numeric ID) and `--model` (LLM
override for the session). `feeds tick` runs a single tick without acquiring
the lock and is safe to schedule via cron alongside the daemon.

The daemon's selection criterion is `feedItems.readTime IS NULL` — Zotero's
unread badge IS the work queue. Materialized items get a
`<!-- zs:note_type=triage;version=3;... -->` provenance comment and the
auto-tag `/zs/feeds-v3` (itemTags.type=1) so they're machine-distinguishable
from user-written notes.

### Phase 1.13 classifier gate

**Current performance** (LightGBM, n=1393, 5×5 stratified K-fold + BCa
bootstrap B=2000): Spearman ρ = 0.205 [0.183, 0.224], AUC = 0.570 [0.557,
0.584], NDCG@10 = 0.694. The 4-class Cohen's κ is ≈ 0.04 — treat the
ranking as the signal, not the discrete `must / should / could / dont`
label. The full report (incl. learning-curve anomaly: Spearman peaks at
n=836 and **declines** to n=1393) is in
[baseline-ceiling-20260515.md](baseline-ceiling-20260515.md).

When `classifier_gate.enabled: true` in `goals.yaml`, every tick batch-predicts
the surviving items with a trained classifier
(`services.model.classifier_persistence.TrainedClassifier`, loaded at lifecycle
startup from `~/.cache/zotero-summarizer/models/{name}.joblib`). Items whose
`predicted_priority` is in `drop_priorities` (default `[dont_read]`) skip the
LLM entirely and land as `gate_rejected`. The gate also runs a per-tick
`file_sha256` check on `zotero-summarizer-golden.csv`; a mismatch with the
trained gate's stored sha kicks off `train_and_save` in a daemon thread, with
an atomic swap on the next tick (the current tick keeps using the stale
model). The lock `classifier_gate_training` (on the app state) prevents
concurrent retrains.

For LightGBM, the gate also computes per-item TreeSHAP via
`predict_proba(X, pred_contrib=True)`. `services.model.classifier_persistence._format_shap`
aggregates the 768 SPECTER2 dimensions into one `semantic_match_specter2`
bucket, surfaces the seven named tabular extras (`has_doi`, `has_venue`,
`year_recency`, `title_log_len`, `abstract_log_len`, `corpus_affinity`,
`prestige_score`), and the bias term separately. The output, plus a snapshot
of OpenAlex author/venue stats from
`services.model.classifier._compute_aux_with_context`, is serialised into
`processed_feed_items.shap_contribs_json` so the review UI can render score
attribution.

### Phase 1.14 review state machine

Two new modules implement the review-mode workflow:

| Module | Role |
|---|---|
| `services/review.py` | Business logic: list rows by state, approve / reject / relabel transitions, golden-CSV append (with full abstract from Zotero's `feedItems`), bulk-confirm gate-rejected, batch-apply via `apply_feed_materialization` |
| `api/routes/review.py` | Thin FastAPI handlers translating domain exceptions to HTTP status codes |

`run_daemon_tick` carries a `gate_only` parameter through to the triage
loop: when true, the LLM step is skipped and each gate survivor gets a
synthesised `TriagedCandidate` from its prediction (`_synthesize_gate_only_candidate`).
The synthesised `SummarizeResponse` carries a placeholder rationale; the
review UI shows SHAP + author/venue panel instead. Implies `review_mode=True`
and is incompatible with `force_daily_selection`.

The state machine added on top of the existing decision taxonomy (in
`storage/feeds.py`):

```
              feeds run [--gate-only]
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
     gate_rejected           awaiting_review
            │                     │
            │             ┌── approve ──────┐
            │             │                 │
            │      reject / relabel=dont   relabel ≠ dont
            │             │                 │
            ▼             ▼                 ▼
       (stay or       user_rejected     user_approved
        confirm)         (terminal)         │
            │             │                 │   POST /api/feeds/review/apply-all
            │             │                 │           │
            │             │                 │           ▼
            │             │                 │   apply_feed_materialization
            │             │                 │   (item + tags + note + Inbox +
            │             │                 │    provenance + outcome window)
            │             │                 │           │
            │             │                 ▼           ▼
            │             │           selected (materialized_via_review_ui)
            │             │                 │     materialized_zotero_key=<NEW>
            │             │                 │     outcome_eligible_at=now+7d
            ▼             ▼                 ▼
   golden CSV       golden CSV       (nothing in CSV;
   (dont_read       (dont_read        approve = "model was right")
    via relabel      via reject)
    or bulk-
    confirm)
                          │
                          ▼
            sha mismatch on next feeds run start
            → lifecycle retrains gate
```

Notable invariants:

- **No `pending_changes` for feed items**. The pending-changes pipeline
  expects an existing Zotero `item_key`; feed items don't have one until
  materialisation. Review-mode apply uses
  `ZoteroWriter.apply_feed_materialization` (the daemon-direct create path)
  instead. The pending_changes pipeline remains for library-centric mutations
  (tag/note/collection updates on existing items).
- **`run_daily_selection` queries `WHERE decision = 'triaged_pending'`**, so
  `awaiting_review` rows are invisible to the auto-materialise path even
  when both subsystems run side by side.
- **Mark-as-read is suppressed in review mode** so items stay visible in
  Zotero's RSS view alongside the web-UI queue until the user has acted.
- **Bulk-confirm-gate-rejected** appends a `dont_read` golden-CSV row per
  untouched `gate_rejected` item but does NOT change the row's decision —
  the user only confirmed the model's verdict; nothing else moved.
- **Golden CSV append always pulls full abstract + authors + venue + year
  from Zotero's live `feedItems` table** (`services.library.review._fetch_feed_metadata`),
  not the 200-char `summary.abstract_preview`. Crucial for training quality —
  rows with empty abstracts get filtered out of the classifier's training set.

The learning loop closes through `zotero-summarizer-golden.csv`: every UI
action that mutates a label writes one row, the CSV's sha256 changes, and
the next `feeds run` start (lifecycle `load_or_train` in
`services/lifecycle.py`) sees the mismatch and retrains the classifier
before the tick. For a continuous `feeds serve` daemon, the per-tick
`_maybe_schedule_gate_retrain` spawns a background thread; the running tick
keeps using the stale model and the swap is atomic on the next tick.
