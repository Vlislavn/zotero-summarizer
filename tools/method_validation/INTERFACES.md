# Frozen support API / input-output authority, version 1

This document describes one implemented support comparator, not a shared multi-task
framework. Future priority/collection features must be additive and separately
qualified. Python models in `support.py` are the validation authority; extra fields
are forbidden. No configuration, prompt logic, generated dataset or execution is
embedded in this document.

## Public seams

- `SupportCase`: frozen generator-only projection. Schema version 1, pair/paper IDs,
  criterion, original UTF-8 SHA-256, Unicode-character start/end, exact excerpt and
  excerpt UTF-8 SHA-256. `admit_support_case(data, original_text)` binds the literal
  occurrence. Repeated text requires the supplied occurrence; no search, trimming,
  fuzzy binding or generated-note substitution occurs.
- `SupportArm`: frozen model/protocol, display identity, qualification metadata,
  explicit native/recorded/blocked kind and optional development-frozen abstention
  threshold. `blockage()` separates unknown/ineligible native execution from failure.
- `ProviderReply`: frozen raw terminal bytes, typed status, native readout and one
  known attempt's telemetry. `NativeDecisionAdapter(arm, cfg, transport=None)` is
  serial caller-owned HTTP, with `adapter(request_bytes, retain, timeout=None)`.
  The required retain callback must return before strict native parsing; callback
  failure propagates. Both public native entrypoints require consent/guard and next-call
  reservation; one adapter instance bounds cumulative calls/work. Whole-workload
  reservation belongs to the runner, not a process-global counter. Off-host HTTP is
  refused; HTTPS and explicit loopback HTTP are supported.
- `evaluate_support(case, arm, cfg, retain, recorded=None, transport=None,
  timeout=None)`: actual native/recorded I/O and projection, not semantic grading.
  Supplying a recording requires an unblocked recorded-kind arm.
- `SupportResult`: frozen validated per-pair/per-arm terminal projection. Gold is
  added by the runner after generator projection; a semantic decision requires its
  native readout or original binary artifact row. Unscored outcomes have no binary
  decision. Model mismatches retain the actual native choice/vector unscored.
- `Config`, `FrozenSupportInput`, `BaselineArtifact`: strict frozen admission
  schemas. `prepare(cfg)` performs read-only whole-workload admission.
- `run(cfg, raw_config, transport=None)` owns one new immutable attempt and returns
  its directory and report only after successful publication. `reopen(directory)`
  validates the inventory, current code/schema identities, admitted inputs, pair
  identities, native/baseline projections and report; it never resumes or executes.

## Generator / grader separation

Only escaped criterion and exact original excerpt enter the native state. Instructions
and the fixed three-option domain are tool-owned. IDs, original full text, family,
split, calibration, baseline output, label/provenance and grader handle never enter
that projection. The model alias is bound to the response, not sent as a selector.

Gold joins via `gold_identity(case, original_text)`: NFKC/casefold/whitespace-canonical
original text, criterion and excerpt plus literal character bounds, hashed as canonical
JSON. This is independent of exact UTF-8 source/excerpt hashes. Gold is never matched
by row position or derived from an arm's verdict. Canonical source aliases must share
family; whole families/canonical sources cannot cross splits. Human provenance is
caller-declared, not authenticated by a nonempty string. Non-null provenance is required
for paired estimates/noninferiority eligibility, not for descriptive per-arm metrics.
All candidate holdout pairs remain in the matched-coverage denominator.

## Binary artifacts

Each imported artifact pins existing owner source SHA, model, source reference and
criterion/original-excerpt projection. Each row binds `digest(case.model_dump())`,
including exact criterion/source/span/excerpt identity. Missing artifact/row/score
is unavailable. For the legacy verifier, a present row with omitted verdict (`null`)
is distinct from a missing row: omission defaults to confirmation, and only trimmed,
lowercased explicit `reject` rejects. Rejection is not contradiction. MiniCheck's
original scalar remains scalar; a separate development-frozen threshold provides
binary support, never a synthetic three-class vector. Missing threshold retains the
scalar unclassified, with descriptive binary Brier available where gold is present. No baseline provider, joblib,
MiniCheck, cache, training, golden CSV or label writer is constructed or invoked.

## Artifact identity

`max_input_bytes` is a shared aggregate cap across frozen inputs, baseline artifacts
and recordings, enforced before owner creation and on replay admission.

Raw original/effective configuration, frozen inputs (including full original sources),
imported baseline bytes, recorded bytes and bounded native terminal bytes remain
private. Every row has case/arm identity and request hash. The manifest hashes all
owned files plus current Python source and validation schemas; ACK binds that manifest.
Reopening verifies content and deterministic projections, not the truth of human
labels, checkpoint claims, measured server activity, source acquisition or a maliciously
rewritten self-consistent manifest. Keep private artifacts outside public repository
artifacts and do not distribute them as a bundled production corpus.
