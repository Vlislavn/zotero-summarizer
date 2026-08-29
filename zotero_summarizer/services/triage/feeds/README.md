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
| `_triage.py` | abstract-only triage primitive + concurrent scoring + prestige re-score — incl. the cold-start author prior via `cold_start_policy_from_config`; exact HackerNoon hosts select the practitioner prompt while retaining the common result/scoring path (accepts a `triage_llm` override — the backlog drain passes the optional `CUSTOM_*` provider) |
| `_gate.py` | Phase 1.13 classifier gate, counterfactual audit, background retrain. In `gate_only` mode an item the gate cannot score (still no title+abstract after the OpenAlex backfill in `model.predict`) has no LLM fallback, so it is returned as a terminal gate-reject `(item, None)` instead of a predictionless survivor — `record_tick_decisions` records it `gate_rejected:gate_unscorable:no_abstract` and marks it read, so one no-abstract item no longer crashes the whole drain. `install_gate()` is the **single source of truth** for "a fresh gate is live": atomic swap + immediate Today-slate rescore — both the daemon retrain (`_gate_retrain_worker`) and the UI `POST /api/admin/retrain` install through it, so the in-memory gate and the slate never drift from the on-disk artifact. `schedule_slate_rescore_async()` re-scores on a background thread (used at startup when a cached gate loads with an unchanged sha → no retrain fires). `install_gate` clears `RuntimeState.classifier_gate_error` on success and the retrain worker records it on failure, so the readiness probe reports WHY the gate is `None`. The background `_gate_retrain_worker` now threads `triage_db_path` into `train_and_save`, so the daemon retrain applies the SAME `hybrid_gt` verdict/outcome overlay as `/admin/retrain` (without it it trained on raw-CSV labels, missing user verdicts + the unchecked-add downgrade) |
| `_daily.py` | daily plateau selection: candidate scoring, plateau-pick, black-swan allocation, full-text refine, reject-cutoff |
| `_daily_materialize.py` | the daily-selection write half: restores the exact persisted summary after restarts (logged refined/persisted/legacy source + note size/sections), then writes Inbox + note + tags and flips the decision; legacy rows retain the sparse fallback. The shared writer now makes every item creation backup-first and refuses while Zotero is running; a guarded failure leaves the row pending for a later tick |
| `_tick.py` | the thin daemon-tick orchestrator — sequences the phases below for one tick; `allow_daily_selection` gates auto-materialization. `_resolve_tick_flags` derives the per-tick dedup/mark-read/outcome config; `_build_tick_report` assembles + logs the `DaemonTickReport`. Auto-review/render consume only the current versioned deep-review contract, so a prompt/policy upgrade refreshes stale cache entries instead of silently automating from them. Their assemblies pass an explicit `quality_first=rank_quality_first_enabled()` — the P3 interleave dispatch (`ZS_RANK_INTERLEAVE`) is for the user-facing GET only; a daemon-internal merge writing `interleave_log` first would claim the day with attribution the user never saw and widen auto-review beyond the shipped arm |
| `_tick_setup.py` | `resolve_tick_adapters` — the per-tick reader/writer/zotero-reader resolution phase (app-RSS source reader vs the OPTIONAL Zotero read/write adapters, each degrading loudly when Zotero is absent) |
| `_tick_phases.py` | the tick's non-dedup phases: round-robin pick, dedup-prep + **identity dedup** (`prepare_unprocessed`, same `feed_item_id`), triage stage, record decisions, mark-read, daily trigger + `_TickResults`. Auto-resolved feeds are filtered by `feeds.exclude_feeds` (non-paper feed NAMES, e.g. GitHub releases — never scored/materialised) |
| `_rescue.py` · `_rescue_l1.py` | shared full-text acquire/re-score primitive + gate-reject and G10 L1-hide rescue policies. Keeping the primitive outside `_tick_phases` removes the old reverse import from rescue policy back into orchestration |
| `_tick_dedup.py` | the tick's **content/trash dedup** phases (split out of `_tick_phases` for file-size + single responsibility). `dedup_against_processed` runs two guards, both recorded `rejected_dedup_processed` (no LLM call, never returns to Today): **trashed-GUID suppression** (always on — drops any re-arrival whose stable GUID matches a paper the user threw away via `user_rejected` / Zotero `trashed`/`deleted_all`; catches id-less items DOI/arXiv can't, and re-arrivals under a fresh `feed_item_id`) and **content dedup** (DOI/arXiv vs `processed_feed_items`, gated by `feeds.dedup_against_processed`, default = the library-dedup flag). `dedup_against_library` normalises DOIs (URL/prefix variants) and skips an item on a lookup error (never re-materialises a dupe) |
| `_outcomes.py` | outcome detection: what the user did with a materialized item → feedback. The weight→`inferred_relevance` mapping delegates to `storage.feeds.relevance_from_signal_weight` (the single shared definition next to `OUTCOME_WEIGHT`) so the feedback emitter and the training-label outcome correction can't drift. Also appends an `outcome_resolved` event to the agentic interaction log (`services.interaction_log.log_behavioural_outcome`) so the 7-day outcome joins the at-triage verdict in one replayable stream (keyed by `feed_item_id`) |
| `_zotero_readsync.py` | the read-sync reconciler: sweep Zotero's unread `feedItems`, mark read every guid the app already read (`rss_items.read_at`). This is what clears Zotero's unread badge since the app-RSS migration (step 5 only writes the app DB); also picks up user actions (Today Trash) one tick later. Gated by `feeds.zotero_read_sync` (default on) + not dry_run/review_mode; Zotero absent or DB-locked → logged skip, self-heals next tick |
| `_loop.py` | the long-running asyncio loop driving `run_daemon_tick` |

**Boundaries:** imports `model/` (gate, prestige, surprise), `zotero/` (pending),
and `storage.feeds`; standard services rules. `_common` is the leaf — siblings
import from it, never the reverse.
