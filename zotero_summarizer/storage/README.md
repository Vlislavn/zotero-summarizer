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
| `repositories.py` | triage-DB core: `DB_PATH`, schema, connection hardening, shared query helpers (re-exports the `_repo_*` groups below) |
| `_repo_results.py` · `_repo_jobs.py` | batch/result rows + queries · triage-job upserts/listing |
| `_repo_pending.py` · `_repo_feedback.py` | pending-change queue · feedback signals |
| `_repo_verdicts.py` · `_repo_labels.py` | role-value + weekly-A/B verdicts · label verdicts |
| `corpus.py` | `EmbeddingCache` — embeddings/upserts + the math helpers |
| `corpus_read.py` · `corpus_types.py` | `EmbeddingCache` read/match methods (mixin) · shared value types |
| `feeds.py` | facade for `processed_feed_items`: schema + decision/materialization writes (re-exports below) |
| `feeds_history.py` | selection + outcome/history queries (re-exported by `feeds`) |
| `feeds_schema.py` · `feeds_constants.py` · `feeds_lookup.py` | schema / decision+outcome enums / single-row lookups |
| `migrations.py` | `migrate_existing()` — idempotent schema init/upgrade |

**Boundaries:** must NOT import `services/` or `api/` (enforced). Connection
hardening (WAL + busy_timeout + 0600) lives in `repositories._connect_to`.
