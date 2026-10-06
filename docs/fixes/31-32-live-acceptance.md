# #31 / #32 — live acceptance, 2026-10-06

```text
#32 deployed kather/sota → fresh original extraction → generation → verifier → quality
                         └─ local decoded capture → causal diagnosis → regression
#31 six frozen queries  → real source observations → common frozen pool + BAAI scores
                         └─ arm-blind Astra proxy labels → deterministic A/B grading
```

Baseline: `402956751d21feca7aecbd1d8b5c3d56f25616f4`. Deployed routing was read through the running app's config endpoint: `kather/sota`, not the developer config's GLM alias. Production originals/config/server were not modified by the experiments. Local captures/configuration are private, gitignored, owner-only; the reports below publish aggregates, not paper text or credentials.

## #32: actual new incident and representative batch

A newly initiated run of `8T8LR3GI` extracted 68,493 characters, selected 60,000 and called the real configured provider with thinking requested off. Generator and bounded correction both completed, but numeric preflight rejected reported positive improvements. Source and selected source explicitly contain `3.3%`, `1.5%` and `83.9 (+9.6)`: preserving optional `+` in string-token identity falsely rejected `+3.3` versus `3.3` and `9.6` versus `+9.6`.

```text
reported positive number → optional-sign string mismatch → false rejection
                         → bounded correction → same mismatch
fix: remove optional '+' symmetrically; retain '-' / magnitude / source / semantic checks
```

Regression receipt: **5 failed / 4 passed before**; the nine new cases pass after normalization, including sign reversal, changed values, endpoint-only arithmetic and downstream semantic rejection. This is not an arithmetic exemption, item whitelist, retry increase or verifier bypass. The original captured candidates pass numeric preflight after the fix; live `8T8LR3GI` then completed digest, real verification and quality evaluation.

Representative real-provider batch: three genuine retained PDFs freshly extracted, one genuine retained denial PDF, one empty-extraction control; identical inputs/config/budgets in isolated baseline/fix roots. No placeholder counts as a usable paper review.

| Input class | Baseline | Fix |
|---|---|---|
| Issue paper | Complete in representative rerun; initial fixed-tree run reproduced numeric defect | Complete after numeric repair |
| Genuine paper 2 | Digest failed | Verifier contract unavailable; no success/grade published |
| Genuine paper 3 | Complete | Complete |
| Denial PDF, 210 chars | **Six calls, false generic-empirical D/flag** | Source admission reject, **zero calls**, no science grade |
| Empty extraction | Reject, zero calls | Reject, zero calls |

Genuine usable completions are **2/3 in each representative arm**, not 3/5 versus 2/5 by counting the denial. The before/after causal incident is the initial failed numeric capture versus the successful repaired capture. The remaining real verifier-unavailable example demonstrates typed abstention, not a successful review. This does not establish the missing historical generation input or an observed historical HTTP 403. Capture retains actual decoded values at client boundaries, not purported raw transport bodies or inferred usage.

## #31: user-approved Astra proxy comparison

The user explicitly substituted **Astra in the role of a human**. Judge identity is `openai-codex/gpt-6-astra`; requested thinking off, provider reported 10 internal reasoning tokens on one call and zero on the other five. This is a model/user-proxy pilot, **not native human validation**. Astra saw only raw query, user need, title, abstract and normalized-text hash; no arm, query plan, filters, ranking, source flags or BAAI score. Labels were frozen before formal grading/replay. Collection and ungraded ranking preparation necessarily preceded corpus annotation.

Six public concept-learning tasks cover device verification/validation, broad regulatory overview, historical guidance, complementary facets, explicit literal/exclusion controls, and a strict clinical publication-type control. Real planner: six `kather/sota` calls. Real cached production BAAI cross-encoder: 325 family rows, 302 query-text identities / 268 globally distinct texts, CPU guard peak 2.97 GiB, exited normally. Grade joins normalized TEXT hashes, not positions/DOI label substitution; one unmatched text variant stays ungraded.

| Macro mean / median | Baseline | Final fix |
|---|---:|---:|
| Shared-pool relevant P@10 | .4667 / .6000 | **.6833 / .7000** |
| Shared-pool useful P@10 | .4667 / .6000 | **.5167 / .6500** |
| Captured-retrieval relevant P@10 | .2667 / .2500 | **.6000 / .6500** |
| Captured-retrieval useful P@10 | .2667 / .2500 | **.4333 / .5000** |

P@10 uses ten slots, with missing results zero; relevance and usefulness are separate Astra labels. Recall concerns **observed pooled gold**, not full-corpus recall. Shared-pool candidate recall=1 is only a construction floor. Initial observed candidate recall mean/median was .5007/.5000 baseline versus .6012/.6136 fix; allocation only updates type metadata, not this retrieval measure.

| Shared-pool relevant P@10 by capability | Baseline | Final fix |
|---|---:|---:|
| Verification/validation | .0 | .7 |
| Regulatory overview | .9 | 1.0 |
| Historical guidance | .0 | .9 |
| Complementary facets | .6 | .7 |
| Explicit phrase/exclusion | .6 | .6 |
| Strict clinical type | .7, title-based eligibility | .2, authoritative type required |

The first pilot exposed FIFO metadata starvation. A generic, independently reviewed title hint now prioritizes an **authoritative exact-DOI lookup**, never eligibility. Budgets remain five/default and fifteen/max. New allocation finds one certified type among 37 captured candidates and two among 58 shared families; other candidates remain unattempted/unknown. The known JAMA record is still beyond the bounded queue. No proxy labels or document-specific IDs affect this allocation.

Trade-offs are retained: shared overview useful P@10 .9→.7; verification useful P@10 .1 despite relevant .7; strict clinical type loses many unconfirmed results rather than certifying from titles. These are not claims of universal improvement or threshold/reranker promotion. Broader original-text answer sufficiency and ranking remain #30/#33.

Original live collection: baseline 36 requests/8 HTTP429, fix 41/12 HTTP429; retrieval comparisons are confounded by backend availability. Separate new **ungraded** timing collection: baseline per-query mean/median **1.3851/1.2403 s**, fix **2.0278/1.5627 s**, 36/41 requests and 6/6 HTTP429. New-pool documents are not silently assigned old labels. Recovery follow-up made ten requests across two independent five-request scopes, zero HTTP429, total request time 2.4895 s. No single production run has a ten-request recovery budget.

## Receipts and limits

Canonical private artifact root: `data/fix-32-31/`.

- `live32/{probe-valid,after-valid,batch-live}.log`, per-arm `runs/*/*/{result,call-*,verification_input-*}.json`, fixed native attempts: source identities, actual response/correction lineage and stage counts.
- `live31/run-final-typepriority/{metrics,allocation_audit,metadata_http,metadata_queries,validation}.json`, `report.md`; original `run-final-006` retained unchanged.
- `live31/run-timing-typepriority/{timing_summary,*_http,*_collect}.json`; missing initial granular timestamps remain unavailable.
- `astra/labels001/manifest.json`, complete blinded input/label hashes and provider events; no labels reach generation/ranking.
- Five deterministic grader tests pass; sixteen recovery-allocation cases and nine positive-sign cases added. Full canonical verification is recorded in the final PR evidence.

Official guidance/handbook and cross-source completeness are not measured. OpenAlex/OpenReview/library were not wired in this pilot. Failed/unattempted providers are not asserted empty/successful. Subsequent changes must use untouched queries/windows; this pilot is not a tuning set for #30/#33.

Reference: ARE `are/simulation/validation/configs.py`, `tool_judge.py`, `benchmark/report_stats.py`: close pinned judge, hard identity checks, frozen labels and explicit per-capability outcomes; diverge event-simulation for actual provider observations; defer multi-run confidence/full-corpus sampling. Codex `codex-rs/core/src/compact.rs`, `protocol/src/protocol.rs`, `core/src/config/otel.rs`: original-source identity and default-off sensitive capture; semantic grounding remains this app's verifier, not a claimed Codex capability.

Simplification: existing dispatcher, eligibility, ranking and quota reused. No new agent loop, dashboard, label source, permanent benchmark framework, threshold tuning, dependency or allowlist change.
