# services/model/eval_baseline — measure the gate honestly

Phase 1.16 measurement framework: how good is the model, and what's the
ceiling? Repeated stratified CV with bias-corrected bootstrap confidence
intervals, plus a learning curve. Pure measurement — no training side effects.

```
golden rows ─_featurize→ X,y ─_runners→ 5×5 StratifiedGroupKFold ─_metrics→ per-fold
                                  └─ _bootstrap (BCa CIs) ─> BaselineReport
                                  └─ learning curve (per fraction)
                       _serialize ─> JSON (data/eval-baseline-*.json)
```

| file | responsibility |
|---|---|
| `_runners.py` | `run_baseline` / `run_learning_curve` — the CV loops |
| `_featurize.py` | turn the golden CSV into the feature matrix — threads the cold-start author-prior policy from `_build_aux_providers` through `_compute_aux` so eval features match production scoring; golden-CSV read reuses `services._common.load_golden_rows` (de-duped) |
| `_metrics.py` | per-fold metrics (Spearman, AUC, NDCG, MAE, κ, …); priority bins come from `domain` (same as derivation/prediction) |
| `_bootstrap.py` | BCa bootstrap confidence intervals |
| `_serialize.py` | report ↔ JSON round-trip |
| `__init__.py` | public surface (`run_baseline`, report types) |

Each fold fit rebuilds corpus affinity and five positive-library columns using
`model.library_features.recompute_engagement_columns`, also used by production
training, temporal evaluation and tuning. The candidate's whole paper group is
excluded from vectors/centroids/authors; held-out engagement never enters P.
The private matrix-override argument and baseline-specific rebuild are removed.
The featurizer carries one corpus similarity/identity snapshot; folds admit
only train-matched corpus papers and exclude held-out matches and the candidate.
No encoder runs inside folds or Optuna trials. Learning-curve subsetting keeps
weights, rows, targets and corpus candidates aligned through the same indices;
its separate caller-supplied row list was removed. It previously dropped sample
weights, even though baseline and deployed fitting used them.
