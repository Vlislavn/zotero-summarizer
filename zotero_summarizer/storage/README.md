# storage — SQLite persistence

Owns the two local databases under `data/` and all SQL. Services call these
functions; nothing here reaches up into `services/` or `api/`.

```
services/ ─call→ storage/
   repositories.py  ──> data/triage_history.db   (triage results, jobs,
                         pending_changes, processed_feed_items, label_verdicts, …)
   corpus.py        ──> data/corpus_cache.db      (SPECTER2 embeddings, OpenAlex cache)
   feeds*.py        ──> processed_feed_items       (schema + decisions + lookups)
   migrations.py    : create/upgrade both DBs (creates data/ first)
```

| file | responsibility |
|---|---|
| `repositories.py` | triage-DB core: `DB_PATH` (set once at startup) + `with_db_path()` for a concurrency-safe per-context override, schema, connection hardening, shared query helpers (re-exports the `_repo_*` groups below) |
| `_repo_results.py` · `_repo_jobs.py` | batch/result rows + queries · triage-job upserts/listing |
| `_repo_pending.py` · `_repo_feedback.py` | pending-change queue · feedback signals |
| `_repo_verdicts.py` · `_repo_labels.py` | role-value + weekly-A/B verdicts · label verdicts (with `source` provenance: `user` vs `machine_add` — the provisional "Add to library" verdict that the training overlay may outcome-correct; UPSERT propagates it so a deliberate relabel flips a machine add back to `user`). `list_label_verdict_keys` (keys only — golden-CSV preservation) and `list_label_verdict_priorities` (`{item_key: user_priority}` — the reading-queue handled-filter needs the priority so `dont_read` hides but a positive label stays visible + pins to top) are both **uncapped** (a paged fetch silently drops rows once the table outgrows the cap) |
| `rows.py` | typed row models for the read boundary — `from_row` fails loud on schema drift, `to_dict` keeps the legacy contract. First adopter: `_repo_pending`. Add a model + route its reader to type more tables. |
| `corpus.py` | `EmbeddingCache` — embeddings/upserts + the math helpers; caches a normalized corpus matrix (version-invalidated on write) for the fast affinity path. The default `all-MiniLM-L6-v2` was shoot-out-validated and deliberately KEPT (2026-06-12, `tools/eval_goal_embedder.py` on 491 real kept/trashed decisions): goal_sim AUC 0.714 vs bge-m3's 0.712 (25× larger, MPS-OOM risk without a 512-token cap) and SPECTER2+proximity's 0.684 (paper-paper model, poor on short goal queries) — don't "upgrade" it without re-measuring |
| `corpus_read.py` · `corpus_types.py` | `EmbeddingCache` read/match methods (mixin): full `match_candidate` (UI) + `affinity_and_goals` (ONE candidate embed → engagement pos−neg affinity AND per-goal `{goal: cosine}` — the single computational definition of both per-candidate corpus signals) + `goal_affinity_for_items` (cached-item cosine to the research-goal embeddings) + `query_affinity_for_items` (cosine to an ad-hoc QUERY string — the dense leg of Library hybrid search); the item-side reads share one `_affinity_to_targets` matmul, no model load · shared value types |
| `corpus_bm25.py` | `CorpusBM25` — in-memory `rank_bm25` (Okapi) index over corpus title+abstract+tags; the LEXICAL leg of Library hybrid search. Rebuilt only on corpus change (count + `MAX(updated_at)`); process-level singleton (`get_corpus_bm25`); `texts_for` feeds the rerank stage. No DB migration. Also exports the public `tokenize` (lowercase alphanumeric words) — the single tokenizer reused by `faithbench._build_qa` and `library._paper_goal_summaries` |
| `feeds.py` | facade for `processed_feed_items`: schema + decision/materialization writes (re-exports below); stores `abstract` and `pub_year` from feed item at insert time; `update_scores` rewrites only the gate-derived fields by PK (slate re-score after a model upgrade) without touching the decision/read status |
| `feeds_history.py` | selection + outcome/history queries (re-exported by `feeds`) |
| `feeds_schema.py` · `feeds_constants.py` · `feeds_lookup.py` | schema / decision+outcome enums / single-row lookups + `fetch_processed_content_pairs` (raw `(doi, arxiv_id)` for content dedup — the same paper under a different GUID; callers normalize via `domain`) + `fetch_trashed_guids` (stable GUIDs of papers the user threw away — rows whose `decision`/`final_outcome` is in the caller-passed trashing taxonomies, e.g. `user_rejected` / `trashed` / `deleted_all`; the durable "never show again" key that survives feed-item-id reassignment and catches id-less items DOI/arXiv can't) + `fetch_resolved_outcomes` (`{feed_item_id: final_outcome}` for the caller-passed outcome taxonomy — feeds the training-label outcome correction in `services/golden/hybrid_gt`). Constants also own `BEHAVIORAL_OUTCOMES` (outcomes that are observed behaviour — `pending`/`unknown` are not) and `relevance_from_signal_weight` (the single weight→relevance map shared by the feedback emitter and the label correction) |
| `migrations.py` | `migrate_existing()` + `run_migrations()` — ordered, version-gated steps recorded in `schema_migrations`. Add a schema change as a new numbered `Migration`, never an inline ALTER. `repositories.apply_schema` is the v1 baseline. |

**Boundaries:** must NOT import `services/` or `api/` (enforced). Connection
hardening (WAL + busy_timeout=10s + 0600) is consistent across `_get_conn` and
`_connect_to` so every writer waits equally for a held lock.
