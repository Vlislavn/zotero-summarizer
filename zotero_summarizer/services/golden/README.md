# services/golden — labels & ground truth

Owns the training dataset and the "manual label always wins" rule. The golden
CSV is derived from your Zotero engagement (emoji tags, notes, collections);
your explicit verdicts overlay on top via hybrid ground truth.

```
Zotero engagement ─goldenset.export→ data/zotero-summarizer-golden.csv
your manual verdicts ───────────────┐
                                     ▼
                          hybrid_gt.apply  (manual overrides derived)  ──> model/ training
label_provenance: per-row "why this label?"   relabel_audit/: is labeling reliable?
```

| file | responsibility |
|---|---|
| `goldenset.py` | export the golden CSV/JSONL from Zotero engagement signals (atomic tmp+replace so a crash can't truncate it; labels/relevance from `domain`). Preserves **all** manual verdict keys on re-export via the uncapped `repositories.list_label_verdict_keys` (a capped fetch would silently drop verdicts); strips note HTML with the canonical `services._common.html_to_text` |
| `hybrid_gt.py` | overlay manual verdicts on derived labels (manual wins); relevance values from `domain.PRIORITY_TO_RELEVANCE` |
| `label_provenance.py` | per-row provenance via `compute_provenance` / `provenance_from_row`: which signal produced which label |
| `feedback.py` | map emoji/engagement events to training signal tiers |
| `relabel_audit/` | blind test-retest reliability study (κ, ICC, …) |

**Boundaries:** standard services rules. `emoji_signals` (shared) is the tag
taxonomy this domain builds on.
