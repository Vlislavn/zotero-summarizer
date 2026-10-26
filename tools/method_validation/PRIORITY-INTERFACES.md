# Frozen priority-comparison contract

This additive task contract does not redefine the support task's [INTERFACES.md](INTERFACES.md).
The concrete strict JSON schemas are generated from `priority.Config`, `Inputs`,
`Truth`, `BaselineArtifact`, `BaselineReadout`, `Readout` and `Result`; changes to
those source/schema identities invalidate artifacts rather than silently adapting
an older attempt. Full usage and field meanings:
[priority feature overview](../../docs/features.md#priority-comparison).

## Entry and ownership

```python
main(['priority', '--config', absolute_config])                 # zero calls/writes
main(['priority', '--config', absolute_config, '--run'])        # explicit new owner
main(['priority', '--inspect', absolute_attempt_directory])    # zero calls/writes
priority_run.prepare(cfg, snapshots=None)
priority_run.run(cfg, raw_config, *, transport=None) -> (owner, report)
priority_run.reopen(owner) -> report
```

Configuration is explicit, strict and environment-overridable via
`ZS_PRIORITY_OVERRIDES`; wall safety uses the existing
`ZS_SOURCE_WORK_SAFETY_MS` default/max-cap. No model/endpoint/credentials/server
launch is implicit. Selected Settings owns `data_dir/priority_comparison/<attempt>`.

The existing `NativeDecisionAdapter` accepts an additive optional `projector`
keyword; its unchanged default remains the support projection. Both tasks share
one request dispatch and raw-terminal ACK owner. The priority projector returns
`(status, readout_or_none)`, never writes or decides permission, and validates the
complete four-label readout. Transport/programmer/publisher failures are not
silently retried or converted into semantic classes.

## Frozen machine boundaries

- Labels: `must_read / should_read / could_read / dont_read`. Experimental ordinal
  values: 3/2/1/0. Native request option keys are fixed lexical order; response keys
  are matched by label, never position. Choice is not replaced by argmax.
- Input case/gold/baseline identity: `digest([normalized(title), normalized(abstract)])`,
  using existing canonical JSON SHA-256/NFKC/casefold/whitespace primitives.
- Native input: bounded title+abstract+goals only. Grader labels, provenance,
  sampling/family/split/paper identifiers never enter the generator request.
  Omitted or null title/abstract remain unknown evidence and yield per-paper
  `missing_input` with no native dispatch, not an implicit negative or whole-run
  admission error. Stored raw inputs preserve omitted versus supplied-null keys.
- Case statuses: decision, threshold_abstention, model_mismatch, invalid_response,
  timeout, unavailable, response_too_large, encoded_response, blocked, wall_budget,
  missing_input, input_too_large. Only decision carries a classified `label`.
  A threshold/mismatched native readout may be retained without becoming a label.
  Intrinsic result validation permits native readout only for these two observed
  states or decision; operational outcomes cannot carry a semantic readout.
  Standalone validation is not authentication of configured policy or raw artifacts.
- Rows have closed `native_four_way / ml_regression / llm_ordinal` capabilities.
  LLM ranking uses ordinal priority, never its retained confidence score; a missing
  ML regression score leaves ranking unavailable while retaining classification.
  No made-up native probability vector; invalid baseline rows remain unscored,
  original artifact retained.
- Whole-family/canonical/time and development-only threshold firewalls apply
  before inference or writes. Whole workload reserves outcomes/calls/forward-pass
  work; snapshots have a cumulative byte cap. No clipping to fit.
- Raw terminal bytes are written and acknowledged before parser projection.
  Rows identify exact request/input/model/readout/telemetry and the native raw
  terminal, with unknown resource/physical-pass observations kept null.
- New owner creation is exclusive. Necessary writes must return before manifest
  ACK. Failed or ambiguous owners cannot be adopted by readback. Reopen verifies
  exact inventory/hashes/config/schema/code/baseline sources and recomputes
  native/baseline/report projections without effects. Recorded terminal bytes
  must equal the admitted exact case/arm recording and preserve received state.
  Terminal references are canonical per-row owners; omission cannot hide retained
  bytes. Terminal-free outcomes reconstruct admitted blockage/recording absence
  and finite remaining-budget observations, not a self-declared blocked status.
  Actual wall exhaustion stays evaluated; clock observations are unsigned.

No product/training/golden CSV/band/corpus/Zotero mutation, real-model qualification,
private-input authority, performance advantage or full issue #38 completion follows
from an offline wiring test. Reports remain no-go/inconclusive, retain baseline,
no winner while empirical/resource qualification is absent.
