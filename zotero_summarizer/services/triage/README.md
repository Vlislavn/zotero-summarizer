# services/triage — the RSS daemon pipeline

Turns raw RSS feed items into scored, ranked `processed_feed_items` rows that
the Today tab consumes. The cheap `model/` gate fast-rejects; survivors get an
LLM summary + composite score.

```
Zotero feedItems ─feeds.run_daemon_tick→ gate(model) ─reject──> dropped
                                            └─keep──> summarization (LLM) ─score→ row
   select.plateau_select  : daemon's materialization cutoff (kneedle elbow)
   daily_select/          : Today's role-mixed slate (model+surprise+diversity)
   daily_actions          : Today "Add to library" / "Trash" (write labels)
   triage_jobs            : on-demand library triage jobs
   triage_backlog         : drain the un-triaged backlog — ML-only by default
```

**ML-first drain (default).** The "Triage backlog" button runs the gate ONLY
(`gate_only=True`, `bulk_drain_gate_only` config): the classifier scores every
survivor from embeddings + prestige with **no per-item LLM call** — fast,
memory-safe, GPU-accelerated. Rows are written `triaged_pending` + marked read
(`review_mode=False`) so the slate fills and the picker drains. The full-text
LLM quality digest is **on-demand per paper** (Library → Deep Review), never run
in bulk. **Fail-fast precondition:** the gate-only drain MANDATORILY needs a live
classifier gate, so the `POST /api/daily/triage-backlog` route calls
`services.readiness.require("classifier_gate")` first — a missing gate (e.g.
`lightgbm` uninstalled, or a retrain still in flight) returns a `503` with the real
reason instead of starting a doomed background spin that re-fetches the same batch
forever (the 2026-06-16 bug). `_drain_worker` repeats the check as defence-in-depth
and its exception boundary now `LOGGER.exception`s, so a drain failure is never
swallowed unlogged again. `triage_backlog.status()` exposes `gate_reject_rate` /
`gate_onward` so the Today banner can show "filtered X% by the ML gate". Concurrency for the
remaining LLM work (legacy drain / live daemon) is provider-aware: **1 for a
local model**, the configured `TRIAGE_JOB_CONCURRENCY` for a remote one
(`services._common.effective_llm_concurrency`).

| file | responsibility |
|---|---|
| `feeds/` | the daemon orchestrator package — facade re-exports the sub-modules below |
| `feeds/_common.py` | constants, the tick-report dataclass, low-level helpers (leaf) |
| `feeds/_triage.py` · `feeds/_gate.py` | abstract triage + concurrent scoring + prestige · classifier gate + audit + retrain |
| `feeds/_daily.py` · `feeds/_tick.py` | daily plateau selection + refine · one daemon tick (orchestration) |
| `feeds/_outcomes.py` · `feeds/_loop.py` | outcome detection → feedback · the long-running asyncio loop |
| `summarization.py` | the LLM summarize/refine pipeline (`run_pipeline`) |
| `select.py` | plateau/elbow cutoff for daily materialization |
| `daily_actions.py` | Today keep/trash → Zotero Inbox + training labels. "Add" writes a PROVISIONAL verdict (`label_verdicts.source='machine_add'`, tier `feed_interest`) that the 7-day materialization outcome corrects at train time (`golden/hybrid_gt`); trash stays a deliberate `user` verdict |
| `triage_jobs.py` | background triage-job lifecycle (`/api/triage/run`); persists a snapshot copy so the DB-write thread never serialises a live-mutating job |
| `triage_backlog.py` | single-thread **ML-only** drain of un-triaged feed backlog (gate scores; no LLM); `allow_daily_selection=False` — the UI button never auto-materialises into the Inbox; `status()` surfaces `gate_reject_rate`. On completion it **auto-rescores the slate** (`rescored`/`rescore_error` in `status()`) so freshly-drained rows rank consistently with what was already there |
| `rescore_slate.py` | re-score the CURRENT Today slate in place with the live gate; rewrites only the gate-derived fields via `storage.feeds.update_scores` — never a card's decision/read-status, and skips already-handled rows so nothing is re-surfaced. It is now triggered **automatically** (not just by `POST /api/daily/rescore-slate`): after a backlog drain, after any gate retrain (daemon or UI `install_gate`), and at startup for a cached gate — so Today always reflects the current model |
| `daily_select/` | the role-allocated Today slate (see its README) |

**Boundaries:** imports `model/` (gate), `zotero/` (pending), and shared
scoring; standard services rules.
