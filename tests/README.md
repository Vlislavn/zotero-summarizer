# Tests

Run focused cases with `pytest -q tests/<test_file>.py`. Tests use the existing
pytest fixtures and isolated application state; do not point fixtures at a live
Zotero library, provider, account or model. Feature-specific regression contracts
are documented with their owning service package and usage instructions.

RSS engineering coverage: `test_feed_engineering.py` exercises same-response
production across rank/prefix/map-reduce with the real digest verifier and scripted
semantic replies, source/profile/cache tamper rejection, honest unknown effort,
current-error retention, continuous ranking and CLI v1 publication/rerun budgets.
`test_reproduction_uncertainty.py` retains the six original availability/duration
oracles. These are deterministic plumbing tests, not scientific/user usefulness
measurements. `test_feed_cached_qualification.py` exercises the real standalone
default getter with an existing config/cache, absent/invalid config and stale or
unavailable source/config; no injected getter is credited as freshness proof.
`test_feed_assessment_publication.py` carries mixed applicable/unknown and all-
unknown evidence through actual card/publication. Startup controls distinguish
missing current config from valid configured cached-only work, keep startup/model
calls absent and explicit queue consent intact, and reject a newly ready job that
omits its required assessment. Parent-owned runtime verification runs these explicitly.

`test_feed_review_regressions.py` covers package exports, omitted/blank unknown
projects, blocker precedence, missing/corrupt abstract-stage isolation, missing-PDF
recovery, numeric v1 confidence and real worker/cache binding. It checks feed-only
browser restrictions while preserving interactive acquisition.

`test_feed_prompt_boundary.py` checks actual refine/triage prompt serialization
with hostile closing-tag markup across source fields and an unchanged plain-text
control. These are structural boundary tests, not live semantic safety evidence.

`test_feed_original_spans.py` resolves layout-only quote whitespace to exact
original substrings/offsets across project, reproduction and impact records;
changed case, punctuation and words still fail, and accepted digests stay intact.

`test_feed_runtime_contracts.py` covers verifier/generator effort separation,
advertised wire dialects, unsupported-provider preservation and JSON-only CLI
stdout when runtime diagnostics are printed. Live model receipts remain separate.

Full verification commands and the known macOS native-library fork baseline are
in [architecture.md](../docs/architecture.md#verify-a-change). Compare the failed
set against that baseline rather than hiding failures. Authored/static-checked
cases are not evidence of execution; report RED/GREEN and runtime limits honestly.

## Policy regression tests

Quality/reading integration fixtures use temporary original-source bytes and the
real existing review-identity builder. Controlled extraction/model/evaluator
responses do not establish scientific truth. Raw-model, projected-grade, note,
proposal and cache assertions remain independent; never bypass source-byte guards
to make an integration fixture pass.

`_legacy_coverage.py` freezes the actual `bd5e267` main coverage algorithm and
thresholds for historical comparisons. Do not call the current grader while
labelling its result old. Original EBAI flag/highlight assertions remain; current
empty/unassessed coverage and explicit failures have separate typed controls.
The helper is test-only, not a second production scorer or newly verified paper
annotation/judge run. Main/current isolated counter and helper provenance are
retained in the parent qualification receipts.

Injected-PDF review fixtures supply a typed independent full-text quality result
when asserting full reading. Abstract/not-assessed controls retain the raw A/read
model digest but require effective skim; a grade/band alone cannot supply basis.

Rubric-admission regressions distinguish complete explicit N/A from missing/invalid
answers in any self-consistency sample, across contribution families. Sparse output
must reach the existing unassessed boundary, never shrink into A/highlight coverage.
The review-family fixture supplies explicit N/A; its original assertions remain.

Projection boundaries cover concept-only caveats for all reading actions and missing
critical support, partial quality in new versus legacy snapshots, and Search API
policy/raw provenance. Search mocks return real `PaperDigest` DTOs, not partial objects.

Use the repository verification commands and scoped source-bound receipts.
