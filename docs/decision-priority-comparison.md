# Offline reading-priority comparison (part of #38)

```text
frozen title/abstract/goals + separate human-label sidecar
  -> canonical/family/time/development admission + whole-work reservation
  -> qualified native HTTP OR retained terminal bytes + matched ML/LLM artifacts
  -> new Settings.data_dir/priority_comparison/<attempt>/
  -> per-case outcomes + JSON/Markdown report + manifest/ACK
```

This is a complete offline comparison tool, not a completed empirical campaign,
a trained-artifact loader, a model promotion or a replacement of production ranking.
It does not change models, labels, golden CSV, CPU gate, bands, corpus or Zotero.

## One explicit command

```bash
python -m tools.method_validation priority --config /absolute/config.json
python -m tools.method_validation priority --config /absolute/config.json --run
python -m tools.method_validation priority --inspect /absolute/attempt-directory
```

The default validates admission with **zero HTTP and zero writes**. `--run` consents
to the selected new private artifact directory and any configured qualified native
requests. It never starts a server, loads weights, resolves credentials, trains,
retries, downloads or runs a baseline constructor. `--inspect` only validates an
existing acknowledged artifact. It cannot resume/reuse/overwrite attempts or infer
success after a writer exception. There is no automatic app integration.

Use the same installed Python environment as the project. Configuration is strict
JSON, at most 1 MiB. `ZS_PRIORITY_OVERRIDES` is an explicit JSON-object override;
malformed values fail. `ZS_SOURCE_WORK_SAFETY_MS` caps configured wall safety
(default 3,600,000; maximum 7,200,000 ms). Work is limited by case, outcome,
call/forward-pass and cumulative snapshot-byte budgets, not wall time alone.
Live requests require public-input consent, a separately installed external
wall/memory guard and qualified existing endpoint/model/runtime/checkpoint/licence/
context declarations. These declarations are inputs, not independently attested
proof of endpoint readiness or physical passes. Do not use this command to launch
an uncoordinated local model on a memory-constrained Mac.

## Frozen inputs and grader firewall

The canonical public schema/API is
[PRIORITY-INTERFACES.md](../tools/method_validation/PRIORITY-INTERFACES.md).
All file references have an explicit absolute `path` and exact `sha256`.

- `inputs` contains `papers` and optional `recordings`. Each paper has `paper_id`,
  canonical `family`, `canonical_ids`, `title`, `abstract`, aware `observed_at`,
  `split` (`development` or `holdout`) and `stratum` (`shown`, `borderline`,
  `gate_rejected`). No gold/label/provenance field is admitted in this object.
- `gold` is a separate sidecar: `labels` with normalized-text `identity`, canonical
  `label` and `human_provenance`; `synthetic`; `split_frozen_at` and
  `holdout_opened_at`. An identity is the existing canonical JSON SHA-256 of
  `[normalized(title), normalized(abstract)]`, using NFKC/casefold/whitespace
  normalization. Match by that text identity, not by row order or paper number.
- The generator receives only title, abstract, configured goals and the fixed task/
  options. It does not receive labels, provenance, split, family, sampling stratum
  or gold filenames. External text remains data, never execution authority.
- Duplicate canonical-text identities or canonical IDs across papers, split leakage,
  future-development leakage, invalid/unmatched labels or calibration using
  holdout families fail before dispatch/artifacts. Thresholds require frozen
  development-only provenance before holdout opening; unknown is not a negative.

Canonical priorities are `must_read`, `should_read`, `could_read`, `dont_read`.
The experimental ordinal mapping is 3/2/1/0; it is never the production composite
score. b11430 request criteria stay in fixed lexical order. Complete native
probabilities are read by label keys into canonical-priority order. The native
choice is preserved even when it differs from argmax; no rationale is invented.

Missing title/abstract, oversized prompts, malformed/oversized response, timeout,
model mismatch, unavailable baseline, explicit blockage and threshold abstention
remain distinct unscored outcomes. They are not `dont_read`, zero-valued training
examples or fabricated one-hot distributions. Native raw terminal bytes are
retained before parsing; malformed neighbors do not suppress valid decisions.

## Arms and existing baselines

Native/recorded/blocked arms reuse the published support comparison's qualified
arm and pinned llama.cpp b11430 protocol. No TypeSafe/OpenJev author-server protocol
is silently assumed compatible; keep unqualified/unsupported arms visibly blocked.
The returned model is a server alias, not proof of a checkpoint hash.

A recorded response specifies `arm_id`, normalized-text `case_sha256` and a bounded
terminal file reference. Only recorded arms may consume those responses.

ML/LLM baselines are **imported matched artifacts**, not new inference. Each declares
`kind` (`ml`/`llm`), model, baseline-owner source hash, model-artifact hash, training
provenance and rows keyed by the same normalized-text identity. Owner hashes bind
the actual existing `classifier.py` or `llm_classifier.py` without importing them.
ML training provenance must name a nonempty set of training families, exclude
holdout families and precede held-out papers. Missing or empty training provenance
is refused, not inferred. All snapshots are private
artifacts of this explicit run; hashes provide integrity, not signed authenticity.

Baseline rows preserve observed model, actual label, optional continuous regressor
score and measured request elapsed time. They never invent four-class vectors.
Rows carry closed native-four-way, ML-regression or LLM-ordinal capabilities.
Native expected ordinal score and imported ML score/LLM ordinal score are labelled
experimental ranking projections. An LLM's optional score/confidence is retained
but never ranked as relevance. A missing ML regression score makes that cohort's
regression ranking unavailable without removing valid classification decisions.
Missing artifacts remain unavailable; malformed
row values remain unscored with the original whole artifact retained. No production
`write_predictions_to_csv` path is called.

## Read the report honestly

The report contains means **and** medians, per-class and sampling-stratum counts,
coverage/status denominators, absolute ordinal errors, must-read retention, false
strong recommendations, P@k/NDCG@k and top-k must-read retention. Input-wide gold/
sampling counts are named separately from held-out eligible gold/sampling counts. Binary usefulness
for P@k means human `must_read` or `should_read`; gains for NDCG are `2**ordinal-1`.
Classification must-read retention counts gold must-read classified into the top
two priorities, conditional on a scored decision. False strong recommendations
count gold dont-read among predicted top-two priorities. `class_coverage` separately
shows total/evaluated counts, statuses and decision coverage for every eligible
gold class, including unscored failures; blocked cases are not evaluated. Empty
denominators are null, not zero success. `synthetic_labels` and
`input_provenance_class_counts` distinguish synthetic labels and declared/missing
provenance by class. A supplied provenance declaration is not independent human
verification; its original text remains in the private gold snapshot.

Native Brier/calibration/ECE use the full vector and probability of the preserved
native choice, not a vendor confidence field. Unsupported baseline probability
metrics are null. No cross-model calibration is asserted.

Pairwise ranking uses the **same successful labelled intersection** at the same
configured depth. Reports show its coverage and selection bias; insufficient
ranking depth is unavailable. Paired percentile uncertainty resamples paper-family
mean absolute ordinal-loss differences with a configured seed. It is not a CI for
NDCG/P@k ratios. No failed/abstained/missing prediction is filled to construct a rank.

Synthetic/unverified labels or fewer than the predeclared real-label/family floors
cannot establish a winner. Cold/warm state, server peak memory, cost and physical
passes/retries are currently unmeasured and remain null. Resource/retention gates
may be false or unknown; the outcome is no-go or inconclusive, **retain baseline,
no winner**. A future real campaign must separately qualify access, matched baseline
and human labels, controlled resources, repeated runs and decision rationale.

## Artifact and safety boundaries

Only new attempts under selected `Settings.data_dir` may be written. Existing or
failed owners are never reused. Every necessary writer must return before ACK;
a readable file after a raised writer is not confirmation. FAILED/missing ACK,
foreign/partial inventory, symlinks, input/config/code/schema/source drift and
inconsistent readout/report projections prevent reopening. Recorded terminals
must equal their exact admitted case/arm recordings and preserve the received
state; recomputed copied-output hashes alone cannot replace those bound inputs.
Each terminal is owned by its canonical row reference; omission cannot orphan
retained bytes. Terminal-free states reconstruct admitted blockage/recording
presence and finite remaining-budget observations, with exact request/null-result
projection. True wall exhaustion remains evaluated/unscored, not blocked. The
transport state domain cannot manufacture predispatch states. Clock observations
are unsigned telemetry, not authenticated execution or model resource evidence.
Reopening performs no
HTTP, acquisition, model load or writes. Filesystems are not malicious-writer or
cross-database exactly-once systems; this is not signed authenticity.

Reference discipline: ARE `are/simulation/validation/judgment.py` preserves unknown
versus failed semantics; Codex `codex-rs/core/src/responses_retry.rs` records scoped
attempt/retry ownership. This comparator deliberately uses zero client retries.
Recent arXiv:2609.30454v1 motivates fixed option order and caution with uncertainty;
its task results are not evidence about this app. No upstream source/data dump,
private owner dataset, model weights or generated result artifacts belong in this PR.
