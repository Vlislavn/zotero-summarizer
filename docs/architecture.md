# How It Works

Zotero Summarizer is a local FastAPI app backed by SQLite. The supported user workflow is:

1. Browse local Zotero items in the browser UI.
2. Select PDF-backed papers.
3. Start a triage job.
4. Review queued Zotero changes.
5. Explicitly apply or reject those changes.

The app is intentionally local-first. It reads from the local Zotero SQLite database, writes its own triage/corpus state to local SQLite files, and writes to Zotero only through the pending-change apply flow.

## Package Layout

| Path | Purpose |
|---|---|
| `zotero_summarizer/api/` | FastAPI app factory, error handlers, and thin route modules |
| `zotero_summarizer/services/` | Business logic for summarization, triage jobs, pending changes, Zotero actions, corpus, results, and config |
| `zotero_summarizer/storage/` | SQLite migrations, repositories, and embedding corpus cache |
| `zotero_summarizer/integrations/` | Zotero reader/writer, PDF extraction adapter, and LLM adapter protocols |
| `zotero_summarizer/mcp/` | MCP server and tools that call the local API |
| `zotero_summarizer/web/` | Static browser UI and results dashboard |
| `zotero_summarizer/settings.py` | Explicit settings loader from `.env`, environment, and project root |
| `goals.yaml` | Research goals, triage criteria, model names, prompt templates, and corpus settings |

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
7. `scoring.compute_composite_score()` combines LLM dimensions and corpus affinity.
8. The result is persisted to `triage_history.db`.
9. `pending.queue_changes_for_item()` creates pending Zotero changes for review.

Long PDFs are split into two chunks before the final refine prompt. LLM and SQLite operations run in worker threads so the FastAPI event loop remains responsive.

## Storage

`triage_history.db` stores:

- `triage_results`
- `batch_runs`
- `user_feedback`
- `pending_changes`
- `triage_jobs`
- manual dimension overrides

`corpus_cache.db` stores corpus metadata, embeddings, and schema migration metadata.

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

## RSS Feed Daemon (Phase 1.5)

A second subsystem — see [feeds.md](feeds.md) — runs alongside the library
triage pipeline. It consumes Zotero's `feedItems` table (RSS aggregator state)
rather than the user library, and is structured as a long-running daemon
(`feeds serve`):

- Every 5 minutes: triage K=5 unread items, mark them read in Zotero, resolve
  any due outcomes from prior materializations.
- Every 24 hours: plateau-select 1–2 best items from the rolling 24-hour
  triaged pool and materialize them directly into the **Inbox** collection
  (bypassing the pending-changes queue — feed creates are low-blast-radius).

The daemon's selection criterion is `feedItems.readTime IS NULL`, not a
date window — Zotero's unread badge IS the work queue. Materialized items
get a `<!-- zs:note_type=triage;version=3;... -->` provenance comment and
the auto-tag `/zs/feeds-v3` (itemTags.type=1) so they're machine-distinguishable
from user-written notes.
