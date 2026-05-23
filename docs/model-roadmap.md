# Paper-prediction model — redesign roadmap

**Created**: 2026-05-15
**Source**: synthesis of a 4-agent ML debate (round 1 + round 2 with empirical fact-check) plus the user's explicit preferences. Stored here so future sessions can pick up the plan without re-running the debate.

---

## Business goal (single source of truth)

> Из имеющейся Zotero-библиотеки выучить, что пользователь читает, и по RSS-фидам предсказывать какие статьи стоит прочитать (1–2 в день). Минимум статей в день, без ручной возни.

Implication: the model is a **personalized relevance ranker**, not a classifier. The product output is a **ranked list per daily RSS tick**, top-1–2 of which land in the Zotero Inbox.

---

## Final results — Sprint 3 V3 (May 16, 2026)

Champion stack: regression on `gold_inferred_relevance` + SPECTER2
proximity adapter + PCA SPECTER2→128 + Optuna-tuned LightGBM + per-row
`sample_weight` (`first_glance` rows kept at 0.2 instead of dropped).

| Metric | Pre-Sprint | Sprint 3 V3 | Δ |
|---|---:|---:|---:|
| AUC | 0.570 | **0.788** | **+0.218** |
| Spearman ρ | 0.205 | **0.763** | **+0.558** |
| Kendall τ | 0.177 | **0.581** | **+0.404** |
| NDCG@10 | 0.694 | **0.807** | **+0.113** |
| MAE (on [1,5]) | 1.407 | 0.496 | −0.911 |
| Cohen's κ (4-class) | 0.042 | **0.472** | **+0.430** |

All deltas have non-overlapping 95% CIs vs the pre-Sprint baseline. The
4-class output is no longer "noise" (κ≈0.04) — it's a real classifier
(κ≈0.47).

The biggest single jump came from **sample weighting** (V1 → V3):
keeping `first_glance` rows with `weight=0.2` instead of dropping them
gives the regressor ~647 additional weakly-supervised negatives, which
dramatically sharpens the decision boundary.

## Active-learning UI (Sprint 3 wiring)

Two surfaces let the user pick high-value rows to re-label:

| Surface | URL | What it sorts |
|---|---|---|
| Library border | `/annotate` → chip **🎯 border** | All non-feed library rows, ranked by predicted-score distance to nearest priority threshold (4.5 / 3.6 / 2.6). Backend re-trains the regressor on every call (~30 s) so the chip blocks for that duration on first click; result cached 5 min. |
| Feed border | `/review` → button **🎯 border** | The current `awaiting_review` or `gate_rejected` queue, re-sorted client-side by `composite_score`-distance-to-threshold. Instant. |

Workflow: open the chip → click first row → set verdict (`must`/`should`/
`could`/`dont`) → auto-advance to next → repeat. 10–20 border re-labels
≈ +0.01–0.03 marginal AUC at this point in the curve.

## Sprint-1 + Sprint-2 results (May 15, 2026)

Implementation completed in one autonomous session. Evaluation harness: same
5×5 + BCa bootstrap (B=2000) as previous baselines, n=524 training rows
after the tier filter (down from 1387 pre-Sprint).

| Metric | Pre-F5 (n=1393) | Post-F5 (n=1387) | **Sprint 2 (n=524)** | Δ post→S2 |
|---|---:|---:|---:|---:|
| Spearman ρ | 0.205 | 0.229 | **0.242** [0.21, 0.28] | +0.013 |
| **AUC** | 0.570 | 0.580 | **0.638** [0.62, 0.65] | **+0.058 ★** |
| **NDCG@10** | 0.694 | 0.728 | **0.785** [0.77, 0.80] | **+0.057 ★** |
| Cohen's κ (4-class) | 0.042 | 0.043 | **0.095** [0.07, 0.12] | **+0.052 ★** |
| **MAE** (on [1,5] scale) | 1.407 | 1.397 | **0.383** [0.37, 0.40] | **−1.014 ★** |

★ = non-overlapping 95% CIs vs the post-F5 baseline (statistically
significant at α=0.05).

**Headline**: AUC moved from "barely above chance" (0.58) to **0.64** —
not a literature-leading result, but the absolute gap is real and the CIs
don't overlap. NDCG@10 captures 78% of theoretical gain (vs 73%
before). MAE collapsed from 1.4 to 0.38 — the regressor recovers the
1–5 score directly instead of inferring it from a binary probability.
Cohen's κ doubled (still low, but no longer indistinguishable from
chance).

Spearman ρ is roughly flat (+0.013, CIs overlap) — expected: the tier
filter removed both noisy and useful rank-pairs in roughly equal
proportion, while AUC / NDCG / MAE all benefit from the regression
objective's sharper score distribution. Kendall τ slipped −0.026 for
the same reason (pairwise correlation is more conservative).

**Verdict**: Sprint 1+2 shipped. Sprint 3 (LambdaRank) is **dropped** —
re-cast as a three-pronged AUC-push sprint instead:

## Sprint 3a/b/c — AUC push (May 15-16, 2026)

The user pushed back on AUC=0.64 as still too low for triage (target ≥
0.75). Three parallel changes layered on top of Sprint 1+2:

| Sprint | Change | Implementation |
|---|---|---|
| 3a | SPECTER2 proximity adapter | Switched `_load_specter2` from `transformers.AutoModel(specter2_base)` to `adapters.AutoAdapterModel(specter2_base)` with `load_adapter("allenai/specter2", load_as="proximity", set_active=True)`. The adapter (Singh et al. 2023) is fine-tuned for nearest-neighbour retrieval — exactly the geometry our P-set features need. Cache hash now mixes in the adapter name, so the change auto-invalidates every old embedding. |
| 3b | PCA SPECTER2 → 256 dims | New `pca_specter_dim` kwarg on `_fit_predict`. When set, the 768-d SPECTER2 block is PCA-reduced inside the fold (TRAIN-fit, val-transform — no leakage), then concatenated with the 12 tabular extras. Controls overfitting on n≈500. The PCA object is persisted in `TrainedClassifier.pca_object` and replayed at inference. |
| 3c | Optuna hyperparameter sweep | New `services/tune.py` module and `zotero-summarizer goldenset tune` CLI. Optimises median per-fold Spearman ρ over a TPE-sampled search space (`n_estimators 100..800`, `num_leaves 7..63`, `max_depth 3..10`, `learning_rate 0.01..0.2`, regularisation, subsample, colsample, and the 3b PCA dim ∈ {None, 128, 256, 384}). Saves winning params to `~/.cache/zotero-summarizer/optuna-best-params.json`; `train_and_save` auto-picks them up via `load_tuned_params`. |

---

## Honest current state (post-F5, commit b2f116e + F5 fix)

| Metric | Value | Source |
|---|---|---|
| Spearman ρ | 0.229 [0.21, 0.25] | [eval-baseline-postF5-20260515.json](../eval-baseline-postF5-20260515.json) |
| AUC | 0.580 [0.57, 0.59] | same |
| NDCG@10 | 0.728 [0.71, 0.75] | same |
| Cohen's κ (4-class) | 0.043 | same — **the 4-class output is noise** |
| n_train | 1387 (190 positive, 13.7% base rate) | post-F5 |
| Deployed model | LightGBM binary classifier + isotonic + quantile bins | [classifier_persistence.py](../zotero_summarizer/services/classifier_persistence.py) |

**Literature ceiling for "abstract + metadata only" scholarly recommenders** (Beel 2016): ρ ∈ [0.20, 0.40]. We're in the lower half. The user's true ceiling on this label set will be measured by the blind re-label session (`relabel-audit-session.json`) — current best estimate from the baseline report: C ≈ 0.50–0.65 Pearson r.

---

## Core insight (user's framing, May 2026)

> "Could_read — это не значит, что статья отстой. Это значит, что в целом норм, можно и почитать. Не полный реджект. Поэтому именно число тут будет лучше работать."

Empirical confirmation from [zotero-summarizer-golden.csv](../zotero-summarizer-golden.csv) — `gold_inferred_relevance` distribution by `gold_priority_final` is essentially deterministic:

| Class | mean gold_inferred_relevance | semantic |
|---|---:|---|
| must_read (n=65) | 4.92 | "definitely read this" |
| should_read (n=125) | 4.10 | "should read soon" |
| **could_read (n=645)** | **3.21** | **"норм, можно почитать"** — NOT a reject |
| dont_read (n=552) | 1.00 | "not for me" |

The current pipeline converts this clean ordinal scale into a binary `{must,should}=1 / {could,dont}=0` target, then post-hoc re-quantizes the prediction into 4 classes. Both steps destroy information.

**The redesign target: a continuous regressor on `gold_inferred_relevance` ∈ [1, 5].** The product surface ranks daily-tick items by predicted relevance and takes the top 1–2.

---

## What changes vs. today

### Architecture changes

| Change | What | Why | Files |
|---|---|---|---|
| **Objective**: binary → regression | `LGBMRegressor` with `objective='regression'`, target = `gold_inferred_relevance` | Preserves the ordinal distance (must vs could is "1.7 apart", not "different class") and matches the user's mental model | [classifier.py:_fit_predict](../zotero_summarizer/services/classifier.py), [classifier_persistence.py:_raw_predict](../zotero_summarizer/services/classifier_persistence.py) |
| **Drop authors+venue from SPECTER2 text input** | Concatenate only `title [SEP] abstract`. Move author/venue signal into tabular features (later sprint) | SPECTER2 was trained on `title [SEP] abstract` only (Cohan 2020). Author/venue strings push off the training distribution and pollute cosine similarities | [classifier.py:75-160](../zotero_summarizer/services/classifier.py#L75-L160) |
| **Filter training rows by `gold_signal_tier`** | Drop tiers `{first_glance, meta}`. Keep `hard_veto` (the 8 explicit 👎/🥱/❌ negatives) | Empirically verified: 647 `first_glance` rows are UI batch dismissals (mostly fast auto-rejects), 216 `meta` rows are library items with zero positive engagement. Together they're ~62% of the trainable set and the noisy source. F5 also keeps `in_trash` filter. Result: n=524, mean class quality much higher | [classifier.py:441-454](../zotero_summarizer/services/classifier.py#L441-L454), [classifier_persistence.py:281-297](../zotero_summarizer/services/classifier_persistence.py#L281-L297), [eval_baseline/_featurize.py:48-58](../zotero_summarizer/services/eval_baseline/_featurize.py#L48-L58) |
| **Two new library-conditioned features** | `nearest_kept_cosine`, `positive_centroid_cosine` over the positive-engagement subset P (≈169 items: 🧠/👀/🗝/✅/👍 emoji OR annotation_count>0 OR user-note exists) | The model currently doesn't see what the user actually reads — only `corpus_affinity` to the declared `research_goals`. These two features close that gap with the smallest possible footprint | new helper in [services/scoring.py](../zotero_summarizer/services/scoring.py) or [services/classifier.py](../zotero_summarizer/services/classifier.py) `_compute_aux_with_context` |
| **Move F4 raw_score floor into TrainedClassifier.predict** | The `raw_score_dont_read_below` rule currently lives only in the daemon path ([feeds.py:599-608](../zotero_summarizer/services/feeds.py#L599-L608)). Move to `TrainedClassifier.predict` so every caller (daemon, CLI, ad-hoc scripts) agrees | Cleans up the discrepancy where `feed-predictions-2.csv` showed all `should_read` despite the floor | [classifier_persistence.py:predict](../zotero_summarizer/services/classifier_persistence.py#L125) |
| **UI: deprecate 4-class chip, show numeric score** | The chip showed must/should/could/dont with κ ≈ 0.04. Replace with a single 0–5 score (or 1–5 rounded) | The 4-class output is statistically indistinguishable from noise; the continuous score has real signal | React UI: [frontend/src/components/PaperListItem.jsx](../frontend/src/components/PaperListItem.jsx) (the old `web/ui.html` was removed in the 2026-05-15 redesign) |

### What is **NOT** changing

- SPECTER2 as the embedding model (good baseline, would need a separate ablation to swap)
- The 7 existing tabular features (`has_doi, has_venue, year_recency, title_log_len, abstract_log_len, corpus_affinity, prestige_score`) — kept; we only add 2 new ones
- The Phase 1.13 classifier-gate pipeline (RSS → gate → LLM → composite → Zotero) — still gates on predicted-priority, but the priority is now derived from the continuous score
- LightGBM as the underlying model (stays; we change the objective only)
- The eval-baseline harness (it already supports continuous metrics — Spearman, NDCG, MAE)

---

## Sprint plan

### Sprint 1 — Regression + label hygiene + library features (5 days)

Goal: ρ 0.229 → ~0.28–0.31 with high confidence, by changing the loss to regression and cleaning labels.

| Day | Change | Acceptance test |
|---|---|---|
| **1** | Drop `gold_signal_tier ∈ {first_glance, meta}` from training in all three training paths (`classifier.predict_new_items`, `classifier_persistence.train_and_save`, `eval_baseline._featurize`). Plus the existing F5 in_trash filter | Eval baseline shows n=524, all 53 existing tests still pass |
| **2** | Switch `_fit_predict` to support `objective='regression'`. Train on `gold_inferred_relevance`. Update `eval_baseline._runners` so Spearman is computed on `y_continuous` vs `predicted_continuous` (no calibration needed — regression output IS the score) | New JSON written; ρ ≥ 0.25 on the cleaned subset (necessary, not sufficient) |
| **3** | Remove authors+venue from `get_or_compute_embedding` text concatenation. Bust the SPECTER2 cache for affected rows (or version-bump the cache key) | Eval baseline; ρ delta ±0.02 expected |
| **4** | Add `nearest_kept_cosine` and `positive_centroid_cosine`. Centroid computed once over P (precomputed in cache); k-NN over P=169 is a single numpy dot-product. Feature dim goes 775 → 777 | Eval baseline; expected ρ delta +0.01 to +0.03 |
| **5** | Move F4 floor into `TrainedClassifier.predict`. UI surfaces show the numeric score; the priority chip (kept for backward compat) is derived from `score >= threshold` not from the model directly | All tests pass; `feed-predictions-*.csv` now reflects the floor in `predicted_priority` |

Hard rule: each change behind a feature flag or env var, eval-baseline JSON is the artifact of record, rollback path is `git revert` of that single change.

### Sprint 2 — Personalization deepening (1–2 weeks, opt-in)

Only if Sprint 1 ships and ρ improves. Adds the rest of B's feature stack:

- `recent_centroid_cosine` (90-day window on P) + `topic_drift` (recent vs all-time centroid delta)
- OpenAlex `author_id` resolution → `author_overlap_id` (count of P-authors among this paper's authors)
- `author_max_engagement_weight` (max engagement among overlapping authors)

Skipped: `venue_in_positive_subset` (6% library coverage, 0% feed coverage in our sample — not worth the engineering).

### Sprint 3 — Optional LTR head (2 weeks, decision after Sprint 2)

Add a parallel `LGBMRanker(objective='lambdarank', lambdarank_truncation_level=2)` head. Group construction needs **real daily-tick groups** captured during deployment (the current monthly `dateAdded` bucketing degenerates because 653 `first_glance` rows have `days_since_added=-1`). To collect real groups: log each `feeds tick` as a `group_id` for ~4 weeks, then retrain.

This is the "do it properly" path C argued for. We defer because regression alone gets us most of the way and ranking adds infra complexity (groups, NDCG@k CI, leave-week-out CV).

### Out of scope / explicitly skipped

| Idea | From | Why skipped |
|---|---|---|
| `/zs/feeds-v3` provenance audit for app-vs-user notes | A | F8 is already filtered in `goldenset.py:208-216` via `NOT LIKE '%zs:note_type=%'`. Belt-and-suspenders not worth the work |
| Drop the 4-class output entirely (UI rip) | C, A | Frontend change, deferred to a separate UI pass; backend already exposes the continuous score |
| OpenAlex `author_id` resolution for Sprint 1 | B | Sprint 2 / 3 — surname collisions make naïve overlap useless and ID resolution is ~1–2 weeks |
| `venue_in_positive_subset` feature | B | 6% library coverage, 0% feed sample coverage → mostly NaN. Defer until OpenAlex venue normalization lands |
| Platt calibration | D | Under regression, we don't need a probabilistic calibrator — the score IS the prediction |
| Optuna hyperparameter sweep | D | Default LightGBM hyperparameters are close-to-optimal on n≈500 tabular+embedding problems; expected ρ delta < 0.01 |

---

## Measurement protocol

Every sprint change runs through the existing eval harness with consistent commands:

```bash
# Pre-change baseline (post-F5)
# eval-baseline-postF5-20260515.json   ρ=0.229 [0.21, 0.25]

# Sprint 1 stacked
uv run zotero-summarizer goldenset eval-baseline \
    --classifier lightgbm-regression \
    --n-repeats 5 --n-folds 5 --n-bootstrap 2000 \
    --output eval-sprint1-regression.json
```

A change "wins" iff its Spearman ρ CI lower bound ≥ 0.229 (the post-F5 point estimate). Run each change in isolation AND stacked. Stacked-only improvement is a red flag for over-fitting interactions; isolated improvement is the cleaner story.

---

## Why this redesign (and not the alternatives)

Four ML engineers debated this. After two rounds + empirical fact-checks, the synthesis collapses to the above. Specifically:

- **Engineer A** (ruthless label hygiene, 230 clean labels): partially adopted — we drop first_glance + meta, keep hard_veto. A's deeper cut to ~230 was over-aggressive given that even noisy `could_read` rows carry signal under regression (mean ≈ 3.2, not 0).
- **Engineer B** (8 personalization features, +0.10–0.15 AUC claim): partially adopted — top-2 features (`nearest_kept_cosine`, `positive_centroid_cosine`) ship in Sprint 1. The other 6 deferred to Sprints 2–3 because their dependencies (OpenAlex ID resolution, venue normalization) take >1 week.
- **Engineer C** (LambdaRank with NDCG@2 objective): deferred to Sprint 3. C's monthly group construction degenerated (`days_since_added=-1` for 653 rows). The user's preference for "predict a number, not a class" also favors regression over ranking — regression preserves the semantic that `could_read = 3.0 = "норм"`, while LambdaRank flattens it to `rel=1 = "above zero"`.
- **Engineer D** (5 pragmatic changes, 1-week sprint): mostly adopted as the Sprint 1 skeleton. D's mistake was dropping `hard_veto` (only 8 explicit negatives in the data) — fixed in the synthesis.

---

## Open questions for the next session

1. **Ceiling measurement (C)**: blind re-label of 79 papers via [Re-label Audit UI tab](http://127.0.0.1:8000) → produces the Pearson r ceiling. Pending user action.
2. **First daily-tick logging for Sprint 3**: do we want to start capturing `group_id` per tick now, so by the time we get to Sprint 3 we have ≥30 real groups? Decision: defer until Sprint 1 lands.
3. **What to do with the 374 `could_read` rows in the engaged subset**: they're noisy positives (mean ≈ 3.21, low strength). Under regression, they contribute moderate-strength signal. Under any future re-classification head, they're ambiguous. For now: keep, they're the "norm, можно почитать" middle the user wants the model to learn.


Финальная сводка сессии
Что сделано сегодня:

Docs pass — 5 файлов с честными CI'd числами вместо устаревших (feeds.md, architecture.md, operations.md, configuration.md) + memory F7
F5 fix — in_trash фильтр в 3 training paths: classifier.py:441-454, classifier_persistence.py:281-297, eval_baseline/_featurize.py:48-58. Все 53 теста пасуют
E3 ablation — измерили эффект F5: ρ 0.205 → 0.229 (+12%), NDCG@10 0.694 → 0.728, все дельты в правильном направлении
4-agent debate в 2 раунда с эмпирическим fact-check — выявили F8 уже починен, скорректировали D-фильтр (keep hard_veto), сошлись на синтезе
Твоё ключевое уточнение "could_read = 3.0 = норм, не реджект" → сдвинули выбор objective с lambdarank на регрессию на gold_inferred_relevance. Данные подтверждают: must=4.92, should=4.10, could=3.21, dont=1.00 — чистая ординальная шкала
Roadmap записан в docs/model-roadmap.md — Sprint 1 (5 дней) с регрессией + label hygiene + 2 фичи над библиотекой, Sprint 2-3 deferred
Что НЕ сделано (и почему):

Production retrain (goldenset train-classifier) — не запускал, ждёт твоего решения по Sprint 1
Sprint 1 implementation — план готов, но это новая сессия (это сегодня был аудит + дизайн, не реализация)
Готов начать Sprint 1 в следующей сессии (или сейчас, если скажешь). Ожидаемый результат: ρ 0.229 → ~0.28–0.31 за 5 дней работы.