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
                                     ▼
                          hybrid_gt.apply  (manual overrides derived)  ──> model/ training
label_provenance: per-row "why this label?"   relabel_audit/: is labeling reliable?
```

| file | responsibility |
|---|---|
| `goldenset.py` | export the golden CSV/JSONL from Zotero engagement signals (atomic tmp+replace so a crash can't truncate it; labels/relevance from `domain`). A `label:<priority>` tag is the top short-circuit in `_infer_label`; `label:%` is an engagement trigger so a label-only item still gets a row. On export it reconciles `label_verdicts` from those tags (`user_labels.reconcile_label_verdicts`). Preserves **all** manual verdict keys on re-export via the uncapped `repositories.list_label_verdict_keys` (a capped fetch would silently drop verdicts); strips note HTML with the canonical `services._common.html_to_text` |
| `user_labels.py` | the `label:<priority>` tag bridge: `detect_label` (read → priority, highest wins) + `reconcile_label_verdicts` → `ReconcileCounts(synced, changed, removed)`: mirrors Zotero tags into `label_verdicts` (Zotero wins, idempotent) AND **retracts** a verdict whose tag was deleted — safely (only when the item is present, live and tag-free; never on a missing/trashed/feed/note item) |
| `hybrid_gt.py` | overlay manual verdicts on derived labels (manual wins); relevance values from `domain.PRIORITY_TO_RELEVANCE` |
| `label_provenance.py` | per-row provenance via `compute_provenance` / `provenance_from_row`: which signal produced which label |
| `feedback.py` | map emoji/engagement events to training signal tiers |
| `relabel_audit/` | blind test-retest reliability study (κ, ICC, …) |

**Boundaries:** standard services rules. `emoji_signals` (shared) is the tag
taxonomy this domain builds on.
