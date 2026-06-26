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
```

| file | responsibility |
|---|---|
| `goldenset.py` | export the golden CSV/JSONL from Zotero engagement signals (atomic tmp+replace so a crash can't truncate it; labels/relevance from `domain`). A `label:<priority>` tag is the top short-circuit in `_infer_label`; `label:%` is an engagement trigger so a label-only item still gets a row. On export it reconciles `label_verdicts` from those tags (`user_labels.reconcile_label_verdicts`). Preserves **all** manual verdict keys on re-export via the uncapped `repositories.list_label_verdict_keys` (a capped fetch would silently drop verdicts); strips note HTML with the canonical `services._common.html_to_text` |
| `user_labels.py` | the `label:<priority>` tag bridge: `detect_label` (read → priority, highest wins) + `reconcile_label_verdicts` → `ReconcileCounts(synced, changed, removed)`: mirrors Zotero tags into `label_verdicts` (Zotero wins, idempotent) AND **retracts** a verdict whose tag was deleted — but only **tag-sourced** verdicts (`original_derived_priority == ZOTERO_LABEL_ORIGIN`) and only when the item is present, live and tag-free. Verdicts typed in the Annotate UI carry a derived original and are **never** auto-deleted (they can't be wiped for lacking a Zotero tag); missing/trashed/feed/note items are skipped |
| `hybrid_gt.py` | the single label-merge point, precedence ladder: explicit user verdict > outcome-corrected machine add > provisional machine add > derived. An **unchecked** "Add to library" verdict (`label_verdicts.source='machine_add'`) is capped at weak `could_read` (3.0) as the effective TRAINING label — not the `should_read` (4.0) the add stamps for display intent — because the user moved it Today→library but hasn't checked the label (`_UNCHECKED_ADD_PRIORITY`). It then gets corrected by the observed 7-day materialization outcome (`processed_feed_items.final_outcome`) — **demote-only** and computed from the could_read cap, not the raw add (`outcome_correction(_UNCHECKED_ADD_PRIORITY, outcome)` → `min` of the cap and `relevance_from_signal_weight(OUTCOME_WEIGHT)`; promotions flow through the engagement export, so promoting here would double-count). `pending`/`unknown`/unmapped outcomes are not behavioural evidence → no correction. Corrected rows get an `outcome_<name>` tier segment (weight: `label_weights`); relevance values from `domain.PRIORITY_TO_RELEVANCE`. Applied by every training path — `/admin/retrain`, the daemon gate retrain, and active-learning all thread `triage_db_path` into `load_or_train` |
| `label_provenance.py` | per-row provenance via `compute_provenance` / `provenance_from_row`: which signal produced which label |
| `feedback.py` | map emoji/engagement events to training signal tiers |
| `relabel_audit/` | blind test-retest reliability study (κ, ICC, …) |

**Boundaries:** standard services rules. `emoji_signals` (shared) is the tag
taxonomy this domain builds on.
