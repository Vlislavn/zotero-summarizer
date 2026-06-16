# services/faithbench — faithfulness mini-benchmark

Validates that the local **deep_review** model answers questions about a paper
and writes review claims **faithfully** (grounded in the paper, no
hallucination) — the go/no-go gate before building the Library-tab deep-review
browser + paper Q&A feature.

```
                      build                     run                      judge                report
Zotero PDFs ──► [_corpus]──► papers/<key>.txt   │                         │                    │
                   │         (frozen + sha256)  │                         │                    │
builder LLM ──► [_build_qa] ─► benchmark_vN.jsonl ─► [_runner] ──► responses.jsonl ─► [_judge] ─► judgments.jsonl ─► [_stats/_report]
(remote,           │  QA span-verified + traps     │  model under test       │  hard ladder        │   report.{json,md}
 api.kather.ai)    └─► benchmark_vN.review.csv     │  (deep_review stage,    │  then pinned        └─► faithbench-runs.jsonl
                                                   │   full_text|retrieval)  │  LLM judge
                                          [_build_claims] digest→claims      │  (residual only)
```

## Stages (all resumable, all artifacts under `data/faithbench/`)

1. **build** (`_corpus` + `_build_qa`): select papers with local PDFs, extract
   and **freeze** their text (`papers/<key>.txt`, sha256 recorded — later
   extraction drift becomes a `HARNESS_FAULT`, never a model failure). The
   builder LLM (remote judge endpoint — the model under test must not write
   its own exam) proposes extractive QA; the **deterministic keep-gate** keeps
   only spans verbatim-anchorable in the frozen text. Traps are verified QA
   from *other* papers whose answer is provably absent (proxy check; eyeball
   `benchmark_vN.review.csv`). Benchmarks are immutable: rebuild ⇒ `v<N+1>`.
2. **run** (`_runner` + `_build_claims`): asks the deep_review-stage model.
   QA conditions: `full_text` (60k-char cap, same as production digest) and
   `retrieval` (top-6 BM25 chunks — lexical-only v1; the product pipeline is
   hybrid, extension point here). Claims track regenerates the `PaperDigest`
   per `(paper, run)` via `quality_review.assess_digest` (claims are the
   model's output — freezing them at build time would benchmark a stale
   artifact) and decomposes it into atomic claims (remote LLM, cached by
   digest sha), each **tagged by the decomposer with its source digest field**
   (token-overlap attribution is only the fallback for untagged entries — the
   tag routes judging and the per-field breakdown). One long-form row per
   trial in `responses.jsonl`; resume skips done
   `(item_id, condition, run_number)` keys; **last row per key wins**
   (`--retry-errors` appends fresh attempts). `manifest.json` refuses a resume
   with different model/benchmark/conditions/runs and snapshots
   `research_goals` (the digest prompt is conditioned on them; the judge must
   use the same text even if `goals.yaml` later changes).
3. **judge** (`_judge` + `_judgment`): **hard-before-soft ladder** — model
   error → malformed → trap rule → abstain rule → normalized exact → numeric
   tolerance → capped span containment; only the residual band reaches the
   **pinned** LLM judge (`_constants.DEFAULT_JUDGE_MODEL`, never a floating
   alias). Verdicts are tri-state `Judgment`s with a closed `FailureReason`
   enum; judge failures (`JUDGE_ERROR`) and harness faults leave the accuracy
   denominator. A row whose paper key is unknown/empty (rebuilt or edited
   benchmark) is a `HARNESS_FAULT` in BOTH tracks — never an uncaught
   `KeyError`. Claims: verbatim-normalized substring → free pass; else
   support judge over top-8 chunks with a full-text second pass on
   `not_enough_info`. **`read_why` claims are judged against paper text PLUS
   the run's `research_goals`** (from the manifest snapshot; older runs fall
   back to current config): the field is goal-conditioned, so its claims
   legitimately describe the paper in goal vocabulary ("addresses agent
   autonomy") — but the goals license only the *vocabulary*, never the facts;
   a paper that never engages with a goal topic stays `unsupported`
   (goal-projection hallucination — the v2-baseline failure mode this track
   exists to catch; those judgments carry `extra.judged_against =
   "paper+goals"`). Re-judging with `--force` never touches responses —
   judge-model ablations are free.
4. **report** (`_stats` + `_report`): `calculate_statistics` is the single
   source of truth for *every* number in `report.json` and `report.md`.
   QA accuracy + claim support rate are reported as **mean AND median** over the
   validated set (a few hard items can't masquerade as a uniformly worse model),
   STD/SEM across **run-level means** (ddof=1, 0.0 when runs ≤ 1), Pass@k /
   Pass^k when runs > 1, trap hallucination rate, abstention precision/recall,
   claim support rate per digest field, judge-escalation fraction, latency
   percentiles. Headline appended to `faithbench-runs.jsonl` (run_id + git commit
   + benchmark sha + `*_accuracy`/`*_accuracy_median` + `claims_support_rate`/
   `claims_support_rate_median`; mirrors `classifier-runs.jsonl`).

## CLI

```
zotero-summarizer faithbench build  [--n-papers 8] [--qa-per-paper 5] [--traps-per-paper 2] ...
zotero-summarizer faithbench run    [--benchmark latest] [--run-id ID] [--runs 1]
                                    [--conditions full_text,retrieval] [--tracks qa,claims] ...
zotero-summarizer faithbench judge  --run-id ID [--judge-model ...] [--force]
zotero-summarizer faithbench report --run-id ID
```

Defaults: 8 papers × (5 QA + 2 traps) × 2 conditions ≈ 112 local calls ≈
2–3.5 h on the MLX 35B; `--runs 3` is an overnight job.

## Iterating cheaply (never re-run the full grind)

The expensive thing is the local-model `run`; everything around it is designed
to be re-used:

1. **Dev slice** — iterate prompts/retrieval on a fixed cheap slice, compare
   against the full baseline only when the slice moves:
   `faithbench run --limit 14 --conditions retrieval --runs 1 --run-id dev-<change>`
   (~15 min). One run-id per experiment; `--limit N` takes the first N items
   deterministically, so slices are comparable across run-ids.
2. **Resume is free** — a crashed/interrupted run re-invoked with the same
   `--run-id` skips every completed `(item, condition, run)` trial.
   `--retry-errors` re-attempts only exception rows (last row per key wins).
3. **Judge changes cost zero model-under-test time** — `judge --force`
   re-judges existing responses (responses are never touched), so judge-model
   ablations or hard-ladder tweaks never re-ask the 35B.
4. **Claims decomposition is cached** by digest sha (`runs/<id>/claims_cache/`)
   — re-running the claims track on unchanged digests skips the decomposer.
5. **Benchmark stays frozen** — model/prompt changes never need a rebuild;
   only rebuild (`build` → `v<N+1>`) when you want different papers/questions.
6. **Compare runs** from `data/faithbench/faithbench-runs.jsonl` — one headline
   line per reported run (accuracy per condition, trap hallucination rate,
   claim support), so A/B deltas are a `jq`/`grep` away.

## Module map

| file | role |
|---|---|
| `_constants.py` | pinned `DEFAULT_JUDGE_MODEL` + env-var names + thresholds |
| `_judgment.py` | tri-state `Judgment`, closed `FailureReason`/`JudgeMethod` enums |
| `_dataset.py` | benchmark schemas, versioned JSONL persistence, review CSV |
| `_corpus.py` | paper selection/extraction, frozen text, `normalize_text` (shared by gate AND judge), chunking, per-paper BM25 |
| `_build_qa.py` | QA generation + deterministic span keep-gate + traps |
| `_build_claims.py` | digest (reuses `library.quality_review`) + claim decomposition |
| `_runner.py` | trial execution, append-only responses, resume + manifest guard |
| `_judge.py` | hard ladder + LLM escalation, claim support judging |
| `_stats.py` | `calculate_statistics` — single source of truth |
| `_report.py` | report.json / report.md rendering + master log |

SOTA provenance: ARE/Gaia2 patterns (hard-before-soft judge, pinned judge
model, typed failure taxonomy, run-level variance) — cards under
`~/.claude/skills/sota-pattern-index/knowledge/are/`.
