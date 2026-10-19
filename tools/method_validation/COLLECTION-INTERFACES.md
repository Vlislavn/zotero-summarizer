# Existing-collection comparison — frozen interface v1

```text
Config FileRefs -> Inputs(tree,papers,recordings) + Truth(labels)
 -> admission -> original terminal/baseline rows -> deterministic reports
 -> exclusive attempt inventory / manifest / ACK -> verified read-only replay
```

Public task grammar: `collections --config FILE [--run]` OR
`collections --inspect DIRECTORY`. `--inspect --run` refuses. Default admission
prints JSON `{mode:inspect-inputs,papers,questions,synthetic,model_calls:0,writes:0}`.
Run prints `Private artifacts: DIRECTORY` then report JSON. Reports never promote
models or alter existing collections/memberships. This is one complete offline tool,
not full#42 empirical acceptance. See [usage](../../docs/decision-collection-comparison.md).

## Code-owned API

- `collections.Config`, `Inputs`, `Truth`, `Paper`, `Tree`, `Node`, `Example`,
  `Gold`, `Baseline`, `BaselineArtifact`, `Readout`, `Result`, `PaperTiming`: closed/frozen Pydantic
  boundaries. Unknown fields refuse; exact schemas via `model_json_schema()`.
- `collections.identity(paper)` reuses priority's normalized title+abstract identity.
- `admit(cfg,inputs,truth)` -> `(papers,gold,nodes,paths,questions)`; keyed normalized
  papers/gold, stable-key tree/derived parent-key paths, shared ordered questions.
- `payload(paper,node_key,(nodes,paths,tree),cfg)` -> full UTF8 request bytes.
- `project(raw,arm,cfg,status='received')` -> `(status, readout_dict_or_none)`.
  Closed shared transport states; native choice and probability order remain distinct.
- `collections_run.baseline_owner()` -> digest of current corpus_read.py/corpus_types.py
  source hash map; this is source identity, not actual model-artifact authentication.
- `prepare(cfg,snapshots=None)` -> `(inputs,truth,papers,gold,nodes,questions,requests,
  errors,baselines,recordings,saved)`; requests/recordings keyed by independent question
  identity and arm, errors by paper identity; exact bounded frozen input snapshots.
- `run(cfg,raw_config,*,transport=None)` -> `(new_owner_path,report_dict)`.
- `reopen(owner)` -> verified report dict; no provider/app/Settings/write effects.
- `command(args,*,transport=None)` -> exit0 only after its operation returns.
- `collections_report.report(rows,cfg,inputs,truth)` / `readable(result)` share ONE
  deterministic projection for JSON/Markdown and replay.

No app imports this optional tool. No new model loader/store/runner framework.
Existing native transport/raw ACK/refusal/publisher and paper/calibration admission
are reused; membership/tree/baseline capabilities and reports remain task-specific.

## Dataset and gold shapes

`Inputs = {papers:[Paper], tree:Tree, recordings:[Recording]=[]}`.
`Paper = {paper_id,family,canonical_ids:[text]=[],title,abstract,split,stratum,observed_at}`;
observed timestamps are timezone-aware; split is development/holdout. Missing title/
abstract is an explicit unscored input outcome. Tree evidence must fit in full.

`Tree = {nodes:[Node],frozen_at,description_sources:[Paper],examples:[Example],
selected_keys:[key]|null=null}`. `Node = {key,name,parent:key|null,
role:topical|project|workflow,description}`. `Example = {paper:Paper,node_key,belongs:bool}`.
Full paths are derived, never guessed. Questions are all eligible stable keys sorted,
or the explicit identical predeclared shortlist. Description sources/examples must
be development-only and predate their own tree freeze and split; declared lineage
is not signed proof of how free text was authored.

`Truth = {labels:[Gold],synthetic:bool,split_frozen_at,holdout_opened_at}`.
`Gold = {identity:sha256,memberships:[stable_key]|null,human_provenance:text|null}`.
Only nonnull memberships WITH human provenance score. Reviewed empty array is not
null. Neither inherited parent membership nor current-library labels are invented.
Whole family/canonical identifiers/normalized duplicates/time firewall applies before
tuning. Only eligible existing keys; no duplicate/unknown/workflow gold.

`Recording = {arm_id,case_sha256,terminal:{path:absolute_path,sha256}}`.
Question identity is `digest([paper_identity,node_key])`. Baseline row identity is
`digest([paper_identity,null])`. A recording can bind only an actual recorded arm's
admitted independent question; raw bytes must match again during inspection.

## Existing similarity capability

`Baseline = {arm_id,display_name,kind:similarity,model,artifact:FileRef|null}`.
`BaselineArtifact = {model,owner_sha256,model_artifact_sha256,frozen_at,
reference_papers:[Paper],rows:[original_row]}`. An original row is exactly
`{case_sha256:paper_identity,observed_model,readout:original_CorpusMatchResult_dict}`.
Retain the actual original result including `has_corpus`, `suggested_collections`
and other original fields; do not invent numeric collection scores. Any explicit
`has_corpus=true` row requires a nonempty declared reference-paper inventory before
effects; no-corpus artifacts may retain an empty inventory and remain unavailable.
The required projection accepts at most3 original names and an exact boolean corpus state.

Rows join by normalized text identity, never array index. Exact unambiguous names
resolve against the ENTIRE tree, not a gold-biased eligible subset. Known workflow
suggestions are excluded and reported; ambiguous/unknown names or malformed readouts
are invalid_response. Missing corpus is unavailable, not no-match. Empty retrieval
is explicit and cannot claim a native confident global rejection. A missing artifact
is unscored; source/model/lineage/hash mismatch refuses before effects. Actual source
and checkpoint qualification remains separate from unsigned declarations.

## Native/protocol and outcomes

`SupportArm` is the existing closed native/recorded/blocked configuration; same
checkpoint/runtime/licence/access/context qualification and development-only calibration
contract. Native payload is `{state,questions:{decision:{type:choice,instructions,
criteria:{belongs:null,does_not_belong:null,insufficient_evidence:null}}}}`.
Native raw response is `{model,answers:{decision:{type:choice,choice,probabilities:
{belongs:number,does_not_belong:number,insufficient_evidence:number},confidence:number}},
usage:{input_tokens:int,output_tokens:0}}`. No rationale/generated keys are accepted.
Options retain lexical order; probabilities sum to1 within the bounded tolerance.
Readout preserves selected label, all3 probabilities, model, confidence and usage.

Each `Result` owns paper/question/arm/capability identity, frozen reviewed gold (never
in prompt), status, native label/readout OR original baseline/resolved selected keys,
telemetry and optional exact terminal/request reference. A readout/status/capability
mismatch cannot become membership. Elapsed observations must be finite/nonnegative,
not booleans. All blocked/missing/wall/invalid/abstained/insufficient branches remain
visible; native global no-match requires ALL eligible questions answering no.

Raw terminal writer return precedes projection. Exclusive new owner is the only
publication target; required writer exceptions leave FAILED regardless of readable
bytes. Final ACK follows successful required publications. Inspection validates exact
inventory closed against admitted configuration/input snapshots, canonical case rows,
verified terminal owners, timing and both reports—not arbitrary manifest-listed extras;
code/schema/input/source bindings, all terminal and terminal-free branches,
row projections, typed `paper-times.json` ownership/completion and both report formats.
Native paper execution timing spans first question through last case-row ACK, not a
sum of HTTP observations; common admission/snapshot/startup remains excluded.
Unsigned timestamps/telemetry/hashes are
not authenticity, hostile-writer containment or remote-stop confirmation.

## Configuration and reporting boundaries

All relevant budgets/thresholds are validated Config fields, overridable by
`ZS_COLLECTION_OVERRIDES`; complete request+snapshot and outcome/call/physical-work
reservations precede effects. CLI source wall cap additionally uses the shared named
`ZS_SOURCE_WORK_SAFETY_MS`. No clipped prompts/evidence and no new automatic retries.

Reports separate complete set proposals, binary-question coverage and original top3
capability. They include class/stratum/collection errors, support/rare metadata,
shortlist recall, explicit common-cohort ranking/set metrics, native-vector calibration,
mean/median/p95 errors/complete-paper latency, selected-cohort paired family intervals
and null missing-resource/manual-correction observations. Nonempty known gold is not
manufactured when provenance is absent. `winner:null`, `action:retain_baseline` and
no-go/inconclusive remain until actual human/model/resource scientific qualification.
