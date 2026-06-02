# services/triage/feeds — the RSS daemon

Turns unread Zotero RSS items into scored `processed_feed_items` rows and, once
per day, materializes the best 1-2 directly into the Zotero Inbox. The package
is a facade (`__init__.py`); each concern lives in a private sub-module.

```
run_daemon_loop ─every N s→ run_daemon_tick (_tick)
   pick round-robin → dedup → gate(_gate) ─reject──> recorded
                                  └─keep──> _triage (LLM score) ─> triaged_pending
   mark read in Zotero · resolve due outcomes (_outcomes) → user_feedback
        once/day ▼
   run_daily_selection (_daily): plateau-pick top 1-2 (+black-swan)
        → full-text refine → materialize into Inbox → schedule outcome check
```

| file | responsibility |
|---|---|
| `__init__.py` | facade: re-exports the public + test-accessed API |
| `_common.py` | constants, `TriagedCandidate`/`DaemonTickReport`, conn + config helpers (leaf) |
| `_triage.py` | abstract-only triage primitive + concurrent scoring + prestige re-score — incl. the cold-start author prior via `cold_start_policy_from_config` (accepts a `triage_llm` override — the backlog drain passes the optional `CUSTOM_*` provider) |
| `_gate.py` | Phase 1.13 classifier gate, counterfactual audit, background retrain. `install_gate()` is the **single source of truth** for "a fresh gate is live": atomic swap + immediate Today-slate rescore — both the daemon retrain (`_gate_retrain_worker`) and the UI `POST /api/admin/retrain` install through it, so the in-memory gate and the slate never drift from the on-disk artifact. `schedule_slate_rescore_async()` re-scores on a background thread (used at startup when a cached gate loads with an unchanged sha → no retrain fires) |
| `_daily.py` | daily plateau selection: candidate scoring, plateau-pick, black-swan allocation, full-text refine, reject-cutoff |
| `_daily_materialize.py` | the write half of daily selection: row→payload/note/tags reconstruction (`processed_feed_items` row → Zotero) + `materialize_pick` (one pick → Inbox + DB decision) + `_PendingScoredRow` |
| `_tick.py` | the thin daemon-tick orchestrator — sequences the phases below for one tick; `allow_daily_selection` gates auto-materialization |
| `_tick_phases.py` | the tick's phases: round-robin pick, dedup-prep, library dedup, triage stage, record decisions, mark-read, daily trigger + `_TickResults`. Auto-resolved feeds are filtered by `feeds.exclude_feeds` (non-paper feed NAMES, e.g. GitHub releases — never scored/materialised); library dedup normalises DOIs (URL/prefix variants) and skips an item on a lookup error (never re-materialises a dupe) |
| `_outcomes.py` | outcome detection: what the user did with a materialized item → feedback |
| `_loop.py` | the long-running asyncio loop driving `run_daemon_tick` |

**Boundaries:** imports `model/` (gate, prestige, surprise), `zotero/` (pending),
and `storage.feeds`; standard services rules. `_common` is the leaf — siblings
import from it, never the reverse.
