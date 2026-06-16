# api/routes — HTTP endpoints (thin)

One module per resource. Each registers its paths on a router; `__init__.py`
collects them via `include_routes(app)`. Handlers parse/validate and delegate
to `services/`; they raise `APIError` for failures.

```
__init__.include_routes(app)
   └─ for module in (health, corpus, results, zotero, triage, pending,
                     review, relabel_audit, daily, golden, admin, config, library, llm, setup):
          app.include_router(module.router)
```

| file | endpoints (prefix) |
|---|---|
| `daily.py` | `/api/daily*` — the Today slate (cards ordered by the shared relevance×goal×prestige blend; payload: `papers` incl. per-card `goal_sim`, `pool_size`, `low_relevance_hidden`/`weak_slate` weak-week banner signals — the model role hides dont_read-band picks, no more `capped_at` — the pool is no longer truncated before role allocation), add-to-library/trash, backlog drain (uses the configured `backlog` stage provider), `rescore-slate` (re-score the current slate in place with the live gate after a model upgrade) |
| `llm.py` | `POST /api/admin/llm-check` — manual operational probe of each pipeline stage's provider (returns per-stage operational\|fail); `GET /api/admin/llm-reachability` — cheap proactive reachability of each stage (`GET /models`, no tokens; returns per-stage `reachable` + `base_url`), polled by the deep-review surface to warn before a run; `POST /api/admin/llm-models` — list a provider's available model ids for the Settings model-picker |
| `golden.py` | `/api/golden*` — labels, verdicts, review-detail, effective labels. A verdict on a library item also writes a `label:<priority>` ground-truth tag straight to Zotero (non-blocking; `label_written`/`label_error` in the response); feed:/note: keys keep the `label_verdicts` path |
| `library.py` | `/api/library*` — whole-library reading queue (+ score `distribution`; `limit`≤10000; `semantic=true` + `search` → HYBRID search ranking: BM25 + dense embeddings + local cross-encoder rerank, response adds `semantic`/`reranked`/`reranker_loading`/`semantic_unavailable`), `reading-queue/status` (cheap in-memory job state, no Zotero read — polled while a Rescore computes), `review-fleet/run` + `review-fleet/status` — kick off / poll the background fleet that PRE-DECIDES a `proposed_verdict` for the top-`top_k` Read-next picks (reuses cached deep reviews; suggestions the user Confirms/Overrides, never auto-applied), `fetch-fulltext` (+ `/status`) — download arXiv full-text PDFs for papers with an arXiv link but no PDF and attach them natively to Zotero (background job; backup-first + connector-guarded), deep-review, PDF stream, `render/{item_key}` status (flags `stale` when the renderer revision changed) + `render/{item_key}/build` + `/presentation` (served **inline** so the reader pane embeds it in an iframe) + `/figures/{name}` — paper-brief artifacts written next to the PDF (notes, single-file HTML brief with readable sections + referee digest, figures, audit; arXiv source download only with explicit consent), `ask` (POST) — grounded per-paper Q&A in three `Literal`-validated modes (`comprehensive`|`full_text`|`retrieval`) with whole-document count answers and quote-grounded abstention (`answer=null` = honest abstain), `sync-rel-tags` (write `zs:rel/<band>` relevance tags → filter in Zotero), `sync-score-ranks` (stamp a whole-library goal-blended rank into every paper's Zotero Call Number → sort the entire library in Zotero; preserves users' own Call Numbers); both whole-library + backup-first |
| `review.py` | `/api/feeds/review*` — Phase 1.14 feed-review workflow |
| `pending.py` | `/api/pending*` — review + apply queued Zotero changes |
| `zotero.py` | `/api/zotero*` — read library items/collections/tags, set tags; `items/{key}/priority` writes the human `label:<priority>` ground-truth tag |
| `triage.py` | `/api/triage*` — run/list/cancel triage jobs |
| `admin.py` | `/api/admin*` — refresh-labels, retrain, model card. `retrain` now **hot-swaps** the freshly-trained gate into the live runtime + re-scores the Today slate (via `feeds.install_gate`), so it takes effect without a server restart; the job result carries `hot_swapped` + `rescored`. The model-card handler (`model_card`/`_model_dir`/`_load_latest_runlog_entry`) lives in `services/model/model_card.py` and is **re-exported** here (layering: no api→api import); route registration is unchanged |
| `setup.py` | `/api/setup*` — first-run onboarding. `GET status` (config/LLM/paths/Zotero/classifier readiness + a `ready` gate); `GET detect-zotero` (read-only per-OS data-dir probe, db_exists first); `PUT paths` (write the allowlisted `PDF_ROOT`/`ZOTERO_DATA_DIR` to `.env`, byte-preserving; 422 on a non-existent or non-allowlisted key; `restart_required`); `POST validate-config` (dry-run GoalsConfig validation → `field_errors`, optional provider probe; persists **nothing**). Secrets never appear: `api_key_env` is a NAME, key presence is a BOOL. Logic in `services/setup/` (shared with the `zotero-summarizer setup` CLI) |
| `relabel_audit.py` | `/api/relabel-audit*` — test-retest reliability study |
| `results.py` · `corpus.py` · `config.py` · `health.py` | dashboard/corpus/config/health |
| `_golden_helpers.py` | pure (non-HTTP) helpers for `golden.py` |

**Boundaries:** import `services/` + `models`; never the reverse.
