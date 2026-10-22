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

Full verification commands and the known macOS native-library fork baseline are
in [architecture.md](../docs/architecture.md#verify-a-change). Compare the failed
set against that baseline rather than hiding failures. Authored/static-checked
cases are not evidence of execution; report RED/GREEN and runtime limits honestly.
