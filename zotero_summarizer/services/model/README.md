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
| `classifier_embed.py` · `classifier_features.py` · `classifier_fit.py` · `classifier_io.py` | embeddings (device-aware MPS/CUDA/CPU; `compute_embeddings_batch` / `get_or_compute_embeddings_batch` encode many papers per forward pass — the gate's throughput primitive; `compute_embedding` is a 1-item shim; **thread-safe lazy load + thread-locked inference** — `_LOAD_LOCK`/`_PREDICT_LOCK`, since transformers' meta-device weight init isn't thread-safe and the retrain thread races the live scoring thread; mirrors `reranker.py`) · aux features (`max_author_h_index=None` when OpenAlex cannot resolve an author; zero is not evidence) · fit/calibration (TabPFN runs on CPU — `classifier_const.TABPFN_DEVICE`: it re-fits in-context per predict over a tiny ~500-row context, so it gains ~nothing from the GPU but, as a third claimant on the encoder+reranker-saturated MPS pool, OOM'd mid-scoring there) · CSV/metrics IO |
| `classifier_artifact.py` | the serialisable `TrainedClassifier` + SHAP attribution; `predict` batches embeddings AND computes per-item aux (corpus affinity + OpenAlex prestige) concurrently on a bounded thread pool (overlaps network I/O). Before the title+abstract filter it runs `_backfill_abstracts`: title-only RSS items (the gate needs both fields) get their abstract recovered from OpenAlex — already queried here for prestige, so it rides the SAME cached payload (`OpenAlexWork.abstract`, reconstructed from `abstract_inverted_index`) at near-zero extra cost. Looked up by DOI then title; cache-only/offline → no-op. The recovered abstract flows back on the item dict and persists to `processed_feed_items.abstract`, so it scores AND shows on the Today card with no Zotero write. `predict(prestige_network=False)` makes the prestige lookup cache-only — interactive scoring (`reading_queue.live_scoring`, the "why this score?" detail) never blocks on a network search. The corpus `EmbeddingCache` for the aux pass is resolved via `classifier_features._resolve_embedding_cache`, which **reuses the process-wide runtime singleton** instead of building a fresh one per `predict()` batch — a fresh instance's model memo is instance-local, so it reloaded MiniLM once per 50-item batch (a whole-library Rescore reloaded the model dozens of times) |
| `classifier_training.py` | `train_and_save` / `save_trained` (run pipeline → one atomically published ZIP: joblib + metadata). Computes OOF per-class precision/recall/F1 + confusion (`oof_metrics_vs_gold`, via `golden_metrics`, on the post-calibration bins), an `oof_spearman_verified` (the SAME OOF restricted to dated reading-decisions — the gate's real ranking ability, ~0.14; the aggregate `oof_spearman` ~0.72 is inflated by the ~72% undated feed rows the gate trivially rejects), **and a forward-looking `temporal_spearman`** (train on the oldest 80% of DATED rows, score the newest 20%, group-aware split on `days_since_added`). The split treats `days_since_added` ≤ 0 / empty as **undated** (feed rows carry the `-1` "no Zotero date" sentinel): they sort oldest and are never held out, and the holdout fraction is over the *dated* pool — before the 2026-06-19 fix `float("-1")` sorted them as the newest, making the holdout ~94% undated feed junk. `None` when the holdout is <30 rows or labels are constant; surfaced on the Settings ModelCard as "Forward ρ". The forward-split helpers (`_row_days`, `_temporal_group_split`, `_temporal_holdout_metrics`) live in `classifier_temporal.py` (keeps this file under the 500-LOC cap). LambdaRank was A/B'd against the pointwise regression on the same temporal holdout and lost decisively (`tools/eval_temporal_objective.py`) — deliberately NOT adopted. When `runs_log_path` is given, appends a FAIR run-log entry so the Settings ModelCard renders them |
| `classifier_temporal.py` | the forward-looking temporal-holdout diagnostic split out of `classifier_training`: `_row_days` (treats `days_since_added` ≤ 0 / empty as **undated** — the `-1` feed sentinel sorts oldest, never held out), `_temporal_group_split` (newest ~20% of the *dated* pool held out, whole `paper_group_id` groups), `_temporal_holdout_metrics` (one extra fit → forward Spearman ρ, `None` on a <30-row holdout or constant labels) |
| `band_calibration.py` | OOF monotone (isotonic) `raw→relevance` map applied to the 4-class BAND only — makes the compressed top reachable (`must_read` recall collapses otherwise) WITHOUT touching the scores used for ranking/gate-composite. Self-gated: kept only if it lifts OOF must+should macro-F1 (else identity), so it can't regress the banding or invent false must_reads when great papers are scarce. Stored in `TrainedClassifier.calibrator` (None ⇒ identity, backward-compatible) |
| `classifier_backup.py` | versioned gate snapshots — rollback safety for `train-classifier --force`. `save_trained` calls `snapshot_current` BEFORE the overwrite, copying the live `{name}.zip` (or legacy joblib) into `history/{name}/{ts}__{sha8}__oof{ρ}/`. INVARIANT (fail-fast): a snapshot that can't be written RAISES → `save_trained` aborts → the live model is never overwritten without a successful backup. Rolling cap (`DEFAULT_KEEP=10`) prunes oldest. CLI: `goldenset model-history` (list rollback targets) + `goldenset restore-model --snapshot <name>` (atomic restore; backs up the live model first so it's reversible) |
| `classifier_persistence.py` | on-disk location, load, lazy retrain; re-exports the artifact/training API. `load_or_train` threads `triage_db_path` into the retrain so the daemon + active-learning paths apply the SAME `hybrid_gt` verdict overlay as `/admin/retrain` (without it they trained on raw-CSV labels, e.g. missing the unchecked-add downgrade) |
| `model_card.py` | Four-field projection of the currently loaded runtime gate for Settings and setup status. Returns `{"model": null}` when no gate is loaded. No artifact selection, file-stat fields, run-log join or JSONL parsing. |
| `llm_classifier.py` | LLM-as-classifier baseline (title+abstract → label); any OpenAI-compatible model, e.g. `--classifier-name llm_custom` |
| `scoring.py` · `prestige.py` · `surprise.py` | composite score; OpenAlex prestige (`percentile_to_score`: field+year-normalized `citation_normalized_percentile` → [1,5]; cold-start/uncited → neutral 3.0, never floored). **Cold-start author prior** (`cold_start_author_score`, gated by `ColdStartPrestigePolicy` built from the prestige config): when a paper has no percentile yet, lift from the authors' *field-normalized* standing (`OpenAlexWork.max_author_field_percentile`, NOT raw h-index — Leiden #6) — asymmetric (raise-only), capped (`p**gamma`), threaded to BOTH train + predict so the `prestige_score` feature stays consistent; serendipity |
| `reranker.py` | local cross-encoder `Reranker` (sentence-transformers `CrossEncoder`, default `BAAI/bge-reranker-v2-m3`) — lazy background warmup tracked by a stdlib `Future`; fusion while loading, original load/inference errors propagate; thread-locked inference; process-level singleton (`get_reranker`) |
| `claim_checker.py` | local **MiniCheck** encoder (`ClaimChecker`/`get_claim_checker`) scoring claim⊨evidence support probs — the deterministic, reference-free, ~445× cheaper alternative to the LLM overstatement judge. **Phase A: SHADOW only** — `quality_eval` runs it alongside the LLM (gated by `quality_review.shadow_claim_check`, default off) and records `QualityEval.claim_support_probs` for an A/B; the band stays LLM-decided. Mirrors `reranker.py` (lazy singleton, thread-locked inference, standard HF cache / offline-aware). **Optional dep** (`minicheck`, not in base) — a missing package or load failure degrades to `is_ready()=False` (LLM verdict stands). `hf_repo_for(variant)` → the HF repo for prefetch (`flan-t5-large` → `lytang/MiniCheck-Flan-T5-Large`) |
| `rank_blend.py` | the shared ORDER-time relevance × goal-text × prestige blend (`blend_scores`, pure cohort math) consumed by BOTH the Library queue (`library/_ranking`) and the Today slate (`triage/daily_select`) — one primitive, two surfaces, so the validated weights (0.4 goal / 0.15 prestige, blind-judge benchmark: NDCG@10 0.38→0.72) route everywhere and can't drift. Min-max per cohort; absent signal folds its weight back into relevance; unknown prestige → median-of-known (never penalised). Degenerate range (identical present values / single row) → 0.5 (uninformative), EXCEPT a lone present positive `goal_sim` (the cohort's only goal evidence) → 1.0 so it tops the goal axis over the 0.0-pinned no-evidence rows ("1 present value" ≠ "many identical"). The gate's aux pass (`classifier_features._compute_aux_with_context`) computes the per-goal cosines (`aux_context.goal_sims`) from the SAME single embed as `corpus_affinity` — goal_sim is aux-only, deliberately NOT a model feature (the engagement-trained gate would re-weight it back toward "similar to what I've saved"). Companion `quality_bonus(band, grade, *, use_band)` — the capped, order-only deep-review QUALITY lift both consumers apply through `order_within_bands`: first sort by the base blend, then permute only among slots of the same raw relevance category. This preserves the base blend's category sequence (including goal-driven cross-category order), raw scores and classifications; the bonus cap alone does not enforce it. Grade-only by default; band-primary (highlight↑/flag↓; `neutral` & `uncertain`→exactly 0.0) is a Phase-2-measured arm selected by the shared `_common.band_primary_enabled` |
| `rank_blend_quality.py` | the quality-FIRST order key (`quality_first_key`, `unified_quality`; pure cohort math) — the SOTA re-rank for the user's directive *"a high-quality paper a little bit off-topic beats a poor-quality paper on-topic."* Quality LEADS (`q`: grade A=1.0/B=.75/C=.4/D=0 absolute, `flag` band caps ≤0.25, unreviewed → rigor+evidence dims prior shrunk to [0.25,0.75]); topicality (`t`: 0.6·norm(goal_sim)+0.4·(goal_align−1)/4, per-row renormalized) is a FLOORED multiplicative soft-gate `key = q·(0.5+0.5·t)`, never the ±0.06 additive nudge `rank_blend` uses. relevance_score/corpus_affinity/prestige are OUT of the lead (de-leaked AUC: relevance 0.634 weakest, corpus_affinity 0.268 inverts, prestige orthogonal). Pinned invariant: grade-A off-topic (key 0.5) always beats grade-D on-topic (key 0, D's q=0 zeroes it). Consumed by `triage/daily_select` behind `quality_review.rank_quality_first` (`ZS_RANK_QUALITY_FIRST`, default OFF). The soft-gate floor is a swept `gate_floor` kwarg (shipped slate always uses 0.5; only `tools/eval_slate_blend.py --replay` varies it); the Track-C label-free frontier ran + was adversarially verified (ADR-A9 / GAP §G11 — contamination-drop passes; the +0.10 median q-lift bar proved UNACHIEVABLE, oracle ceiling +0.088 with A2 at 100% of it, so offline is exhausted and the flip decision moves to the P3 online interleave). `rank_blend.py` stays the byte-identical control arm |
| `team_draft.py` | pure team-draft interleave kernel (`team_draft_merge`; Radlinski/Chapelle) for the P3 online decision: merges the A0 (control) and A2 (quality-first) slates' top-K into ONE list — shared next pick collapses to `both` (credited to neither, Airbnb-style competitive-pair discipline), disagreements become coin-flip drafting rounds where both picks share a `pair_id` (the scoring unit), an exhausted arm yields uncontested unpaired picks, k-truncation never emits a half-pair (a pair needs both sides shown). Deterministic per `seed` (the slate passes the LOCAL ISO day → same slate on repeated GETs; first-drafter advantage averages out across days). `pair_id` is per-merge (restarts at 1 each call) — cross-call integrity is the log's job (`storage/interleave` day-level write-once). Fail-fast on duplicate inputs / k≤0. Consumed by `triage/daily_select._assemble_interleaved` behind `ZS_RANK_INTERLEAVE`; scored by `tools/eval_interleave.py` (Wald SPRT on user verdicts) |
| `label_weights.py` | per-row training weights by signal tier. Explicit `label:<priority>` verdicts (tier `user_label`) weigh at the top (1.0) — your deliberate, decay-immune ground truth, no longer the 0.7 medium fall-through. Tier dispatch keys on the FIRST pipe segment (a suffixed tier inherits its base, never the 0.7 fall-through); any `outcome_*` segment (resolved 7-day materialization observation, see `golden/hybrid_gt`) → `WEIGHT_REVIEW`. Band-frequency balancing was measured and deliberately NOT shipped (no must_read-recall gain, −9 pts dont_read recall = junk through the gate; see module comment). Recency-decay weighting (half-life sweep, `0.5**(days/halflife)`) was ALSO measured and rejected — it hurt forward ρ (the drift is a base-rate shift, not feature→relevance; and "recent" rows are mostly undated provisional adds) |
| `golden_metrics.py` | accuracy / per-class / confusion for eval; shared finite-vector Spearman diagnostic for training and feed prediction |
| `library_features.py` | features conditioned on your positive-engagement set |
| `active_learning.py` | border-case picks that would most improve the model. Disagreement is judged against your EFFECTIVE label (`label:*`-aware via `hybrid_gt.load_hybrid_labels`) — "the gate disagrees with what *you* decided", not a noisy derived guess; `has_label` flags rows where the truth is your explicit verdict. Pure `_ground_truth` resolver keeps it unit-testable. Golden-CSV read reuses `services._common.load_golden_rows` (de-duped) |
| `tune.py` | Optuna hyperparameter search: paper-group folds, aligned row weights, train-fold positive-library features |
| `eval_baseline/` | 5×5 CV baseline + learning curve with bootstrap CIs |

**Boundaries:** standard services rules. `scoring/prestige/surprise` are shared
primitives also used by `triage/`.

Reranker availability is distinct from failure (A046): a pending load permits
fusion/lexical results, but a completed failed load raises from the next readiness
check or rerank request. Library search, federated ranking and goal-summary
retrieval share this boundary; no consumer silently replaces inference errors
with another ranking. The required `sentence-transformers` import and synchronous
setup load also fail directly. A failed background load remains failed until
process restart; no request-loop retry or extra status API is introduced.
The one-shot executor stops accepting work immediately after submission and its
worker exits after loading; Python joins outstanding executor work on exit.
This does not cancel or time-limit a download/model load.

Label-drift comparison reads `n_train` from the predecessor artifact in the
selected training output directory (ZIP or legacy joblib), before replacement.
It does not infer a deployed model from evaluation/training log rows. This also
handles rollback and CLI/daemon runs without a log path. Missing artifacts or
legacy unknown sizes yield no delta; malformed sizes/artifacts raise. No log-type
filter or alternate history reader is needed.

Model persistence, history, model-card reads and tuning resolve paths at use time
from the selected Settings (`data/models/`, `data/optuna-best-params.json`). Explicit
CLI output paths remain supported. There are no import-time model-path constants
or forwarding `_model_dir` helper; pretrained dependency downloads retain their
third-party cache policy, separate from these mutable application artifacts.

`classifier_store.py` owns the single-artifact format: `{name}.zip` contains
`model.joblib` and `metadata.json`, published together by one atomic replace.
History reads embedded metadata without unpickling; current-model cards read
only the live object. Legacy `.joblib` inputs
remain supported, deriving metadata from the object, never its JSON twin; ZIP
wins when both formats exist. Corrupt archives raise, without legacy fallback or
implicit retraining. Existing files are not migrated or deleted automatically.
Only locally trusted artifacts may be loaded (joblib executes pickle code).

The store assigns `TrainedClassifier.model_sha256` from the exact artifact bytes
after successful publication or loading (including legacy joblib). Loading hashes
and deserializes one open descriptor; saving hashes the private staging file,
so concurrent replacement cannot attach another writer's identity. The runtime
identity is recomputed on load, never trusted from the pickle. It is not a new
metadata sidecar; no per-event file read is needed. `golden_csv_sha256` continues
to describe training input, not model identity. Restoring the same ZIP restores
its identity; reserializing/repackaging produces a new artifact identity.

Restore validates the payload and metadata before touching live state, backs up
the current artifact without pruning, then publishes one archive before pruning.
Legacy snapshots are converted on restore. A failed restore retains all backups.
Unique snapshot directories and per-write staging files prevent same-time
collisions. Atomic replace prevents partial publication, not power-loss durability
or serialization of concurrent training/restore operations.

ML and LLM prediction-column writes share `golden.csv_store.edit_csv` with label
appends and export: the per-file lock covers reading through atomic replacement,
so parallel classifier writers cannot drop new labels or each other's columns.

LLM classification propagates provider/schema errors before returning any batch
for publication. Pending futures are cancelled on failure or interruption;
already-running HTTP calls finish under the provider's timeout. There is no retry
or partial-success mode. Structured priorities use the existing `ReadingPriority`
enum: unknown/noncanonical strings are rejected, never converted to `could_read`.
Rows without title/abstract retain the explicit skip contract (empty priority,
`skip_reason`); this is missing input, not a provider failure.

Classification metrics take the effective input rows and current keyed predictions,
not a CSV path or prediction/split column names. CLI CV and holdout use their report
keys; LLM evaluation uses only this invocation's results. Later verdict edits and
old prediction columns cannot change the run's evaluation truth or population.
Source labels remain untouched when prediction columns are published. This does
not change fold construction or resolve the separate feature-leakage findings.

`classifier_inputs.load_training_inputs` supplies both the actual hybrid training
rows and their reuse identity to training, cached loading and daemon scheduling.
The identity includes effective training columns after the verdict/outcome overlay,
goals/corpus/prestige config, requested classifier/folds/PCA, effective LightGBM
tuning, authoritative corpus/goal rows, Python/package versions and application
Python source. Missing identity on legacy artifacts means stale. Lookup-cache
warming is excluded; no database is created by the identity reader. Hashing the
whole source package is deliberately conservative (unrelated code/dependency
changes can retrain); no dependency-graph registry or version sidecar is added.
Prediction columns, unrelated CSV audit metadata, column order and CSV formatting
do not invalidate reuse. `_TRAINING_COLUMNS` lists content, paper identity,
targets, weighting, eligibility and recency inputs; update it when training starts
consuming another field. Row order remains significant for fold/array alignment.
The separate `csv_sha256` still records exact input bytes from the shared reader;
reusing a model does not rewrite its original training provenance or artifact.
Training saves the pre-training identity, never a later CSV hash. Concurrent
input edits therefore remain detectable by the next reuse check; this is not a
cross-store snapshot or a lock on human labeling. Encoder provenance and
train/evaluation fit-recipe consistency have separate checks below.
The SPECTER2 tokenizer/base and proximity adapter are pinned to immutable Hub
commits in `classifier_const.py`, shared by loading, input identity and the
single/batch vector-cache key. Pins match the pre-existing local snapshots:
[base 3447645](https://huggingface.co/allenai/specter2_base/tree/3447645e1def9117997203454fa4495937bfbd83),
[adapter 2081559](https://huggingface.co/allenai/specter2/tree/2081559630a80fc5851d8f798a05ba81e9468089).
Tokenizer/base use `revision`; the installed adapters API uses `version`, which
forwards to Hub `revision`. Upgrading pins is an explicit code change + process
restart, followed by retraining and lazy vector recomputation. Old unversioned
vectors are cache misses, not deleted in bulk. Content fields use JSON framing,
so delimiter-containing titles cannot collide with a different title/abstract
split. External lookup-cache refresh policy is not a guarantee of identical
future feature values; corpus embeddings use their own storage-level identity below.
The CV cache-hit probe is read-only and distinguishes an absent DB/table from
SQL/schema failures, which propagate instead of being counted as cache misses.

LightGBM training snapshots tuned parameters/PCA once in `TrainingInputs` and
passes that same recipe to every OOF fold, the temporal holdout and final fit.
The final stage no longer rereads the Optuna file. `_fit_lightgbm_regressor`
owns regression defaults and weighted fitting for both evaluation and persistence;
PCA remains fitted only on each training subset, never the validation rows.
`training_metadata.fit_options` records the overrides used by these stages.
A tuning-file edit during training affects the next reuse check, not later folds
of the current run. The calibrator therefore receives OOF predictions using the
deployed LightGBM recipe. This does not resolve separate feature/label leakage
findings or make tuning-selection metrics an independent nested-CV estimate.

Optuna uses the deployed OOF splitter (`paper_group_id` + `GroupKFold`) on
eligible, featurized rows and passes their confidence weights to every fit.
Too few distinct papers raises before any trial or result publication; twins
cannot cross a fold under the existing DOI/title grouping policy. Its seed now
controls only the Optuna sampler. Tuning and baseline share a train-fold positive
library rebuild, including held-out author features, without rereading embeddings
or retaining all fold matrices. Corpus affinity now follows the same train-only
boundary, using a frozen per-run corpus snapshot rather than repeated inference.

`library_features.recompute_engagement_columns` owns that train-only rebuild
for baseline, learning curves, Optuna, persisted training OOF, temporal holdout
and feed-prediction diagnostics. Baseline's optional matrix override is removed;
its fold-fit boundary always rebuilds P and corpus affinity. Feed prediction reuses training's
batched featurization and OOF loop instead of maintaining copies.
`compute_library_features(candidate_row=...)` takes authors and paper identity
together: it excludes the entire `paper_group_id` group from vectors, both
centroids and authors, including a new key for a known DOI/title. This replaces
the separate author-string/exact-key arguments. Existing grouping policy still
uses DOI when present, otherwise normalized title, otherwise key; it does not
reconcile incomplete/conflicting paper metadata across those identity types.

New archives store one `PositiveLibrary`, retaining raw vectors, recency, paper
groups and per-row authors. Four centroid/embedding constructor fields are
removed from `TrainedClassifier`; its legacy reader still recognizes old pickle
state. Historical centroid-only artifacts lack exclusion metadata and retain
their old feature behavior until retrained. Existing archives/metrics are not
rewritten; source changes invalidate the normal cached-training reuse identity.
The transient corpus snapshot is not archived; final fitting keeps full-corpus
self-excluded scores, matching current inference. Historical metrics are not
an independent evaluation of model/calibrator selection.

Training (aggregate OOF, dated OOF and forward holdout) and one-shot feed prediction
share `golden_metrics.spearman_correlation`. Fewer than three rows or constant
labels/predictions mean `None`, not zero/NaN; forward evaluation retains its
30-row minimum. Invalid shapes or nonfinite inputs raise. Model metadata stores
unavailable diagnostics as JSON `null`, and startup/install/CLI logs render `n/a`.
Archive publication rejects NaN/Infinity with stdlib strict JSON before the atomic
replace, leaving a prior model intact on failure. Existing archives are not
rewritten or silently sanitized. Offline baseline/tuning metric policies remain
separate from these training diagnostics.

`year_recency` uses the UTC calendar year at feature construction, not an
import-time or hardcoded constant. Training snapshots `feature_reference_year`
with its inputs, uses it for the entire feature matrix (including OOF/temporal/
final fits), and stores it in model metadata. The year participates in the
shared reuse hash, so a rollover makes cached-loader and daemon checks request
retraining without a CSV edit or process restart. Inference uses the current
year; the existing gate may remain live while its replacement trains. Missing
years still map to 20 and known ages remain clamped to 0–20. This does not add a
timer or refresh already persisted prediction rows independently of their
existing rescore paths.

Unknown prestige in `rank_blend` is imputed with stdlib `statistics.median`:
for an even-sized known cohort, the two central values are averaged. Both Library
and Today use this one primitive; known `[0.1, 0.9]` therefore gives unknown
prestige 0.5 rather than tying the best paper. Empty/single/tied cohorts retain
their defined uninformative behavior. The redundant `_median` helper is removed.

Configured auxiliary-provider initialization and feature-computation failures now
propagate: in particular, an incompatible corpus encoder cannot silently become
zero affinity/no goal signal during training or prediction. Disabled providers
retain their documented neutral values. Corpus storage validates vector provenance
and invalidates both matrix caches on external writes (A040). These checks are
separate from the engagement-feature exclusion rules described here.

Classifier auxiliary scoring passes DOI into the shared corpus matcher. It
excludes the candidate's own positive/negative engagement by normalized DOI,
or normalized title when DOI is missing, while retaining per-goal similarity.
Corpus DOI metadata arrives through normal refresh (v6 migration preserves old
rows); cached training identity already hashes authoritative corpus rows.
Training featurization now returns `_TrainMatrix` with its aligned targets,
weights and `CorpusAffinity` snapshot, removing the duplicate assembly in feed
prediction. Baseline/tuning carry the snapshot in `FeaturizedGolden`. Every
fold/forward holdout restricts reference corpus papers to training identities;
held-out identity wins over an ambiguous train alias, and unassigned corpus rows
are excluded. No fold/trial rereads the corpus or re-embeds candidates. Final fit
and inference still use the whole current corpus minus candidate matches.
Legacy `cross_validate` and `tools/eval_temporal_objective.py` also use this shared
engagement-column rebuild. The legacy CV's row-wise split/calibration recipe is
otherwise unchanged; this correction does not make that recipe equivalent to
deployed regression or reconstruct historical engagement changes.

Quality-order evaluation: the additive `blend+grade` / `blend+band` score arms
in `tools/eval_slate_blend.py` remain counterfactual ablations, not a replay of
the category-slot restriction. Its `--replay` also compares score keys directly,
not the production role allocator; neither measures this new ordering policy.
The new restriction has regression coverage, not a measured user-preference lift;
bonus magnitudes and feature-flag defaults are unchanged.
