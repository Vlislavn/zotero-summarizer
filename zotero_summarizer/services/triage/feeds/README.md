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
| `_triage.py` | abstract-only triage primitive + concurrent scoring + prestige re-score (accepts a `triage_llm` override — the backlog drain passes the optional `CUSTOM_*` provider) |
| `_gate.py` | Phase 1.13 classifier gate, counterfactual audit, background retrain |
| `_daily.py` | daily plateau selection, full-text refine, row→payload reconstruction |
| `_tick.py` | one daemon tick — pick → gate → triage → persist. Auto-resolved feeds are filtered by `feeds.exclude_feeds` (non-paper feed NAMES, e.g. GitHub releases — never scored/materialised); library dedup normalises DOIs (URL/prefix variants) and skips an item on a lookup error (never re-materialises a dupe); `allow_daily_selection` gates auto-materialization |
| `_outcomes.py` | outcome detection: what the user did with a materialized item → feedback |
| `_loop.py` | the long-running asyncio loop driving `run_daemon_tick` |

**Boundaries:** imports `model/` (gate, prestige, surprise), `zotero/` (pending),
and `storage.feeds`; standard services rules. `_common` is the leaf — siblings
import from it, never the reverse.
