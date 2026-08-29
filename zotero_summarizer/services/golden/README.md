# services/golden — labels & ground truth

Owns the training dataset and the "manual label always wins" rule. The golden
CSV is derived from your Zotero engagement (emoji tags, notes, collections);
your explicit verdicts overlay on top via hybrid ground truth.

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
