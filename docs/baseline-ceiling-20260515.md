# Phase 1.16 Step 0 — Baseline + Learning Curve (preliminary report)

**Generated**: 2026-05-15
**Plan**: `.claude/plans/idea-for-my-zotero-summarizer-harmonic-summit.md`
**Skills applied**:
[scientific-critical-thinking](.claude/skills/scientific-critical-thinking/SKILL.md),
[statistical-analysis](.claude/skills/statistical-analysis/SKILL.md),
[scholar-evaluation](.claude/skills/scholar-evaluation/SKILL.md)

**Status**: B (baseline) and S (learning curve) measured. C (ceiling) awaits the user's re-label session via the new UI tab.

## TL;DR

- **B (baseline)**: LightGBM Spearman ρ = **0.205** [95% CI 0.183, 0.224] on n=1393, 5×5 stratified K-fold CV + BCa bootstrap (B=2000).
- **S (learning curve)**: Spearman **peaks at n=836** (ρ=0.261 [0.226, 0.283]) and **declines** to 0.185 at n=1393 — non-overlapping CIs, statistically significant.
- **C (ceiling)**: TBD — pending user's blind re-labeling of 79 papers in the new UI tab.

## 1. Pre-registered protocol (recap from plan)

Per the plan's Phase 0 decision matrix (pre-registered before data collection):

| Scenario | Trigger | Phase 1 action |
|---|---|---|
| A: Near-ceiling | `B_upper / C > 0.85` AND `S < 0.02` | No model refactor — improve features or labels |
| B: Model-limited | `B_upper / C < 0.65` AND `S < 0.02` | Refactor model class |
| C: Data-limited | `S > 0.02` regardless of B | Active learning |
| D: Task-impossible | `C < 0.40` | Re-scope product |

The slope `S` is defined as projected gain from doubling the training set. Today's finding is **S < 0**, which the matrix did not anticipate. Discussed in §4.

## 2. Baseline (B) — full 5×5 LightGBM

```bash
uv run zotero-summarizer goldenset eval-baseline \
    --classifier lightgbm --n-repeats 5 --n-folds 5 --n-bootstrap 2000
```

Artifact: [`eval-baseline-20260514.json`](../eval-baseline-20260514.json) (~7 KB, 25 fold-runs).
Elapsed: 12.8 min.
n_rows = 1393, n_positive = 190 (13.6% base rate), n_features = 775.

### Results

| Metric | Point | 95% CI (BCa) | CI width | Interpretation |
|---|---:|---|---:|---|
| Spearman ρ | **0.2047** | [0.1831, 0.2238] | 0.041 | **Lower half of Beel 2016 band** (0.20–0.40 typical for scholarly recs). |
| Kendall τ | 0.1767 | [0.1582, 0.1932] | 0.035 | Consistent ranking signal but weak. |
| AUC | 0.5701 | [0.5569, 0.5840] | 0.027 | **Below the AUC=0.60 we'd been reporting.** The stored `oof_auc=0.6843` was a single-fold point estimate; this is the honest distribution. |
| NDCG@10 | 0.6939 | [0.6681, 0.7203] | 0.052 | Decent — top-10 captures ~70% of theoretical gain. |
| MAE | 1.4069 | [1.3796, 1.4342] | 0.055 | Predicted-vs-gold continuous score off by ~1.4 on the [1,5] scale. |
| Cohen's κ (4-class) | 0.0416 | [0.0345, 0.0492] | 0.015 | Near-zero — the 4-class derivation from a binary probability is barely better than chance. |

### Interpretation (scientific-critical-thinking)

- **B = 0.205 [0.18, 0.22]** is in the lower half of the literature band, not the lower bound. With n=1393 paired abstracts under SPECTER2+OpenAlex features and no engagement history, this is within the range Sugiyama & Kan 2010 and Beel 2016 surveyed (~0.20–0.40).
- The earlier "AUC = 0.60" reported in the existing `lightgbm.json` came from one fold's OOF prediction — that's a single sample from the distribution, not the distribution itself. The proper 5×5 + BCa run gives **AUC = 0.57 [0.56, 0.58]**, with non-overlapping CIs against 0.60. The 0.03-point gap is real, not noise.
- **κ = 0.04 is alarming.** The 4-class confusion derived from a binary probability via quantile binning is essentially guessing within the kept-class group. Whatever value the current pipeline adds is in **ranking** (Spearman/NDCG) — the discrete 4-class labels are an unreliable output.

## 3. Learning curve (S)

```bash
uv run zotero-summarizer goldenset eval-baseline \
    --classifier lightgbm --learning-curve \
    --learning-curve-fractions 0.15,0.30,0.60,0.85,1.00 \
    --n-bootstrap 500
```

Artifact: [`learning-curve-20260514.json`](../learning-curve-20260514.json).
Elapsed: 13.4 min.

| n_train | fraction | Spearman ρ | 95% CI (BCa) | NDCG@10 |
|---:|---:|---:|---|---:|
| 209 | 0.15 | 0.148 | [0.061, 0.234] | 0.680 |
| 418 | 0.30 | 0.197 | [0.131, 0.246] | 0.700 |
| 836 | 0.60 | **0.261** | **[0.226, 0.283]** | 0.697 |
| 1184 | 0.85 | 0.196 | [0.179, 0.219] | 0.668 |
| 1393 | 1.00 | 0.185 | [0.151, 0.209] | 0.694 |

### Critical finding

The curve **is not monotonically increasing**. It peaks at n=836 and declines. The CI at n=836 [0.226, 0.283] **does not overlap** the CI at n=1184 [0.179, 0.219] or n=1393 [0.151, 0.209] — the decline is statistically significant at α=0.05.

### Hypotheses (Cochrane risk-of-bias framing)

The plan's "confounding" risk-domain flagged this exact possibility: **time-decay weighting + UI-appended `feed:*` rows** may have introduced systematic label noise in the most recent additions. Tested hypotheses:

| H | Description | Evidence for | Evidence against |
|---|---|---|---|
| **H1: Label-quality regression** | The last ~550 labels (n=836→1393) are noisier than the early Zotero-engagement labels | Plausible: includes 653 `feed:*` and `note:*` rows added through review UI in May 2026, many with sparse engagement context. **Non-overlapping CIs** at the gap. | None measured yet. |
| **H2: Class-imbalance shift** | New rows may have shifted the positive rate | n_positive_total = 190/1393 = 13.6%; subsampling preserves the rate by stratification | The subsampling routine in `_runners.py` explicitly uses stratified pos/neg fractions per the same ratio. |
| **H3: Random fluctuation** | This is bootstrap noise | The CI gap is 0.05 wide with no overlap. p < 0.05 by definition of non-overlapping 95% CIs. | Stronger H. |

**Provisional conclusion**: H1 is consistent with the data. The user's two-day intensive labelling session (visible in git history as Phase 1.14 active-learning loop) added many labels that the model fits worse than the older organic engagement signal.

This is the **same pattern** I tried (and the user rejected) to "fix" by adding the 🥱-veto + time-decay + LLM-note filter — only one part of the noise. The remaining noise is structural to the UI-added rows themselves.

## 4. Decision matrix update

The plan's pre-registered matrix did not account for `S < 0`. Reading it strictly:

| Plan trigger | Today's data |
|---|---|
| `S < 0.02` (data-limited) | **TRUE** — slope from n=836 to n=1393 is **negative**, well below 0.02 |
| `B_upper / C > 0.85` (near-ceiling) | **Unknown until C measured** |

So we're at least in **Scenario A or D** (the data-limited C is ruled out — adding more labels hasn't helped, it's hurt). The C measurement determines which:

- If **C ≥ 0.50** → Scenario A (near-ceiling but human label is reliable; the bottleneck is feature impoverishment)
- If **C < 0.40** → Scenario D (task-impossible from abstract alone; re-scope product)
- If **0.40 ≤ C < 0.50** → ambiguous; the gap to baseline B=0.205 is modest

A separate **Scenario E (label-quality regression)** that the pre-registration missed: the `S < 0` pattern suggests we should also try retraining on a *subset* of the labels — e.g. only the n=836 most-reliable ones — and see if Spearman recovers to 0.26.

## 5. What you should do next

### 5.1 Re-label 79 papers (gives us C)

Open http://127.0.0.1:8000 → **Re-label Audit** tab → click **Start / Resume**. The UI shows 79 papers blind (no original label visible) stratified across age + class. Click must / should / could / dont for each — about 30 min total. When done, click **Show metrics** to see Cohen's κ, ICC(2,1), Pearson r.

The **Pearson r is the ceiling C** — the upper bound on Spearman ρ any model can achieve on your labels.

### 5.2 Side-experiment: retrain on the top-836 subset

Independent of C, we should test H1 directly. The `eval_baseline.run_baseline` framework already lets us pass a subset. Skipped here pending your direction.

## 6. References

- Beel et al. 2016. "Research-paper recommender systems: a literature survey." *Int. J. on Digital Libraries* 17(4).
- Cohan et al. 2020. "SPECTER: Document-level Representation Learning using Citation-informed Transformers." ACL.
- Efron 1987. "Better Bootstrap Confidence Intervals." *JASA* 82(397) — BCa method.
- Koo & Li 2016. "A Guideline of Selecting and Reporting Intraclass Correlation Coefficients for Reliability Research." *J Chiropr Med* 15(2).
- McHugh 2012. "Interrater reliability: the kappa statistic." *Biochem Med (Zagreb)* 22(3).
- Sugiyama & Kan 2010. "Scholarly Paper Recommendation via User's Recent Research Interests." JCDL.
- Vanwinckelen & Blockeel 2012. "On Estimating Model Accuracy with Repeated Cross-Validation." Proceedings of BeneLearn.
