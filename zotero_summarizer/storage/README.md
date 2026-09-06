# storage — SQLite persistence

Owns the two local databases under `data/` and all SQL. Services call these
functions; nothing here reaches up into `services/` or `api/`.

```
services/ ─call→ storage/
   repositories.py  ──> data/triage_history.db   (triage results, jobs,
                         pending_changes, processed_feed_items, label_verdicts,
                         review_notes, sync_changes/mutations, …)
   corpus.py        ──> data/corpus_cache.db      (SPECTER2 embeddings, OpenAlex cache)
   feeds*.py        ──> processed_feed_items       (schema + decisions + lookups)
   migrations.py    : create/upgrade both DBs (creates data/ first)
```

| file | responsibility |
|---|---|
| `repositories.py` | triage-DB core: `DB_PATH` (set once at startup) + `with_db_path()` for a concurrency-safe per-context override, schema, connection hardening, shared query helpers (re-exports the `_repo_*` groups below) incl. the public `table_columns(conn, table)` PRAGMA reader the `_repo_*` modules and `integrations._zotero_write_tags` share |
| `_repo_results.py` · `_repo_jobs.py` | batch/result rows + queries · triage-job upserts/listing |
| `_repo_pending.py` · `_repo_feedback.py` | pending-change queue with source-aware status transitions (including retry `failed → applied`) and a per-paper open-sibling check for safe Inbox removal · feedback signals |
| `_repo_verdicts.py` · `_repo_labels.py` | role-value + weekly-A/B verdicts · label verdicts (with `source` provenance: `user` vs `machine_add` — the provisional "Add to library" verdict that the training overlay may outcome-correct; UPSERT propagates it so a deliberate relabel flips a machine add back to `user`). `list_label_verdict_keys` (keys only — golden-CSV preservation) and `list_label_verdict_priorities` (`{item_key: user_priority}` — the reading-queue handled-filter needs the priority so `dont_read` hides but a positive label stays visible + pins to top) are both **uncapped** (a paged fetch silently drops rows once the table outgrows the cap). Also owns the `review_notes` table (one editable free-text note per paper, jotted during a review — decoupled from a verdict): `upsert_review_note` / `get_review_note`, same UPSERT shape as `label_verdicts` |
| `_repo_sync.py` | v2 offline-sync persistence: triggers turn every verdict/note write (including delete tombstones) into a monotonic field revision; `BEGIN IMMEDIATE` applies typed UUID mutations idempotently and records applied/conflict/resolution results in `sync_mutations`. Replaying a stored conflict returns that same conflict, while a stored applied write returns `already_applied`. No service imports and no SQLite-file replication |
| `rows.py` | typed row models for the read boundary — `from_row` fails loud on schema drift, `to_dict` keeps the legacy contract. First adopter: `_repo_pending`. Add a model + route its reader to type more tables. |
| `corpus.py` | `EmbeddingCache` — embeddings/upserts + the math helpers; exports `open_corpus_conn(db_path)` (timeout + WAL pragma), the single corpus-DB opener `corpus_bm25` now shares so both readers of the same file run the same journal mode; caches a normalized corpus matrix (version-invalidated on write) for the fast affinity path. The default `all-MiniLM-L6-v2` was shoot-out-validated and deliberately KEPT (2026-06-12, `tools/eval_goal_embedder.py` on 491 real kept/trashed decisions): goal_sim AUC 0.714 vs bge-m3's 0.712 (25× larger, MPS-OOM risk without a 512-token cap) and SPECTER2+proximity's 0.684 (paper-paper model, poor on short goal queries) — don't "upgrade" it without re-measuring |
| `corpus_read.py` · `corpus_types.py` | `EmbeddingCache` read/match methods (mixin): full `match_candidate` (UI) + `affinity_and_goals` (ONE candidate embed → engagement pos−neg affinity AND per-goal `{goal: cosine}` — the single computational definition of both per-candidate corpus signals) + `goal_affinity_for_items` (cached-item cosine to the research-goal embeddings) + `query_affinity_for_items` (cosine to an ad-hoc QUERY string — the dense leg of Library hybrid search); the item-side reads share one `_affinity_to_targets` matmul, no model load. That matmul looks items up in a PROCESS-WIDE normalized-embedding matrix (`_normalized_corpus_matrix`) cached by a `_corpus_fingerprint` (main + `-wal` mtime/size, so a WAL-resident write still invalidates it) — the reading queue builds a fresh `EmbeddingCache` per open, so the instance `_affinity_cache` can't help there; without this every open re-parsed ~2k embeddings from JSON (~0.5s) · shared value types |
| `corpus_bm25.py` | `CorpusBM25` — in-memory `rank_bm25` (Okapi) index over corpus title+abstract+tags; the LEXICAL leg of Library hybrid search. Reuses the main/WAL fingerprint from `corpus_read`, so same-timestamp updates invalidate the index; process-level singleton (`get_corpus_bm25`); `texts_for` feeds the rerank stage. No DB migration. Also exports the public `tokenize` (lowercase alphanumeric words) — the single tokenizer reused by `faithbench._build_qa` and `library._paper_goal_summaries` |
| `feeds.py` | facade for `processed_feed_items` decision/materialization writes (re-exports below); stores `abstract` and `pub_year` from feed item at insert time; `update_scores` rewrites only the gate-derived fields by PK without touching decision/read status |
| `feeds_history.py` | selection + outcome/history queries (re-exported by `feeds`). `reserve_materialization_key` atomically commits the existing/materialized or proposed key before external Zotero writes. `select_by_decisions`'s `since_hours` accepts `None` to disable the time window entirely (returns rows of any age) — used by `apply_all_approved` so a `user_approved` row (an explicit user instruction) can never age out of reach; every other caller keeps its windowed default |
| `feeds_schema.py` · `feeds_constants.py` · `feeds_lookup.py` | schema init/migrations + the shared `open_triage_conn` / decision+outcome enums / single-row lookups, including `get_processed_feed_item_by_stable_key` for the Zotero-optional app library reader + `fetch_processed_content_pairs` (raw `(doi, arxiv_id)` for content dedup — the same paper under a different GUID; callers normalize via `domain`) + `fetch_trashed_guids` (stable GUIDs of papers the user threw away — rows whose `decision`/`final_outcome` is in the caller-passed trashing taxonomies, e.g. `user_rejected` / `trashed` / `deleted_all`; the durable "never show again" key that survives feed-item-id reassignment and catches id-less items DOI/arXiv can't) + `fetch_resolved_outcomes_by_key` (`{stable_feed_key|legacy_key: final_outcome}` for the caller-passed outcome taxonomy — feeds the training-label outcome correction in `services/golden/hybrid_gt`). Constants also own `BEHAVIORAL_OUTCOMES` and `relevance_from_signal_weight` |
| `interleave.py` | `interleave_log` sidecar for the P3 quality-first online experiment: per-day, per-slot arm attribution (`team` ∈ `a0`/`a2`/`both` + competitive `pair_id`) written by the Today slate when `ZS_RANK_INTERLEAVE` is on. `record_interleave_slate` is DAY-level write-once (`BEGIN IMMEDIATE` existence check → first recorded slate of a day is the ONLY write; later same-day assemblies are complete no-ops) — row-level `INSERT OR IGNORE` alone would let intra-day pool drift mix two merges' per-merge `pair_id`s into one corrupt `(day, pair_id)` group and crash the scorer (adversarial review 2026-07-08). Attribution therefore never changes after the user may have seen the slate. `fetch_interleave_log` opens read-only and returns `[]` only when the table doesn't exist yet (experiment never ran — explicit `sqlite_master` check, not a swallowed error). Table is self-creating (sidecar, additive; no `migrations.py` step needed). Sole consumer: `tools/eval_interleave.py` |
| `migrations.py` | `migrate_existing()` + `run_migrations()` — ordered, version-gated steps recorded in `schema_migrations`. Add a schema change as a new numbered `Migration`, never an inline ALTER. v3 reconciles databases whose old v1 marker predates later baseline/feed columns; routine feed connections skip those O(N) legacy backfills. |

**Boundaries:** must NOT import `services/` or `api/` (enforced). Connection
hardening (WAL once per process/path + busy_timeout=10s + 0600) is shared by
`_get_conn` and `_connect_to`, avoiding concurrent WAL-mode lock races.

Startup job reconciliation converts `running` to `interrupted`, but `cancelling`
to `cancelled`, so restart does not resume an acknowledged cancellation request.
This uses the existing status column, not a schema change.

`insert_result` commits the result and optional planned pending changes in one
transaction, returning the queued count. Standalone queue writers reuse the
same private connection-taking insert; SQL/serialization failures roll back and
propagate, and nonempty plans with missing item keys/change types raise rather
than silently losing rows. Unused batch/ranking write overrides were removed;
historical columns and readers remain unchanged. Job progress is still a
separate snapshot, so this is not crash-safe exactly-once job execution.

`insert_feedback_events` applies each batch in input order within one transaction.
An explicit approve/reject replaces its opposite for that item; implicit signals
and other items are untouched. Concurrent writers serialize through SQLite, and
any failed insert rolls the whole batch back, including prior verdict removal.
Required empty fields reject the batch instead of skipping rows; invalid supplied
relevance raises, while numeric zero remains zero. The separate public
`delete_feedback_signals` entry point was removed. No schema change is needed.

`get_latest_explicit_feedback()` reads one uncapped snapshot with only item ID,
signal, saved `original_priority` and UTC age in days. It does not join current
triage results: re-triage, later overrides and result deletion cannot change the
prediction recorded with a decision. The old `*_with_results(days)` API and its
unused result fields are removed. SQLite computes all ages against the same
second-resolution clock; the reporting service selects its periods from that
one snapshot. Latest explicit decision per item remains the cohort, not the
complete interaction-event history or a random gate-audit sample.

`record_outcome` stages a first materialized-row resolution and its `user_feedback`
signal on the same caller-owned connection; both commit or roll back together.
The private feedback insert is shared with `insert_feedback_events`, so this
path cannot accidentally use another project's repository default connection.
Already-resolved/missing/unmaterialized rows return false without writing a signal.
Outcome type and relevance come from the known outcome taxonomy/weight mapping;
the independent weight override and service mapping-wrapper exports are gone.

RSS item reads accept `limit=None` for an uncapped whole-feed pass (`LIMIT -1`);
finite callers keep their bounded result contract. Feed-tick exclusion uses a
separate empty SQLite lock file beside the Settings-provided triage store, not
a long-lived transaction on application tables.

`select_by_decisions` orders before `LIMIT`: score-descending by default,
`recent` by creation time, or `border` by distance to the domain's priority
thresholds (unscored last). Review ties use creation time then row ID descending;
unknown sort names raise. Feed/time/decision filters share one SQL query.
`limit=None` disables the row cap for Apply-all; numeric limits retain the
existing 1–5000 bounds and other callers retain their defaults.

`upsert_label_verdict(conn, ...)` validates and writes within a caller-owned
transaction, returning the row ID and previous snapshot for transition logging.
The existing path-based `insert_or_update_label_verdict` delegates to it and
commits; feed review uses it in the same transaction as its terminal decision.
Migration v7 adds nullable `label_verdicts.training_sample_json`: Review stores
its metadata on that same connection, ordinary relabels retain it, and deletion
removes it with the verdict. No separate sample table, journal or worker exists.
`list_all_label_verdicts(include_training=True)` supplies labels and metadata in
one SQL snapshot to hybrid training; the default HTTP/transfer projection stays
unchanged. The corpus namespace advances through a no-op v7 for version parity.

Corpus and goal vectors carry `encoder_id` (SentenceTransformer backend, installed
package version and configured model name); item content hashes include it too.
Migration v4 adds nullable provenance without deleting library metadata. Legacy
rows remain unverified until re-embedded. Every vector reader rejects unknown or
different identities with a resync error, including both warm matrix caches;
metadata/BM25 reads remain usable. Reimport recomputes unchanged text when its
encoder changes. Research goals must also be re-embedded before vector matching.
Both matrix caches include encoder identity and main/WAL fingerprints, observing
other instances' writes; the redundant instance-only version counter is gone.
The model name identifies the configured artifact, not a content digest of a
mutable local model directory or an unpinned upstream revision.

Encoder loading/encoding and malformed vector/SQLite errors propagate; there is
no hash-vector fallback, test-environment branch or guessed embedding dimension.
Failed batches roll back, and a failed load is retryable on the next call. Corpus
construction runs numbered migrations (also outside setup); the migration runner
locks and rereads the version before each step; v4's DDL and marker commit in one
transaction. Test encoders are supplied by pytest, not production code.

Migration v6 adds corpus DOI metadata without re-embedding or removing rows.
Zotero refresh and corpus imports preserve it; metadata-only edits reuse the
stored vector through the same UPSERT as text changes (the duplicate UPDATE
branch is removed). DOI updates participate in cache fingerprints and the
existing authoritative-row training identity.
Fast affinity and full matching share candidate exclusion: equal normalized
DOIs identify a paper; if either DOI is missing, normalized title is used.
Distinct known DOIs override coincident titles. Exclusion is request-local,
removing the candidate from positive/negative averages, collection suggestions
and similar-item lists without changing goal similarity or stored rows.
Legacy rows gain DOI on normal refresh and use title matching meanwhile.
`corpus_affinity(rows)` freezes corpus weights, candidate similarities and
DOI/title matches once per training/evaluation run. `CorpusAffinity.scores`
uses only corpus papers represented in the supplied training indices, excludes
all held-out matches (also when a train alias matches), and always removes the
candidate itself. Unassigned corpus rows cannot enter a fold. Without indices,
final fitting uses the whole current corpus minus self-matches, as inference does.
The snapshot has no model or connection; repeated folds/trials do not re-embed,
and subsetting selects candidate rows without changing the reference corpus.
Scalar fast scoring and fold scoring share the positive-minus-negative weighted
mean. Empty reference sets contribute zero. This is current-snapshot evaluation,
not reconstruction of historical engagement edits on the older training papers.

Untouched stale items carry a **negative** engagement weight (-0.3). The shared
weight function feeds metadata, scalar matching and vectorized affinity; explicit
rejection still wins over positive signals, otherwise the strongest positive
signal wins. Weights are relative within each positive/negative cosine average,
not an absolute multiplier on the final score. Recent/undated unengaged items
remain neutral.

BM25 builds the replacement index before publishing its keys and fingerprint;
failed builds propagate and remain retryable rather than validating an old index
under new metadata. The declared `rank-bm25` dependency is required, and malformed
tag JSON raises. Empty corpora/vocabularies return no lexical matches. As with the
vector caches, DB-wide fingerprints may rebuild on unrelated lookup-cache writes;
no separate version query, table-revision schema or error fallback is added.

Corpus reconciliation reads all cached keys via `list_item_ids` (no metadata
page cap or encoder compatibility requirement). `upsert_items` accepts explicitly
confirmed `missing_item_ids`: targeted deletes and upserts share one transaction,
and encoder/SQL failures roll the batch back. No delete-all/rebuild is needed for
routine sync; goals, model-input embeddings and historical feedback are untouched.

`label_mirrors.py` reuses `sync_changes` as label intent. Migration v5 adds only
`label_mirror_receipts`, keyed by the acknowledged deletion revision. `current_label`
holds `BEGIN IMMEDIATE` over mirror delivery and records a receipt only after a
successful exit with a resolved target; failures and unmaterialized feed keys leave
the already-committed deletion retryable. Feed,
legacy and materialized library identities resolve to their newest shared revision.
`states` supplies an uncapped snapshot per Zotero target, including pending deletion.
Reconciliation UPSERTs and deletes pass `expected_revision`; the existing label
writer checks that revision under its writer lock and returns `None`/`False` on
a concurrent change. Pre-sync verdicts with no revision retain revision zero.
Migration v5 also versions changes to `original_derived_priority`: transferring
ownership from a Zotero-derived label to an app verdict must invalidate a stale
automatic retraction even when its category/comment/source remain the same.
Offline verdict UPSERTs also replace that origin with the submitted reading priority
(or `unknown` for non-priority values, including a cached `zotero_label` marker),
instead of retaining Zotero ownership of an app decision.
No new queue/worker or reviewed-pending-change behavior is involved.

`list_all_label_verdicts` is the sole full-row verdict-list reader: newest first,
uncapped, shared by HTTP, training and the one-time Zotero transfer. The duplicate
`list_label_verdicts` API and its implicit 500/5000-row caps were removed. Key-only
and priority-only readers retain their narrower projections.

`review_notes.py` resolves a note family through stored/unambiguous legacy aliases
and materialized library keys. Online reads/writes and offline canonical values,
conflict revisions and snapshots share the latest note revision, including deletion
tombstones. Pre-sync notes use timestamp order at revision zero. Writes update the
current winning row under the caller's writer transaction; materialization needs
no body copy or migration, and reads do not rewrite historical data. Ambiguous
legacy IDs are not guessed. This changes local note identity, not Zotero mirror
delivery. Snapshot identity resolution is per note family, not one batched join.
