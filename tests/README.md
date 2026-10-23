# Current part 2 coverage

This checkout includes retained history/analysis tests plus incremental CSV,
UTC-overflow, journal-source and SQL-identity regressions. The part-1 paragraph
below is the prerequisite #162 coverage record, not this checkout's suite limit.
Operation suites are covered by part 3 below; weekly scheduling is covered by #166.

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

Follow the isolated test/runtime rules in [architecture](../docs/architecture.md).
`test_calibration_load_admission.py` runs real calibration and factory admission
against inert lower adapters: local and cached refusal, both constructors,
HTTP job status, hosted calibration and ordinary local application controls.
`test_setup_attempt_regressions.py` covers inventory cancellation, actual callback
exclusion past executor shutdown, active/interrupted attempt identity, external
credential rotation, empty retries and malformed endpoint failure data.
No live inference, credentials, network, private library or hardware load occurs.

## Audit regression tests

Audit tests exercise original-source verification, correction, cache retention and
read-only export using scripted clients and temporary files. They establish machine
contracts, not model accuracy, paper completeness or human-audit benefit.

`test_digest_contract_payload.py` checks that unrelated runtime subtype metadata
cannot change the declared digest attempt identity; plain digests and original
model values remain unchanged. No extra model or native-library call is needed. PDF-override fixtures declare
full-text independent quality evidence rather than weakening reading-policy
or source-path assertions.

`test_audit_cache_compatibility.py` covers out-of-root native attachments versus
export denial, single-hash reuse, and malformed legacy exports with valid controls.
`test_audit_digest_subtypes.py` follows enriched digests through actual scripted
publication and export; optional metadata stays intact but outside audit authority.

Use the repository verification commands and the local source-bound evidence;
scoped checks do not substitute for full hooks, backend or hosted CI.

## Explicit local/gateway boundary

```text
actual app factory + fixture SPA   -> synthetic admission/denial matrix
actual Uvicorn + private socket    -> HTTP/WS + owner/lifetime/restart oracles
actual Vite proxy + local guard    -> same-origin POST / foreign-Origin denial
```

Gateway tests use disposable project/data directories, generic owner/origin
fixtures and owned subprocesses. No Tailscale/account/ACL/native-library/model
activation is performed. `test_local_access_boundary.py` and
`test_make_launchers.py` preserve the local path and unsafe-bind/shortcut refusal;
`test_serve_free_port.py` retains the no-foreign-process-killing invariant.
`test_mobile_access_boundary.py` has synthetic transport adaptation, not a real
network identity. `test_gateway_app_surface.py` exercises the actual factory and
fixture assets/public files, including admitted/refused WebSockets.

`test_gateway_owned_paths.py` checks modes, ancestry, symlinks, foreign/replaced
paths, idempotent cleanup, startup failure and bounded shutdown configuration.
`test_gateway_real_transport.py` uses real Uvicorn Unix HTTP/WS with handcrafted
fixture identity headers. `_gateway_process.py` binds child imports to the tested
source and records their roots; it substitutes a trivial application lifespan.
Signal assertions require non-success, owned socket removal before fixture cleanup
and successful rebinding, not one platform-specific signal/exception exit encoding.
`test_mobile_server_lifetime.py` also injects SIGTERM/SIGINT after bind and before
server startup, so cleanup is checked before Uvicorn's usual signal-capture point.
`test_gateway_signal_control.py` retains stock-versus-gateway interruption controls.

`test_gateway_vite_proxy.py` imports the actual Vite configuration and verifies a
real local POST through it. Its explicit Node/frontend-dependency absence skip is
not a passing integration test; install existing project dependencies and run this
case before accepting development wiring. Caches/logs are fixture-owned, not written
through shared dependency directories. Real subprocess tests require POSIX sockets.
They use bounded readiness steps and named environment-overridable safety caps.

These machine contracts do not certify a real trusted TLS gateway, installed
Tailscale build/header sanitization, private device policy, device-specific access,
active-stream revocation, physical iPhone installation/offline behavior, downstream
model shutdown, scientific accuracy or full native-data delivery. See
[deployment boundary](../docs/local-deployment-boundary.md).

## Isolated test lifecycle

The existing autouse boundary isolates app context, credentials, network and the
named application's logger. It restores level via `setLevel`, propagation,
disabled state and original handlers, closing only new unshared handlers.
Handlers attached to root or another logger stay open. Restoration and remaining
closures run even when one close fails; that error still fails teardown.
Production logging remains unchanged.

`test_app_logger_isolation.py` copies the real conftest into ordered pytest
subprocess scenarios, keeping diagnostic state out of the outer test process.
They check cache/flag restoration, handler ownership, closure failure and warning
capture. Real `setup_logging` uses a test-owned log path, ignoring any inherited
absolute `APP_LOG_FILE`; setup failures leave the outer logger untouched.

```sh
pytest -q tests/test_app_logger_isolation.py tests/test_review_fleet.py \
  tests/test_startup_boundaries.py
```

This verifies test isolation, not live-model, Zotero or scientific behavior.
See the root architecture verification workflow.

## Corpus test boundaries

`test_corpus_off_boundary.py` covers targeted refresh with corpus disabled,
initialized resources, existing/missing locks, enabled reconciliation and
unavailable/uninitialized runtime resources. OFF fixtures fail on any reader,
cache, lock or feedback I/O; these tests load no encoder or user library.

Existing engagement/reconciliation fixtures explicitly declare corpus enabled.
Their assertion trees and parametrizations are unchanged. Existing manual-edit
and pending tests retain their native-write assertions; OFF only suppresses the
subsequent optional corpus refresh, not the explicit edit.

## Current part 3 coverage

The part-1/part-2 availability statements above are prerequisite #162/#163
references. This head includes manual CLI and actual-child operation suites;
`test_feedback_operation_lifecycle.py` retains the six signal launch/receipt and
completed-worker RSS regressions. Weekly scheduling is covered by the later section.
Current dependency controls preserve all root, snapshot, report and analyzer
regressions without claiming a fresh full-suite run.

## Selected CLI test boundary

Run `pytest -q --forked` under the repository's isolated test environment; compare
known macOS native-library failures to the unchanged baseline.

Part 1 retains the original Feedback capture assertions and adds duplicate
migration-namespace refusal cases. `test_feedback_analysis_report_primitives.py`
keeps recorded Markdown content inside literal code spans; downstream
the statement describes the prerequisite #162 checkout, not this integrated branch.

`test_cli_selected_environment.py` exercises fresh isolated interpreters against
synthetic default/selected projects. Import/invalid-input paths deny model SDKs and
network. Positive migration cases use real CLI, Settings and SQLite migrations;
only the embedding SDK symbol is inert and its constructor refuses all model
work. They verify configuration selection, not actual model compatibility.
Existing Faithbench exports retain exact defining object identity. Default-root
redirect cases cover shell/cwd admission and the server factory handoff. The import
guard also denies the direct Anthropic SDK; subprocess safety defaults to 10 seconds,
with `ZS_CLI_TEST_WALL_SECONDS` overrides capped at 60 seconds.

## Current part 4 coverage

Weekly scheduling/recovery and manual/weekly signal/RSS controls are covered by
the #166 fixtures; the part-1/2/3 notices above are prerequisite-stage records.

Part 2 retains the corresponding original Feedback suites without assertion changes.

Part 3 retains the corresponding original Feedback suites without assertion changes.

Part 4 retains the corresponding original Feedback suites without assertion changes.

The existing daemon training-ledger producer is retained with its original synthetic-trainer regression suite. It appends retained artifact identities only after install/rescore; this packaging adds no fitting, inference or new identity authority.

Original frozen weekly replay/source is retained with its original three-argument cohort ordering contract. Existing source helpers use their canonical number/summary names and the production caller supplies its existing cohort IDs. No new grader, labels, ranking formula or real-data claim is designed.

Retain original setup source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.

Retain original RSS source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.

Retain original reading source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.
