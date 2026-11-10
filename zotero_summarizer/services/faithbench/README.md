# services/faithbench — faithfulness mini-benchmark

Contract for changing this package. It checks that the local **deep_review** model answers questions about a paper and
writes review claims faithfully (grounded in the paper, no hallucination). Everything lives under `data/faithbench/`
and every stage is resumable. CLI: `zotero-summarizer faithbench {build,run,judge,report,interpretation}`
(`--help` lists options).

```
Zotero PDFs ─► [_corpus] ─► papers/<key>-<sha>.txt (frozen + sha256)
builder LLM ─► [_build_qa] ─► benchmark_vN.jsonl + benchmark_vN.review.csv (starts unapproved)      (remote API)
                                      │
                 [_runner] ─► responses.jsonl  (model under test, deep_review stage; full_text | retrieval)
                 [_build_claims]: digest → atomic claims (remote decomposer, cached by digest sha)
                                      ▼
                 [_judge] hard ladder, then pinned LLM judge for the residual ─► judgments.jsonl
                                      ▼
                 [_stats]/[_report] ─► report.{json,md} + faithbench-runs.jsonl headline
```

## Files

| file | responsibility |
|---|---|
| `__init__.py` | lazy exports resolving to the defining objects (`ANSWER_PROMPT`, `PaperChunkIndex`, `RunInputs`, `RunOptions`, `RunPaths`, `answer_with_retry`, `build_items`, `build_report`, `judge_run`, `run_benchmark`, `select_papers`) |
| `_constants.py` | pinned `DEFAULT_JUDGE_MODEL`, env-var names, window/chunk/top-k/tolerance constants, `resolve_qa_max_builder_windows` |
| `_options.py` | pure `RunOptions` validation shared by pre-Settings CLI admission and the runner (imports no corpus/model/execution module) |
| `_judgment.py` | tri-state `Judgment`; closed `FailureReason`/`JudgeMethod` enums |
| `_dataset.py` | benchmark schemas, versioned JSONL persistence, review CSV, item↔frozen-text binding |
| `_corpus.py` | paper selection/extraction, frozen text, `normalize_text` (shared by gate and judge), `chunk_spans`/`chunk_text`, per-paper BM25 `PaperChunkIndex` (`tokenize` from `storage.corpus_bm25`), `PaperSubstrate` (text + norm + index per paper), shared context clipper |
| `_build_qa.py` | QA generation, deterministic span keep-gate, traps |
| `_build_claims.py` | digest (reuses `library.quality_review.assess_digest`) + claim decomposition (`CLAIM_FIELDS`) |
| `_runner.py` | trial execution, append-only responses, resume, manifest guard, `ANSWER_PROMPT`/`parse_answer`; `RunInputs` = frozen run artifacts shared with the judge |
| `_judge.py` | hard ladder, LLM escalation, claim-support judging |
| `_stats.py` | `calculate_statistics`: the single source of truth for every reported number |
| `_report.py` | `build_report`, report.json/report.md rendering, master log |
| `_interpretation_inputs.py` · `_interpretation.py` · `_interpretation_report.py` | opt-in A/B/C interpretation experiment: recipe admission + frozen preparation · immutable attempts · blinded packets and paper-paired decisions |

## Shared invariants

- Every builder, answer, decomposition and judge prompt treats paper text, generated claims/questions and candidate
  answers as escaped untrusted data (`UNTRUSTED_INPUT_RULE`); embedded directives cannot close the boundary.
- `_corpus.chunk_spans` returns the exact stripped-window original coordinates (repeated occurrences stay
  distinct); `chunk_text` and `PaperChunkIndex` project the same windows. Search question evidence reuses these
  coordinates, `ANSWER_PROMPT` and `parse_answer`; that product verifier is not a benchmark oracle.
- `parse_answer` admits answer/quote strings only if they encode as UTF-8 (escaped lone surrogates are invalid
  output, propagate without model repair/replay); only malformed JSON gets the single existing repair.
- The paper-context cap (`full_text` 60k chars, same as the production digest; `retrieval` top-6 BM25 chunks of 1,200
  chars; claim judge top-8 chunks) applies to QA retrieval, claim-judge retrieval and full-text paths, including
  the `\n\n[...]\n\n` separators. The shared `_clip_chunks` keeps ranked prefixes, clips the final fragment and emits no
  separator-only fragment. It is not a token, whole-prompt or process-memory limit.
- Artifact identity is checked on the bytes actually parsed (run binds its initial file hash to that read; judge and
  report require the manifest's full SHA-256). One leading metadata header and unique paper/item identities are
  mandatory. Run checks each item's paper SHA, literal gold offsets, evidence sentence and trap source against the
  frozen text before model work; the judge records per-item violations as `HARNESS_FAULT` (never a model
  rejection) and uses the item's explicit paper key.
- Atomic visibility, not power-loss durability, is the guarantee; recovery assumes one writer per run directory.

## Build (`_corpus`, `_build_qa`, `_dataset`)

- Select papers with local PDFs, extract once and freeze `papers/<key>-<sha256>.txt` (sha recorded). Later
  extraction drift is a `HARNESS_FAULT`, never a model failure. New extractions never overwrite earlier versions.
  Legacy `papers/<key>.txt` stays readable only after SHA verification and is never rewritten; keys, hashes and
  resolved paths are validated, symlink escapes fail, and a corrupt hash-named file is an error (no substitution).
- The builder LLM is the remote judge endpoint: the model under test never writes its own exam. A candidate enters the
  benchmark only if the deterministic keep-gate finds its span verbatim in the frozen text (`MAX_GOLD_SPAN_CHARS`).
  Traps are verified QA from other papers whose answer is absent by a lexical proxy.
- Source windows: contiguous, non-overlapping 6,000-char windows covering every character. Per-paper limit
  `--max-builder-windows` (1–256, default 32) > shell `ZS_FAITHBENCH_QA_MAX_BUILDER_WINDOWS` > project `.env` >
  default; resolved and validated before Settings/provider construction, recorded in benchmark metadata. Every window
  is a builder request. `build_items` checks all selected papers against the limit before any builder call.
- Requests need ≥2 distinct papers, positive QA/trap budgets and a generated cohort with both answerable QA and traps.
  This checks cohort presence, not semantic unanswerability: human approval is the semantic gate for the absence proxy.
- `benchmark_vN.review.csv` starts unapproved; every QA/trap row must be `approved=yes` before the QA track runs; the
  exact approved CSV hash is bound into the run manifest. Benchmarks are immutable (rebuild ⇒ `v<N+1>`). Both files
  stage completely; the CSV publishes first and the benchmark is linked last as the version commit marker, so a
  failed build leaves no readable partial benchmark and consumes no version.

## Run (`_runner`, `_build_claims`)

- QA conditions: `full_text` and `retrieval` (lexical BM25 only; the product pipeline is hybrid, an extension point).
  The claims track regenerates the `PaperDigest` per `(paper, run)` (claims are the model's output, so they are not
  frozen at build time), decomposes it with the remote decomposer, and tags each claim with its source digest field
  (token overlap is only the fallback for untagged entries).
- `responses.jsonl` holds one long-form row per trial; resume skips done `(item_id, condition, run_number)`; the last
  row per key wins (`--retry-errors` appends fresh attempts). Run/judge recover only an unterminated final JSONL row (a
  damaged original is kept as `<ledger>.interrupted-<uuid>`, completed rows preserved, repaired journal published
  atomically); a complete last row lacking a newline is framed before any append; interior corruption raises; reports
  use the strict reader.
- `manifest.json` is an atomic JSON snapshot; a missing manifest beside existing trials is rejected, never recreated.
  It guards resolved under-test/decomposer profiles (endpoint, generation parameters), the full `GoalsConfig`
  snapshot, request timeout, prompt/schema/generation-source hashes, `research_goals` (the digest prompt and the
  judge use the same snapshot), benchmark semantic content and execution options. The CLI checks this identity
  before client construction; direct `run_benchmark` callers first publish a valid manifest and pass resolved
  generation profiles. The runner rechecks live inputs and benchmark bytes before journal repair, cache reuse or model
  calls. Missing legacy identities refuse resume (no upgrade/overwrite); changed config/source may conservatively
  force a new run. Remotely mutable weights are not pinned.
- Claims decomposition covers all 21 `CLAIM_FIELDS` (grading dimensions, categorical decisions and runtime metadata
  are excluded; every `PaperDigest` field needs an explicit policy). It rejects empty snippets/results and invalid
  cached rows; an empty/malformed claims response is one explicit failed trial, never zero judgments. The cache is
  `runs/<id>/claims_cache/claims-v3-<digest>.json` (atomic writes; corrupt entries discarded and rebuilt; old
  six-field artifacts never reused); the manifest pins the decomposer profile.
- `RunOptions`: positive integer budgets, nonempty/known/unique conditions and tracks, no QA limits on claims-only
  runs. Omitted `--limit` means all QA; `--limit N` takes the first N items deterministically.

## Judge (`_judge`, `_judgment`)

- Hard-before-soft ladder: model error → malformed → trap rule → abstain rule → normalized exact → numeric tolerance →
  capped span containment (`MAX_CONTAINMENT_ANSWER_CHARS`); only the residual band reaches the pinned LLM judge
  (`_constants.DEFAULT_JUDGE_MODEL`, never a floating alias; never the model family under test).
- Verdicts are tri-state; `JUDGE_ERROR` and `HARNESS_FAULT` leave the accuracy denominator. Equivalence judgments
  require an explicit JSON boolean (missing/null/string/number/container ⇒ `JUDGE_ERROR`, no extra retry). A row with
  an unknown/empty paper key is a `HARNESS_FAULT` in both tracks.
- Numeric QA uses exact decimal arithmetic only for complete scalar answers (optional sign, grouped thousands,
  decimal fraction): integers need exact equality, fractions keep `NUMERIC_REL_TOL`; units, ranges, exponents or
  prose go to the equivalence judge unless text matches gold exactly. Numeric QA skips punctuation-stripping
  normalization and prefix containment.
- Claims: verbatim-normalized substring ⇒ pass; else support judge over top-8 chunks, plus one full-text second pass
  on `not_enough_info`. `read_why` claims are judged against paper text plus the run's `research_goals` (manifest
  snapshot; older runs fall back to current config) and carry `extra.judged_against="paper+goals"`: goals license
  vocabulary only, never facts, so a paper that never engages a goal topic stays `unsupported`.
- Judgments bind to the canonical response hash and the benchmark/judge/context snapshot. Changed responses are
  re-judged; a changed judge/configuration/substrate invalidates cached verdicts. Reports use the latest matching
  verdict per trial/claim and reject mixed judging contexts; unbound legacy judgments must be re-judged. Nothing
  upgrades or overwrites a historical artifact on read. `judge --force` re-judges and preserves complete response rows.

## Report (`_stats`, `_report`)

- `calculate_statistics` feeds every number in report.json and report.md. A trial is validated when its judgment is
  `True`/`False`. QA accuracy and claim support are reported as mean and median; STD/SEM over run-level means
  (ddof=1, 0.0 when runs ≤ 1); Pass@k/Pass^k over complete cohorts when runs > 1; trap hallucination rate;
  abstention precision/recall; per-field claim support; judge-escalation fraction; latency percentiles (summed
  per-trial latency is labeled cumulative trial time, not wall time).
- A rate with no trap or answerable denominator is JSON `null` / Markdown `N/A (unmeasured)` (headline included).
  `build_report(paths=, faithbench_dir=)` loads its own manifest, benchmark and verified frozen text. Publishing requires intact
  ground truth, complete configured response coverage (legacy limited runs without a recorded limit are not assumed
  complete) and a current judgment for every response/claim.
- The headline appends once per run ID to `faithbench-runs.jsonl` under a cross-process file lock; rows are never
  rewritten (run_id, git commit, benchmark sha, `*_accuracy`/`*_accuracy_median`, `claims_support_rate`/`_median`;
  mirrors `classifier-runs.jsonl`).

## Interpretation experiment (opt-in)

`zotero-summarizer faithbench interpretation [--recipe R] [--run --capture-local] [--run-id ID [--labels L]]`: inspect
by default; `--run` requires `--capture-local` consent; existing-run scoring/export cannot request generation.

- `_interpretation_inputs.inspect` is read-only and admits a recipe over frozen benchmarks, exact approved references
  and source texts: four answerability/stance cells per held-out paper, clinical/agent coverage, nonempty
  family/version/time metadata, a separate earlier tuning cohort, `minimum_papers ≥ 20`. Limits and stricter-only
  thresholds are validated, not coerced; actual approval rows are checked (not only the CSV hash); exact duplicate
  evaluation extractions refuse admission even under different family names. Source, run/export parents and reopened
  run paths reject symlinks and project escapes before I/O.
- `prepare` freezes a private attempt and verifies its own approved reference before any provider construction;
  existing runs/caches are untouched. A client-construction failure after a published plan fails every assigned
  review/question with the original exception type (no fabricated output); consent, cohort admission and frozen
  identities precede construction, which does not repeat per assignment. Model/thinking resolution belongs to
  strategy profiles and `RuntimeState` (no second client cache).
- Returned input/plan/record JSON is decoded from the bytes already checked against the manifest. Saved optional
  acceptance must match its retained invocation tape on reopening. Declared runtime conditions, unknown physical
  retries and mutable remote weights are distinct from observed completion data.
- Blind packets and label templates follow independent random IDs, never assignment order; card judgments use a
  separate source/check/passage packet; review/QA packets omit cards. Known refusal gates do not depend on unrelated
  missing judgments; token and timing caps need independent evidence (either known breach refuses, success needs
  both); point thresholds use exact assigned-count fractions and exact token counts/decimal latency, with no epsilon.
  Reports count actual whole-card QA exposures (zero exposure never recommends a card pilot); a report with no
  non-null human score stays inconclusive, and assigned errors alone never produce a reject. Scientific prose is never
  graded by keywords.
