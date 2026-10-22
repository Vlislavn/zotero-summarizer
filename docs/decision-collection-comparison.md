# Offline existing-collection comparison

```text
frozen taxonomy/public papers + separate reviewed assignments
 -> identical independent membership questions / imported existing similarity results
 -> raw terminal ACKs / typed per-question outcomes
 -> private set/question reports / verified read-only replay
```

This optional tool compares existing-taxonomy membership without creating collections,
queuing changes, modifying Zotero, training/loading models or starting a server. It is
an independently useful offline part of #42, not the completed empirical study or a
model winner. Public synthetic fixtures prove machine wiring only.

```sh
python -m tools.method_validation collections --config /absolute/path/config.json
python -m tools.method_validation collections --config /absolute/path/config.json --run
python -m tools.method_validation collections --inspect /absolute/path/attempt
```

Default admission and `--inspect` have zero model calls/writes and bypass app bootstrap.
Only explicit `--run` loads Settings for the selected absolute `project_root`, with
`.env` disabled, and exclusively reserves a NEW
`Settings.data_dir/collection_comparison/<attempt>/`. Existing/failed attempts cannot
be reused. No credentials, provider/model fallback or endpoint discovery is performed.
See [frozen schemas and signatures](../tools/method_validation/COLLECTION-INTERFACES.md).

## Inputs and scope

Configuration JSON supplies `project_root`, safe `attempt`, hash-bound absolute FileRefs
`inputs`/`gold`, `arms`, `baselines`, explicit baseline `reference`, and distinct
`generator_handle`/`grader_handle` (the grader here is deterministic set comparison).
`ZS_COLLECTION_OVERRIDES` overlays validated configuration. Unknown/malformed fields,
nonfinite JSON, duplicate object keys, invalid UTF8 and mismatched file hashes fail
loudly. References are operator-supplied inputs, not paths proposed by document/model
text. No current user library/model or private endpoint is inferred.

Inputs contain papers, the tree and optional recorded terminal FileRefs. Each paper
has a stable paper ID, canonical family/identifiers, original title/abstract, development
or holdout split, sampling stratum and aware observation timestamp. Gold is a SEPARATE
grader-only sidecar joined by normalized title+abstract hash, never positional index.
Reviewed empty membership lists mean genuine no-match. Null memberships or absent
human provenance are unscored, never negative labels. Existing memberships require
human confirmation before they become scientific gold.

The tree supplies unique stable keys, names, parent keys, explicit topical/project/
workflow roles, descriptions, description-source papers, development examples and a
freeze timestamp. Full paths are derived from validated parents; cycles/unknown
parents or work limits refuse. Workflow nodes are never membership questions. A child
choice never adds its parent. Default questions cover ALL eligible nodes in stable key
order. An explicit `selected_keys` shortlist is frozen identically for every decision
arm; its recall/ceiling is reported separately, never repaired with gold keys.

Every description/example/baseline reference must precede its OWN frozen owner as well
as the split. Heldout families, normalized duplicates and canonical versions are
excluded; an unknown or falsely declared lineage is not authenticated by this tool.
Taxonomy/baseline freeze predates holdout opening. Thresholds require the development-
only calibration contract shared with priority: exact split digest, declared development
families and pre-opening freeze. Do not tune on test outputs.

Prompt data contains ONLY title/abstract, target key/derived full path, its description
and development examples. Gold, source lineage, split, stratum and assignment-derived
tags are not in the payload. Descriptions/examples are untrusted evidence, not action
permissions. Options, question ordering, preprocessing and zero client retry policy
are identical across arms. A node-specific oversized prompt leaves other questions
and imported baseline rows available; missing paper text remains unscored everywhere.
No textual rationale is accepted/generated.

## Capabilities must not be invented

Native and recorded arms preserve the selected `belongs / does_not_belong /
insufficient_evidence` choice, ALL three keyed probabilities, observed model alias,
confidence and usage. Choice need not be argmax. Threshold abstention, insufficient
evidence, invalid/missing/oversized inputs, model mismatch and operational failure
remain distinct. A native confident no-match requires all eligible questions to have
answered `does_not_belong`; a shortlist cannot claim this global conclusion.

The original `storage/corpus_read.py::CorpusReadMixin.match_candidate` returns at most
THREE COLLECTION NAMES, NOT keys, scores or probabilities. `top_k` controls similar
paper titles, not collection count. Import its original readout unchanged with the
actual owner/source and model-artifact bindings plus frozen reference corpus. An
explicit `has_corpus=true` row with an empty reference inventory refuses before effects;
no-corpus/unavailable artifacts may have no references. Original strings resolve against
exact names and derived `Parent > Child` paths across the ENTIRE frozen tree, including
workflow nodes. Distinct-key alias collisions, duplicate resolved keys and unknown names
are unscored, never guessed from gold or parent context.
Known workflow suggestions are explicitly excluded and counted, not assigned. Preserve
remaining original ordering. `has_corpus=false` is unavailable. Empty retrieval is
not a native confident rejection. No baseline probability vector/calibration is
fabricated; comparisons respect the original top3 capability.

OpenJev/TypeSafe/CLEF names alone do not qualify a model. Use explicit blocked arms
with reasons for unknown access/licence/runtime/protocol. Native dispatch requires a
compatible `/v1/systemone` endpoint, pinned runtime/checkpoint, quantization/source/
licence/access/context qualification, physical-pass bound, public-input consent and
external resource guard. The shared llama.cpp b11430 adapter never loads a backend,
uses no API key/auth lookup, follows no redirects and adds zero HTTP retries. A served
alias or caller-supplied artifact hash is not authenticated checkpoint provenance.
Actual model/endpoint/source licences and access must be verified separately.

## Budgets and receipts

All paper×question×arm outcomes, baseline rows, calls, declared physical passes,
complete prompt/request bytes and cumulative snapshots+generated requests are
reserved BEFORE owner creation/HTTP. No clipping or evidence substitution. Defaults
are configurable admission policies, not empirically validated resource promises:
1000papers/200nodes/32depth; max4000work units/1000calls, 8MiBper input,
64MiBcumulative snapshots+requests, 12000prompt characters/64KiBper request or response.
Time safety is 120s idle/3600s run, schema-capped and additionally bounded by
`ZS_SOURCE_WORK_SAFETY_MS` (default3600000, maximum7200000). External process-tree
RSS/free-RAM/swap monitoring remains required for live execution. Native consumption
checks the remaining run deadline between chunks and on completion, preserving an
expired stream as timeout. Synchronous I/O can remain blocked for its per-operation
inactivity timeout; an external process guard supplies the hard wall, and cancellation
does not prove remote computation stopped. No concurrent local model loading is authorized.

Every raw dispatched/recorded terminal is acknowledged by its required writer BEFORE
projection. Rows/report/manifest then publish under the new owner; ACK binds the final
manifest only after all required writes RETURN successfully. Writer exceptions propagate
and leave FAILED even if bytes are readable. `os.replace` is confined to this newly
reserved single-writer owner, not a hostile-writer or power-loss durability guarantee.

Inspection closes filenames against admitted configuration/input snapshots, canonical
case rows, verified terminals, timing and reports; resealing foreign case/snapshot or
unrelated filenames cannot make them part of the plan. It verifies hashes, code/schema/source identities, exact
admitted recording bytes, canonical per-row terminal ownership, request identities,
closed transport states consistent with HTTP status, completion and retained-byte
observations, and every derived row/report. Terminal-free outcomes reconstruct
admitted blockage, recording presence and finite remaining-budget observation: optional
terminal removal cannot turn malformed responses into blocked coverage. Unsigned local
time/resource observations are consistency evidence, not authenticated execution history
or protection against coordinated replacement of all inputs/history. Strict toolkit
code identity means another toolkit revision may require the original checkout to reopen.

## Reporting and remaining acceptance

Report JSON includes set micro/macroF1, exact-set accuracy, mean/median/p95 normalized
Hamming loss, P@3/Recall@3 at explicit `min(3, eligible_count)` depth, per-collection
support/FP/FN, rare/nested/duplicate-name metadata and sampling strata. Independent-question
Brier/selected-choice calibration is available only for actual full native vectors;
baseline calibration is null. Native-paper execution timing spans first question start
through final case-row writer ACK for wholly dispatched question batches, including
all calls/projections/row publications. Common preflight/snapshots/startup are explicitly
excluded, not silently relabelled complete application time. Timings are typed and
bound to their paper/arm completion during inspection; missing timing stays unknown.
No HTTP-only sum or single-question time is relabelled paper time. Empty denominators stay null.

Paper coverage means complete declared-question/set proposals; native question coverage
requires a binary membership answer, not insufficient evidence. Exact-set scoring uses
the full eligible gold set, so omitted shortlist memberships remain false negatives.
Common successful cohorts have explicit denominators/survivorship limits. Paired
uncertainty is an equal-family percentile bootstrap of normalized set-Hamming-loss
DIFFERENCE, not an F1/ratio confidence interval. Defaults require100judged papers/
20families for that interval. Perfect synthetic arithmetic is a wiring floor only.

Cold/warm/startup/memory/cost/physical retry/pass/manual-correction measurements remain
null where unavailable. Predeclared coverage/error/resource ceilings can yield no-go;
otherwise results remain inconclusive, baseline retained, winner null. Full#42 still
needs approximately100human-confirmed papers with rare/nested/duplicate/multiple/
ambiguous/no-match strata, controlled development calibration, actual qualified endpoints,
resource/matched-coverage measurements and human interpretation. No Zotero changes or
scientific deployment acceptance follows from this offline PR.
