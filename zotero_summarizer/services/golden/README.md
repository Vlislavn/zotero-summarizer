# services/golden — labels & ground truth

Owns the training dataset and the "manual label always wins" rule. The golden
CSV is derived from your Zotero engagement (emoji tags, notes, collections);
your explicit verdicts overlay on top via hybrid ground truth.

Feed Review now commits training metadata with its SQLite verdict, not through
a pre-commit CSV append. Hybrid training unions these samples with CSV rows and
deduplicates stored feed aliases; existing CSV metadata wins on overlap. Labels
and their sample metadata are read in one SQL snapshot. Ordinary relabels retain
the metadata; verdict deletion removes it. Export adds DB-only samples to both
CSV and JSONL without duplicating existing rows. Existing retraction markers
still exclude historical exported verdict rows from effective training.

Your **explicit** verdict lives in Zotero as a `label:<priority>` tag (the
ground truth — set in the app or directly in Zotero; Zotero reconciles). It is
the **top-precedence** signal: present on a library item it beats trash and all
emoji scoring in `goldenset._infer_label`. Where you haven't set one, derivation
behaves exactly as before.

```
Zotero engagement ─goldenset.export→ data/zotero-summarizer-golden.csv
your manual verdicts ───────────────┐
7-day materialization outcomes ─────┤   (corrects provisional "Add" labels)
                                     ▼
                          hybrid_gt.apply  (user > outcome > machine add > derived)  ──> model/ training
label_provenance: per-row "why this label?"   relabel_audit/: is labeling reliable?
label_verdicts: current snapshot + append-only user-label transition event
```

| file | responsibility |
|---|---|
| `label_verdicts.py` | the concrete current-label command seam used by deliberate writers: read prior snapshot → commit `label_verdicts` → append `label_transition` with distinct prior-user/new-user/model fields. Offline sync commits atomically in its storage transaction, then calls the same `log_committed_transition` semantic seam. Same-label user saves are transition no-ops; user labels cannot be overwritten by `machine_add`; an automated row taken over by a user becomes an assignment. The JSONL append is best-effort after commit (not a transactional outbox) |
| `verdict_effects.py` | idempotent post-commit effects shared by online and offline verdicts: metadata-enriched golden row, positive-feed materialization, real-key `label:*`/comment mirrors, plus the shared review-note mirror. Sync replays these on `already_applied` to repair a process crash between SQLite commit and the effect; failures stay soft because current state is already durable |
| `goldenset.py` | export the golden CSV/JSONL from Zotero engagement signals (atomic tmp+replace so a crash can't truncate it; labels/relevance from `domain`). A `label:<priority>` tag is the top short-circuit in `_infer_label`; `label:%` is an engagement trigger so a label-only item still gets a row. On export it reconciles `label_verdicts` from those tags (`user_labels.reconcile_label_verdicts`). Preserves **all** manual verdict keys on re-export via the uncapped `repositories.list_label_verdict_keys` (a capped fetch would silently drop verdicts); strips note HTML with the canonical `services._common.html_to_text` |
| `user_labels.py` | the `label:<priority>` tag bridge: `detect_label` (read → priority, highest wins) + `reconcile_label_verdicts` → `ReconcileCounts(synced, changed, removed)`: mirrors Zotero tags into `label_verdicts` (Zotero wins, idempotent), emits first-observed/change/retraction transitions, and **retracts** a verdict whose tag was deleted — but only **tag-sourced** verdicts (`original_derived_priority == ZOTERO_LABEL_ORIGIN`) and only when the item is present, live and tag-free. A first observation on a device is marked with unknown earlier history; no old label is guessed. Verdicts typed in the Annotate UI carry a derived original and are **never** auto-deleted unless a later mismatched Zotero label takes ownership; missing/trashed/feed/note items are skipped |
| `hybrid_gt.py` | the single label-merge point, precedence ladder: explicit user verdict > outcome-corrected machine add > provisional machine add > derived. An **unchecked** "Add to library" verdict (`label_verdicts.source='machine_add'`) is capped at weak `could_read` (3.0) as the effective TRAINING label — not the `should_read` (4.0) the add stamps for display intent — because the user moved it Today→library but hasn't checked the label (`_UNCHECKED_ADD_PRIORITY`). It then gets corrected by the observed 7-day materialization outcome (`processed_feed_items.final_outcome`) — **demote-only** and computed from the could_read cap, not the raw add (`outcome_correction(_UNCHECKED_ADD_PRIORITY, outcome)` → `min` of the cap and `relevance_from_signal_weight(OUTCOME_WEIGHT)`; promotions flow through the engagement export, so promoting here would double-count). `pending`/`unknown`/unmapped outcomes are not behavioural evidence → no correction. Corrected rows get an `outcome_<name>` tier segment (weight: `label_weights`); relevance values from `domain.PRIORITY_TO_RELEVANCE`. Applied by every training path — `/admin/retrain`, the daemon gate retrain, and active-learning all thread `triage_db_path` into `load_or_train` |
| `label_provenance.py` | per-row provenance via `compute_provenance` / `provenance_from_row`: which signal produced which label |
| `feedback.py` | map emoji/engagement events to training signal tiers |
| `relabel_audit/` | blind test-retest reliability study (κ, ICC, …) |

**Boundaries:** standard services rules. `emoji_signals` (shared) is the tag
taxonomy this domain builds on.

`csv_store.edit_csv` is the shared golden CSV read-modify-write boundary for
review appends, export, and ML/LLM prediction columns. A per-path SQLite lock
sidecar serializes threads and processes (10s busy timeout); the existing atomic
writer publishes one complete snapshot. No-op appends do not rewrite the file;
malformed headers/rows and failed writes raise, leaving the original intact.
The sidecar stays beside the Settings-resolved CSV; SQLite releases locks on exit.

`csv_store.read_snapshot` supplies parsed rows and the full SHA-256 of the same
read to classification, training and the shared row loader. It never rereads the
file for provenance. Atomic replacement by app writers preserves a complete read
snapshot; this is not a transaction across CSV, verdict SQLite and config, nor
a lock against external in-place writers. File hashes describe bytes, not model
reuse: training owns the projection of effective fields used for its identity.

Empty exports preserve namespaced and explicit-verdict rows just like non-empty
exports; a new empty dataset has a header. Preserved samples are validated before
replacement. JSONL and reported counts use the complete merged export snapshot,
not just fresh Zotero rows. JSONL is an export snapshot, not a live mirror of later
CSV edits; the two files are individually atomic, not a cross-file transaction.

Hybrid loading honors existing `sync_changes` deletion markers for HTTP,
reconciliation and offline verdict retractions. A retracted `feed_user_label`,
`user_label` or `feed_interest` CSV snapshot (including outcome suffixes and
legacy feed aliases) is excluded from effective labels/training, not relabeled
as derived evidence. The CSV remains intact for metadata and later reassignment.
Independent engagement rows revert to their derived label; standalone imported
labels without a retraction remain valid. Older stores without sync history
retain their prior behavior; SQL errors propagate.

Online/offline deletion calls `verdict_effects.mirror_current_verdict` after the
local commit. A missing Zotero configuration leaves a durable, unacknowledged
deletion; connector/backup/write failures propagate. Repeated DELETE and applied
offline UUID replay retry it, reading current state under SQLite's writer lock
instead of replaying an obsolete value. Already-acknowledged deletion is a no-op.
The reconciliation entry runs the numbered triage migrations, including for a
standalone CLI export that has not passed through application startup.
Reconciliation retries pending removals only through the configured reader/writer
for the same Zotero file, with Zotero closed; a read-only standalone export never
constructs a writer. It suppresses pending removals and verifies live tags before
importing a label, so a stale export is not an assignment. Observed tag absence
also acknowledges a pending retraction; later direct Zotero labels remain valid.
Feed/legacy/library aliases share the latest mirror revision. Conditional UPSERT
prevents a concurrent retraction from being overwritten after snapshot collection.
Tag-removal reconciliation also conditionally deletes its observed revision, so
a concurrent user relabel survives. Set effects share the same current-state
mirror; stale set retries cannot re-add a removed label. A newly materialized
target is revalidated even if an earlier deletion receipt already exists, since
an in-flight creation may have stamped an obsolete label. Revalidation performs
no Zotero write when the tag already matches. CSV/comment enrichment retains its
separate existing post-commit behavior.

When a verdict command receives no explicit original priority, it inherits only
a recognized prior reading priority, otherwise storing `unknown`. A prior
`zotero_label` origin is not inherited by a deliberate app decision, so confirming
the same category transfers ownership and advances the existing revision trigger.
Zotero reconciliation still supplies its origin explicitly and remains tag-owned.

Label provenance uses the same `detect_label` and domain relevance mapping as
export: explicit tags win before trash/veto/engagement. CSV `user_label` rows
reconstruct the tag from `gold_priority_inferred`, never the editable final value;
missing/invalid originals raise. The existing short-circuit trace exposes
`explicit_label`, so the UI explains the label instead of showing additive math.
The shared short-circuit constructor also handles trash/veto; unlabeled scoring
and final-label manual-override detection retain their existing semantics.
