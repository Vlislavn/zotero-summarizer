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
| `repositories.py` | triage-DB core: `DB_PATH` (set once at startup) + `with_db_path()`/`TriageRepository` for a concurrency-safe per-context override, schema, connection hardening, shared query helpers (re-exports the `_repo_*` groups below) |
| `_repo_results.py` · `_repo_jobs.py` | batch/result rows + queries · triage-job upserts/listing |
| `_repo_pending.py` · `_repo_feedback.py` | pending-change queue · feedback signals |
| `_repo_verdicts.py` · `_repo_labels.py` | role-value + weekly-A/B verdicts · label verdicts |
| `rows.py` | typed row models for the read boundary — `from_row` fails loud on schema drift, `to_dict` keeps the legacy contract. First adopter: `_repo_pending`. Add a model + route its reader to type more tables. |
| `corpus.py` | `EmbeddingCache` — embeddings/upserts + the math helpers; caches a normalized corpus matrix (version-invalidated on write) for the fast affinity path |
| `corpus_read.py` · `corpus_types.py` | `EmbeddingCache` read/match methods (mixin): full `match_candidate` (UI) + `affinity_only` (engagement pos−neg, the gate's per-item feature) + `goal_affinity_for_items` (cosine to the research-goal embeddings) + `query_affinity_for_items` (cosine to an ad-hoc QUERY string — the dense leg of Library hybrid search); both share one `_affinity_to_targets` matmul, no model load on the item side · shared value types |
| `corpus_bm25.py` | `CorpusBM25` — in-memory `rank_bm25` (Okapi) index over corpus title+abstract+tags; the LEXICAL leg of Library hybrid search. Rebuilt only on corpus change (count + `MAX(updated_at)`); process-level singleton (`get_corpus_bm25`); `texts_for` feeds the rerank stage. No DB migration |
| `feeds.py` | facade for `processed_feed_items`: schema + decision/materialization writes (re-exports below); stores `abstract` and `pub_year` from feed item at insert time; `update_scores` rewrites only the gate-derived fields by PK (slate re-score after a model upgrade) without touching the decision/read status |
| `feeds_history.py` | selection + outcome/history queries (re-exported by `feeds`) |
| `feeds_schema.py` · `feeds_constants.py` · `feeds_lookup.py` | schema / decision+outcome enums / single-row lookups + `fetch_processed_content_pairs` (raw `(doi, arxiv_id)` for content dedup — the same paper under a different GUID; callers normalize via `domain`) + `fetch_trashed_guids` (stable GUIDs of papers the user threw away — rows whose `decision`/`final_outcome` is in the caller-passed trashing taxonomies, e.g. `user_rejected` / `trashed` / `deleted_all`; the durable "never show again" key that survives feed-item-id reassignment and catches id-less items DOI/arXiv can't) |
| `migrations.py` | `migrate_existing()` + `run_migrations()` — ordered, version-gated steps recorded in `schema_migrations`. Add a schema change as a new numbered `Migration`, never an inline ALTER. `repositories.apply_schema` is the v1 baseline. |

**Boundaries:** must NOT import `services/` or `api/` (enforced). Connection
hardening (WAL + busy_timeout=10s + 0600) is consistent across `_get_conn` and
`_connect_to` so every writer waits equally for a held lock.
