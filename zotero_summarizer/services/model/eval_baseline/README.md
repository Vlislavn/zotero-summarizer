# services/model/eval_baseline — measure the gate honestly

Phase 1.16 measurement framework: how good is the model, and what's the
ceiling? Repeated stratified CV with bias-corrected bootstrap confidence
intervals, plus a learning curve. Pure measurement — no training side effects.

```
golden rows ─_featurize→ X,y ─_runners→ 5×5 StratifiedKFold ─_metrics→ per-fold
                                  └─ _bootstrap (BCa CIs) ─> BaselineReport
                                  └─ learning curve (per fraction)
                       _serialize ─> JSON (data/eval-baseline-*.json)
```

| file | responsibility |
|---|---|
| `_runners.py` | `run_baseline` / `run_learning_curve` — the CV loops |
| `_featurize.py` | turn the golden CSV into the feature matrix |
| `_metrics.py` | per-fold metrics (Spearman, AUC, NDCG, MAE, κ, …) |
| `_bootstrap.py` | BCa bootstrap confidence intervals |
| `_serialize.py` | report ↔ JSON round-trip |
| `__init__.py` | public surface (`run_baseline`, report types) |
