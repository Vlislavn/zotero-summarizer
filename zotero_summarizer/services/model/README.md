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
| `classifier_embed.py` · `classifier_features.py` · `classifier_fit.py` · `classifier_io.py` | embeddings (device-aware MPS/CUDA/CPU; `compute_embeddings_batch` / `get_or_compute_embeddings_batch` encode many papers per forward pass — the gate's throughput primitive; `compute_embedding` is a 1-item shim) · aux features · fit/calibration (TabPFN runs on CPU — `classifier_const.TABPFN_DEVICE`: it re-fits in-context per predict over a tiny ~500-row context, so it gains ~nothing from the GPU but, as a third claimant on the encoder+reranker-saturated MPS pool, OOM'd mid-scoring there) · CSV/metrics IO |
| `classifier_artifact.py` | the serialisable `TrainedClassifier` + SHAP attribution; `predict` batches embeddings AND computes per-item aux (corpus affinity + OpenAlex prestige) concurrently on a bounded thread pool (overlaps network I/O). `predict(prestige_network=False)` makes the prestige lookup cache-only — interactive scoring (`reading_queue.live_scoring`, the "why this score?" detail) never blocks on a network search |
| `classifier_training.py` | `train_and_save` / `save_trained` (run pipeline → joblib + JSON twin, atomic). Computes OOF per-class precision/recall/F1 + confusion (`oof_metrics_vs_gold`, via `golden_metrics`, on the post-calibration bins) and, when `runs_log_path` is given, appends a FAIR run-log entry so the Settings ModelCard renders them |
| `band_calibration.py` | OOF monotone (isotonic) `raw→relevance` map applied to the 4-class BAND only — makes the compressed top reachable (`must_read` recall collapses otherwise) WITHOUT touching the scores used for ranking/gate-composite. Self-gated: kept only if it lifts OOF must+should macro-F1 (else identity), so it can't regress the banding or invent false must_reads when great papers are scarce. Stored in `TrainedClassifier.calibrator` (None ⇒ identity, backward-compatible) |
| `classifier_persistence.py` | on-disk location, load, lazy retrain; re-exports the artifact/training API |
| `llm_classifier.py` | LLM-as-classifier baseline (title+abstract → label); any OpenAI-compatible model, e.g. `--classifier-name llm_custom` |
| `scoring.py` · `prestige.py` · `surprise.py` | composite score; OpenAlex prestige (`percentile_to_score`: field+year-normalized `citation_normalized_percentile` → [1,5]; cold-start/uncited → neutral 3.0, never floored). **Cold-start author prior** (`cold_start_author_score`, gated by `ColdStartPrestigePolicy` built from the prestige config): when a paper has no percentile yet, lift from the authors' *field-normalized* standing (`OpenAlexWork.max_author_field_percentile`, NOT raw h-index — Leiden #6) — asymmetric (raise-only), capped (`p**gamma`), threaded to BOTH train + predict so the `prestige_score` feature stays consistent; serendipity |
| `reranker.py` | local cross-encoder `Reranker` (sentence-transformers `CrossEncoder`, default `BAAI/bge-reranker-v2-m3`) — the coherence-rerank stage of Library hybrid search. Lazy load with BACKGROUND warmup so the first search never blocks on the model download (serves fusion order meanwhile); thread-locked inference; process-level singleton (`get_reranker`) |
| `label_weights.py` | per-row training weights by signal tier. Explicit `label:<priority>` verdicts (tier `user_label`) weigh at the top (1.0) — your deliberate, decay-immune ground truth, no longer the 0.7 medium fall-through |
| `golden_metrics.py` | accuracy / per-class / confusion for eval |
| `library_features.py` | features conditioned on your positive-engagement set |
| `active_learning.py` | border-case picks that would most improve the model. Disagreement is judged against your EFFECTIVE label (`label:*`-aware via `hybrid_gt.load_hybrid_labels`) — "the gate disagrees with what *you* decided", not a noisy derived guess; `has_label` flags rows where the truth is your explicit verdict. Pure `_ground_truth` resolver keeps it unit-testable |
| `tune.py` | Optuna hyperparameter search |
| `eval_baseline/` | 5×5 CV baseline + learning curve with bootstrap CIs |

**Boundaries:** standard services rules. `scoring/prestige/surprise` are shared
primitives also used by `triage/`.
