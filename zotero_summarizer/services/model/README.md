# services/model — the relevance gate (ML)

Predicts how relevant a paper is to you. A cheap classifier fast-rejects
obvious non-matches before any LLM call; the same model ranks your unread
library. Trains on `golden/`'s hybrid ground truth.

```
golden CSV ─featurize→ SPECTER2 embeds + library/prestige features
                              │
                          classifier (LightGBM regressor)  ── eval_baseline/ (CV + CIs)
                              │                              ── tune/ (Optuna)
            predict ──> composite score ──> reading_priority
            scoring = LLM dims + corpus affinity + prestige (+ surprise)
```

| file | responsibility |
|---|---|
| `classifier.py` | SPECTER2 classifier core: `cross_validate` + `predict_new_items` (re-exports the rest) |
| `classifier_const.py` | constants + result types (`ClassifierReport`/`FeedPrediction`) |
| `classifier_embed.py` · `classifier_features.py` · `classifier_fit.py` · `classifier_io.py` | embeddings · aux features · fit/calibration · CSV/metrics IO |
| `classifier_artifact.py` | the serialisable `TrainedClassifier` + SHAP attribution |
| `classifier_training.py` | `train_and_save` / `save_trained` (run pipeline → joblib + JSON twin) |
| `classifier_persistence.py` | on-disk location, load, lazy retrain; re-exports the artifact/training API |
| `llm_classifier.py` | LLM-as-classifier baseline (title+abstract → label); any OpenAI-compatible model, e.g. `--classifier-name llm_custom` |
| `scoring.py` · `prestige.py` · `surprise.py` | composite score; OpenAlex prestige; serendipity |
| `label_weights.py` | per-row training weights |
| `golden_metrics.py` | accuracy / per-class / confusion for eval |
| `library_features.py` | features conditioned on your positive-engagement set |
| `active_learning.py` | border-case picks that would most improve the model |
| `tune.py` | Optuna hyperparameter search |
| `eval_baseline/` | 5×5 CV baseline + learning curve with bootstrap CIs |

**Boundaries:** standard services rules. `scoring/prestige/surprise` are shared
primitives also used by `triage/`.
