# services/faithbench — faithfulness mini-benchmark

Validates that the local **deep_review** model answers questions about a paper
and writes review claims **faithfully** (grounded in the paper, no
hallucination) — the go/no-go gate before building the Library-tab deep-review
browser + paper Q&A feature.

```
                      build                     run                      judge                report
Zotero PDFs ──► [_corpus]──► papers/<key>-<sha>.txt │                      │                    │
                   │         (frozen + sha256)  │                         │                    │
builder LLM ──► [_build_qa] ─► benchmark_vN.jsonl ─► [_runner] ──► responses.jsonl ─► [_judge] ─► judgments.jsonl ─► [_stats/_report]
(remote API)       │  QA span-verified + traps     │  model under test       │  hard ladder        │   report.{json,md}
                   └─► benchmark_vN.review.csv     │  (deep_review stage,    │  then pinned        └─► faithbench-runs.jsonl
                                                   │   full_text|retrieval)  │  LLM judge
                                          [_build_claims] digest→claims      │  (residual only)
```

## Stages (all resumable, all artifacts under `data/faithbench/`)

1. **build** (`_corpus` + `_build_qa`): select papers with local PDFs, extract
   and **freeze** their text (`papers/<key>-<sha256>.txt`, sha256 recorded — later
   extraction drift becomes a `HARNESS_FAULT`, never a model failure). The
   builder LLM (remote judge endpoint — the model under test must not write
   its own exam) proposes extractive QA; the **deterministic keep-gate** keeps
   only spans verbatim-anchorable in the frozen text. Traps are verified QA
   from *other* papers whose answer is absent by a lexical proxy. The generated
   `benchmark_vN.review.csv` starts unapproved; every QA/trap row must be checked
   and marked `approved=yes` before the QA track can run. The exact approved CSV
   hash is bound into the run manifest. Benchmarks are immutable: rebuild ⇒ `v<N+1>`.
   New extractions never overwrite earlier text versions; publication uses the
   shared atomic writer. Existing `papers/<key>.txt` files remain readable only
   after SHA verification and are never rewritten on read. Keys, hashes and
   resolved paths are validated; symlink escapes fail. A corrupt hash-named file
   is an error, not a reason to substitute a legacy file. This guarantees atomic
   visibility, not power-loss durability or protection from a hostile local
   process racing filesystem changes.
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
   denominator. Equivalence judgments require an explicit JSON boolean:
   missing/null/string/numeric/container values are `JUDGE_ERROR`, never coerced
   into a pass or a model rejection. This uses the existing tri-state error
   boundary, without adding a retry for a structurally invalid verdict. A row
   whose paper key is unknown/empty (rebuilt or edited
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
   "paper+goals"`). Re-judging with `--force` preserves complete response rows —
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

Successful QA retrieval and claim-judge retrieval obey the same paper-context
character cap as their full-text paths, including `\n\n[...]\n\n` separators.
The shared `_corpus` clipper retains ranked prefixes and clips the final fragment;
it also supports review selection's document-order assembly. No separator-only
fragment is emitted. Existing no-hit behavior and the claim judge's one full-text
second pass are unchanged. This is a paper-context cap, not a token/whole-prompt
or process-memory limit. Regression tests capture the actual prompts in production
Q&A, benchmark Q&A and both claim-judge templates.

```
zotero-summarizer faithbench build  [--n-papers 8] [--qa-per-paper 5] [--traps-per-paper 2] ...
zotero-summarizer faithbench run    [--benchmark latest] [--run-id ID] [--runs 1]
                                    [--conditions full_text,retrieval] [--tracks qa,claims] ...
zotero-summarizer faithbench judge  --run-id ID [--judge-model ...] [--force]
zotero-summarizer faithbench report --run-id ID
```

Defaults: 8 papers × (5 QA + 2 traps) × 2 conditions ≈ 112 local calls ≈
2–3.5 h on the MLX 35B; `--runs 3` is an overnight job.

Build requests require at least two distinct papers, positive QA/trap budgets,
and a generated cohort containing both answerable QA and traps. No-trap builds
fail before benchmark publication; a legacy one-cohort benchmark cannot run the
QA track. This checks cohort presence, **not semantic unanswerability**: mandatory
human approval is the semantic gate for the cross-paper absence proxy.
`RunOptions` rejects non-positive or non-integer budgets, empty/unknown/duplicate
conditions or tracks, and QA limits on claims-only runs. CLI parses these options
once, before Settings and provider construction. Omitted `--limit` means all QA;
an explicit positive smoke limit remains supported. If the selected/validated
trials have no trap or answerable denominator, those rates are JSON `null` /
Markdown `N/A (unmeasured)`, including the master headline, never a measured 0%.

QA generation fully covers papers up to 18,000 characters using up to three
6,000-character windows; longer papers use bounded evenly-spaced samples including
both ends. Three windows are an explicit cost ceiling, not a promise to inspect
every character of a long paper. The short-paper suffix and integer-spacing
end-point omissions are removed without increasing that ceiling.

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
   Run/judge recover only an unterminated final JSONL row: a damaged original is
   retained as `<ledger>.interrupted-<uuid>`, completed rows are preserved, and
   the repaired journal is published atomically. A complete last row lacking a
   newline is retained and framed before any append. Interior corruption still
   raises. Reports use the strict reader, not a repair-on-read fallback.
   Manifests are atomic JSON snapshots; missing manifests beside existing trials
   are rejected, never recreated with a possibly different model/configuration.
   A legacy corrupt manifest requires restoration from trusted evidence. Recovery
   assumes one writer per run directory and does not promise power-loss durability.
3. **Judge changes cost zero model-under-test time** — `judge --force`
   re-judges existing responses (complete rows are preserved), so judge-model
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
| `_corpus.py` | paper selection/extraction, frozen text, `normalize_text` (shared by gate AND judge), chunking, per-paper BM25 (word `tokenize` reused from `storage.corpus_bm25`); `PaperSubstrate` bundles a paper's text+norm+chunk-index (the trio `_judge`/`_runner` cache per paper) |
| `_build_qa.py` | QA generation + deterministic span keep-gate + traps |
| `_build_claims.py` | digest (reuses `library.quality_review`) + claim decomposition |
| `_runner.py` | trial execution, append-only responses, resume + manifest guard; `run_benchmark`'s 7 execution knobs (conditions/tracks/runs/limit/retry_errors/serial/max_workers) are bundled in `RunOptions`, and the frozen run artifacts (meta/items/papers_dir/paths) in `RunInputs` — the same bundle `judge_run` takes, so runner and judge can never disagree on what a "run" is |
| `_judge.py` | hard ladder + LLM escalation, claim support judging |
| `_stats.py` | `calculate_statistics` — single source of truth |
| `_report.py` | report.json / report.md rendering + master log |

Design provenance: ARE/Gaia2 agent-benchmark patterns (hard-before-soft judge,
pinned judge model, typed failure taxonomy, run-level variance).

Numeric QA uses exact decimal arithmetic only for complete scalar answers
(optional sign, correctly grouped thousands, decimal fraction). Integers require
exact equality; fractional values retain the configured tolerance. Units, ranges,
exponents, or additional prose go to the existing equivalence judge unless the
answer exactly matches the gold text. Numeric QA bypasses punctuation-stripping
normalization and prefix-containment shortcuts, preserving signs and large integers.

Artifact identity is checked on the bytes actually parsed: run binds its initial
file hash to that read; judge/report require the manifest's full SHA-256. A single
leading metadata header and unique paper/item identities are mandatory. Run checks
each item's paper SHA, literal gold offsets, stored evidence sentence and trap
source against frozen text before model work. Judge records per-item violations
as `HARNESS_FAULT`, never as a model rejection. It uses the item's explicit paper
key rather than inferring a different target from the item ID.

`build_report` now takes only run paths and the faithbench root; it loads its own
manifest, benchmark and verified frozen text, instead of accepting three redundant
and potentially inconsistent arguments. Diagnostic fault judgments remain available
in JSONL, but publishing a report requires intact current ground truth, complete
configured response coverage, and a current judgment for every response/claim.
Legacy limited runs without a recorded limit cannot be assumed complete.

Judgments bind to the canonical response hash and to their benchmark/judge/context
snapshot. Changed responses are re-judged; a changed judge/configuration or substrate
state invalidates prior cached verdicts. Historical attempts remain in JSONL, while
reports use the latest matching verdict per trial/claim and reject mixed judging
contexts. A pre-existing judgment without those bindings must be re-judged before
reporting. No historical artifact is silently upgraded or overwritten on read.

An empty/malformed successful claims response produces one explicit failed trial,
not zero judgments. Decomposition rejects empty snippets, empty results and invalid
cached claim rows before returning a successful claims trial or publishing a new
empty cache. The existing per-trial exception taxonomy remains in use.

Claims decomposition includes all 21 claim-bearing digest fields, including the
executive summary, findings, methods, limitations, reading rationale/targets,
impact and structured parameters (JSON preserves nested values and boolean false).
A schema-coverage test requires an explicit policy for every PaperDigest field;
only grading dimensions, categorical decisions and runtime metadata are excluded.
The v3 decomposition-cache namespace leaves the old six-field artifacts intact
but never reuses them. This removes field omission; decomposition and semantic
support judgments still depend on the judge and are not proof of completeness.

Run manifests now guard resolved under-test/decomposer profiles (including actual
endpoint and generation parameters), the full GoalsConfig snapshot, request
timeout, prompt/schema and generation-source hashes, research goals, semantic
benchmark content and execution options. CLI checks this identity before client
construction; direct `run_benchmark` callers must first publish a valid manifest
and supply explicit resolved generation profiles. The runner rechecks live inputs
and benchmark bytes before journal repair, cache reuse or model calls. Missing
legacy identities refuse resume without upgrading/overwriting old artifacts.
Changed config/source may conservatively require a new run even if irrelevant to
a particular trial. This does not pin remotely mutable model weights or replace
the frozen dataset's separate human-review requirement.
