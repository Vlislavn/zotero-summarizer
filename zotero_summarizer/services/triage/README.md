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
   triage_backlog         : drain the un-triaged backlog with the SOTA model
```

| file | responsibility |
|---|---|
| `feeds/` | the daemon orchestrator package — facade re-exports the sub-modules below |
| `feeds/_common.py` | constants, the tick-report dataclass, low-level helpers (leaf) |
| `feeds/_triage.py` · `feeds/_gate.py` | abstract triage + concurrent scoring + prestige · classifier gate + audit + retrain |
| `feeds/_daily.py` · `feeds/_tick.py` | daily plateau selection + refine · one daemon tick (orchestration) |
| `feeds/_outcomes.py` · `feeds/_loop.py` | outcome detection → feedback · the long-running asyncio loop |
| `summarization.py` | the LLM summarize/refine pipeline (`run_pipeline`) |
| `select.py` | plateau/elbow cutoff for daily materialization |
| `daily_actions.py` | Today keep/trash → Zotero Inbox + training labels |
| `triage_jobs.py` | background triage-job lifecycle (`/api/triage/run`) |
| `triage_backlog.py` | single-thread drain of un-triaged feed backlog |
| `daily_select/` | the role-allocated Today slate (see its README) |

**Boundaries:** imports `model/` (gate), `zotero/` (pending), and shared
scoring; standard services rules.
