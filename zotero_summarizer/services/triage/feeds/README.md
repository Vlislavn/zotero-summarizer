# services/triage/feeds — the RSS daemon

Turns unread app-RSS items into scored `processed_feed_items` rows and, once
per day, materializes the best 1-2 directly into the Zotero Inbox. The package
is a facade (`__init__.py`); each concern lives in a private sub-module.

Two reader roles per tick: the triage SOURCE is `AppRssReader` (the app-owned
`rss_feeds`/`rss_items` pool, refreshed in rotation each tick); Zotero-library
questions go through a separate OPTIONAL `ZoteroReader` (library dedup, outcome
membership, read-sync) — Zotero absent means those degrade to a logged skip.

```
run_daemon_loop ─every N s→ run_daemon_tick (_tick)
   refresh app RSS (rotating) → pick round-robin
     → dedup(identity → trashed-GUID → content → library[ZoteroReader]) → gate(_gate) ─reject──> recorded
                                  └─keep──> _triage (LLM score) ─> triaged_pending
   mark read (app rss_items; legacy Zotero rows via writer)
   read-sync: app-read guids → Zotero feedItems.readTime (_zotero_readsync — clears the unread badge)
   resolve due outcomes (_outcomes, ZoteroReader) → user_feedback
        once/day ▼
   run_daily_selection (_daily): plateau-pick top 1-2 (+black-swan)
        → full-text refine → materialize into Inbox → schedule outcome check
```

| file | responsibility |
|---|---|
| `__init__.py` | facade: re-exports the public + test-accessed API |
| `_common.py` | constants, `TriagedCandidate`/`DaemonTickReport`, conn + config helpers (leaf) |
| `_triage.py` | abstract-only triage primitive + concurrent scoring + prestige re-score — incl. the cold-start author prior via `cold_start_policy_from_config` (an unknown author h-index stays `None`, including telemetry, and never becomes UI evidence); exact HackerNoon hosts select the practitioner prompt while retaining the common result/scoring path (accepts a `triage_llm` override — the backlog drain passes the optional `CUSTOM_*` provider) |
| `_gate.py` | Phase 1.13 classifier gate, counterfactual audit, background retrain. In `gate_only` mode an item the gate cannot score (still no title+abstract after the OpenAlex backfill in `model.predict`) has no LLM fallback, so it is returned as a terminal gate-reject `(item, None)` instead of a predictionless survivor — `record_tick_decisions` records it `gate_rejected:gate_unscorable:no_abstract` and marks it read, so one no-abstract item no longer crashes the whole drain. `install_gate()` is the **single source of truth** for "a fresh gate is live": atomic swap + immediate Today-slate rescore — both the daemon retrain (`_gate_retrain_worker`) and the UI `POST /api/admin/retrain` install through it, so the in-memory gate and the slate never drift from the on-disk artifact. `schedule_slate_rescore_async()` re-scores on a background thread (used at startup when a cached gate loads with an unchanged sha → no retrain fires). `install_gate` clears `RuntimeState.classifier_gate_error` on success and the retrain worker records it on failure, so the readiness probe reports WHY the gate is `None`. The background `_gate_retrain_worker` now threads `triage_db_path` into `train_and_save`, so the daemon retrain applies the SAME `hybrid_gt` verdict/outcome overlay as `/admin/retrain` (without it it trained on raw-CSV labels, missing user verdicts + the unchecked-add downgrade) |
| `_daily.py` | daily plateau selection: candidate scoring, plateau-pick, black-swan allocation, full-text refine, reject-cutoff |
| `_daily_materialize.py` | the daily-selection write half: restores the exact persisted summary after restarts (logged refined/persisted/legacy source + note size/sections), commits a stable per-row key reservation, then writes Inbox + note + tags and flips the decision only on first materialization; legacy rows retain the sparse fallback. Returns the persisted key directly; failures propagate to the run boundary. The shared writer is backup-first, refuses while Zotero runs and treats a committed key as a whole-operation no-op on retry; outcome timing and later user changes survive retries |
| `_tick.py` | the thin daemon-tick orchestrator — sequences the phases below for one tick; `allow_daily_selection` gates auto-materialization. `_resolve_tick_flags` derives the per-tick dedup/mark-read/outcome config; the tick accumulates counters directly in `DaemonTickReport` and logs its serialized result. Auto-review/render consume only the current versioned deep-review contract, so a prompt/policy upgrade refreshes stale cache entries instead of silently automating from them. Their assemblies pass an explicit `quality_first=rank_quality_first_enabled()` — the P3 interleave dispatch (`ZS_RANK_INTERLEAVE`) is for the user-facing GET only; a daemon-internal merge writing `interleave_log` first would claim the day with attribution the user never saw and widen auto-review beyond the shipped arm |
| `_tick_setup.py` | `resolve_tick_adapters` — the per-tick reader/writer/zotero-reader resolution phase (app-RSS source reader vs the OPTIONAL Zotero read/write adapters, each degrading loudly when Zotero is absent) |
| `_tick_phases.py` | the tick's non-dedup phases: round-robin pick, dedup-prep + **identity dedup** (`prepare_unprocessed`, same `feed_item_id`), triage stage, record decisions, mark-read, daily trigger + `_TickResults`. Auto-resolved feeds are filtered by `feeds.exclude_feeds` (non-paper feed NAMES, e.g. GitHub releases — never scored/materialised) |
| `_rescue.py` · `_rescue_l1.py` | shared full-text acquire/re-score primitive + gate-reject and G10 L1-hide rescue policies. Keeping the primitive outside `_tick_phases` removes the old reverse import from rescue policy back into orchestration |
| `_tick_dedup.py` | the tick's **content/trash dedup** phases (split out of `_tick_phases` for file-size + single responsibility). `dedup_against_processed` runs two guards, both recorded `rejected_dedup_processed` (no LLM call, never returns to Today): **trashed-GUID suppression** (always on — drops any re-arrival whose stable GUID matches a paper the user threw away via `user_rejected` / Zotero `trashed`/`deleted_all`; catches id-less items DOI/arXiv can't, and re-arrivals under a fresh `feed_item_id`) and **content dedup** (DOI/arXiv vs `processed_feed_items`, gated by `feeds.dedup_against_processed`, default = the library-dedup flag). `dedup_against_library` normalises DOIs (URL/prefix variants) and skips an item on a lookup error (never re-materialises a dupe) |
| `_outcomes.py` | outcome detection: Zotero membership → outcome; `storage.feeds.record_outcome` stages the first resolution and matching training signal in one transaction, using the shared outcome weights/relevance mapping. Read/write failures propagate and remain retryable; concurrent stale snapshots cannot resolve twice. After commit, appends `outcome_resolved` to the interaction log (keyed by `feed_item_id`); that JSONL append remains a separate effect, not part of the SQLite transaction |
| `_zotero_readsync.py` | the read-sync reconciler: sweep Zotero's unread `feedItems`, mark read every guid the app already read (`rss_items.read_at`). This is what clears Zotero's unread badge since the app-RSS migration (step 5 only writes the app DB); also picks up user actions (Today Trash) one tick later. Gated by `feeds.zotero_read_sync` (default on) + not dry_run/review_mode; Zotero absent or DB-locked → logged skip, self-heals next tick |
| `_loop.py` | the long-running asyncio loop driving `run_daemon_tick` |

**Boundaries:** imports `model/` (gate, prestige, surprise), `zotero/` (pending),
and `storage.feeds`; standard services rules. `_common` is the leaf — siblings
import from it, never the reverse.

App-RSS refresh errors propagate out of the tick: the reader records the failing
feed and rolls back that feed's item writes before raising. A failed refresh
cannot silently continue into triage against a stale source pool.

The retrain worker writes to `Settings.model_dir`; full-text refinement passes
`Settings.pdf_cache_dir` to the PDF integration. Both are project-local data paths.

Startup/per-tick retrain scheduling shares `model.classifier_inputs` with the
cached loader: verdict-only, config, tuning, code and corpus changes can retrain
without a CSV edit. Legacy models lacking input identity are stale. The worker
uses the currently configured classifier name, not the predecessor's name.
The existing live gate remains available while background retraining runs.
Startup and gate installation share the same quality formatter. An undefined
Spearman (`None`) logs as `quality=n/a`; measured zero remains `Spearman=0.000`.

Gate installation and Today rescoring happen in sequence, not one transaction.
A rescore error propagates to both UI and daemon callers; the new live gate is
not rolled back. Startup's rescore thread likewise reports uncaught failures.
There is no successful `None` result on error (`None` means an explicitly skipped
rescore only), and retrain failures are recorded without claiming the old gate
was necessarily retained.

Each tick owns a nonblocking SQLite exclusive lock in a separate
`<triage-db-stem>.tick-lock.sqlite` beside the Settings-provided triage database.
It covers pick/dedup/LLM/persistence/daily work for CLI, daemon and UI callers;
another tick fails before selecting items. SQLite releases it on exceptions or
process exit; there is no PID check, stale-file deletion or new locking dependency.
The lock database contains no app rows and does not block normal triage-DB writes.
This is one tick per store, not a distributed per-item lease.

The picker uses one reader call per feed in both modes: None is explicitly
unlimited end-to-end, and bounded mode interleaves with itertools without pop(0).
Reader failures propagate. Dry ticks do not refresh RSS, clear retryable errors,
schedule retraining, record decisions, mark read, resolve outcomes or start
automatic review/render work. Existing model/input caches may be read or warmed.
Tick counts are accumulated in DaemonTickReport itself; dataclass serialization
includes fatal_llm_error. The loop fails on exceptions or failed reports and
removes its signal handlers on exit; zero ticks does no work.
