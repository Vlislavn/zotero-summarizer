# api/routes — HTTP endpoints (thin)

One module per resource. Each registers its paths on a router; `__init__.py`
collects them via `include_routes(app)`. Handlers parse/validate and delegate
to `services/`; they raise `APIError` for failures.

```
__init__.include_routes(app)
   └─ for module in (health, corpus, results, zotero, triage, pending,
                     review, relabel_audit, daily, golden, admin, config, library, llm):
          app.include_router(module.router)
```

| file | endpoints (prefix) |
|---|---|
| `daily.py` | `/api/daily*` — the Today slate, add-to-library/trash, backlog drain (uses the configured `backlog` stage provider), `rescore-slate` (re-score the current slate in place with the live gate after a model upgrade) |
| `llm.py` | `POST /api/admin/llm-check` — manual operational probe of each pipeline stage's provider (returns per-stage operational\|fail); `POST /api/admin/llm-models` — list a provider's available model ids for the Settings model-picker |
| `golden.py` | `/api/golden*` — labels, verdicts, review-detail, effective labels |
| `library.py` | `/api/library*` — reading queue (+ score `distribution`), deep-review, PDF stream, `sync-rel-tags` (write `zs:rel/<band>` relevance tags → filter in Zotero), `sync-score-ranks` (stamp a whole-library goal-blended rank into every paper's Zotero Call Number → sort the entire library in Zotero); both whole-library + backup-first |
| `review.py` | `/api/feeds/review*` — Phase 1.14 feed-review workflow |
| `pending.py` | `/api/pending*` — review + apply queued Zotero changes |
| `zotero.py` | `/api/zotero*` — read library items/collections/tags, set tags |
| `triage.py` | `/api/triage*` — run/list/cancel triage jobs |
| `admin.py` | `/api/admin*` — refresh-labels, retrain, model card |
| `relabel_audit.py` | `/api/relabel-audit*` — test-retest reliability study |
| `results.py` · `corpus.py` · `config.py` · `health.py` | dashboard/corpus/config/health |
| `_golden_helpers.py` | pure (non-HTTP) helpers for `golden.py` |

**Boundaries:** import `services/` + `models`; never the reverse.
