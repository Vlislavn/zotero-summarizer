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

`test_search_materialize_recovery.py` and `test_search_planned_identity.py` exercise
explicit Search Add with real temporary native SQLite: persistence refusal before
effects, native commit before failed session finalization, rollback, unchanged
native replay, backup, concurrency, legacy plans and exact-family/background
identity preservation. They do not claim distributed or cross-library exactly-once.

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

`test_interpretation_dependency_retention.py` reproduces typed A/B subtype capture
and current C receipt handling across both actual HTML renderers. Unrelated null
or nested metadata stays stored without becoming scientific authority; declared
finding changes still refuse. These are synthetic contract checks, not a study.

`test_interpretation_experiment_*` uses explicitly synthetic cohorts/clients to
exercise source/schema/presence, optional failure isolation, current-field and
passage bindings, unchanged legacy QA authority, saved-record renderer handoff,
final-byte brief audits and per-invocation completion observations. Cohort fixtures
are not twenty real held-out papers, human judgments or a measured benefit study.
Model-free fixtures never authorize native writes or count as physical-device QA.
Resource arithmetic controls cover inclusive tightened/default caps, per-paper and
exact cohort token counts, fractional latency, genuine breaches and shortfall
interval boundaries; these are numerical contracts, not scientific evidence.
`test_interpretation_experiment_review_fleet.py` covers blinded export order,
matching-but-unapproved CSV admission and independent partial-label refusal gates;
unknown primary outcomes cannot establish a conclusive benefit shortfall, and
zero actual card exposure cannot establish a card-pilot recommendation. Symlink
counterexamples cover reference reads, capture/export parents, foreign runs and
manifest reads without touching another project's real data.
`test_interpretation_experiment_visual.py` opts into installed Chromium with
`ZS_APPLICATION_BROWSER_SMOKE=1`: synthetic accepted/malformed optional records,
actual audited brief and native-note HTML, 390/1440px geometry and PNG evidence.
The note uses a disclosed browser wrapper, not the native Zotero application's CSS;
parent image inspection remains separate from deterministic geometry assertions.

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

`test_pending_rejection_atomic.py` covers mixed generated and ordinary rejection,
including stale-selection rollback without changing generated audit history.

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

## Decision-support regression tests

The application fixtures isolate runtime paths and network; see `conftest.py` and
`docs/architecture.md`. No test is permission for live models or private data.

## Offline existing-collection comparison (part of #42)

`test_collection_comparison.py`, `test_collection_boundaries.py`,
`test_collection_replay.py`, `test_collection_reservations.py` and
`test_collection_native.py` use only public synthetic `test_collection_fixtures.py`
data. `test_collection_review_findings.py` rejects missing corpus-backed reference
inventory and resealed foreign case/snapshot/unrelated files, preserving safe earlier
references, no-corpus unknowns and canonical replay without original snapshots.
`test_collection_review_regressions.py` covers independent oversized questions, original
nested baseline paths and alias collisions, strict owner freezes, HTTP replay consistency,
trickling-response deadlines and reordered scored gold; the configured paper-cap control
also verifies reused priority admission. `test_collection_dependencies.py` exercises
shared direct native consent, same-family canonical refusal and remaining-byte read
caps; 0/60/100 provenanced-label controls verify the existing paired-inference firewall.
Real module entry exercises admission/run/inspect/reuse refusal; inert HTTPX
exercises native raw ACK order and operational states. Original top3 names, duplicate
workflow names, missing corpus, independent parent/child choice, shortlist recall,
canonical/time/source-owner joins, cumulative requests+snapshots, closed capabilities,
threshold/insufficient states and whole-branch replay remain explicit.

Recomputed report/hashes/ACK do not replace admitted recording bytes or ownership.
Writer-before/after-write raises never count as ACK. Literal perfect set metrics are
wiring floors, not scientific quality, live-provider, private-library or resource
acceptance. Existing176support/priority tests remain unchanged controls.

## Offline reading-priority comparison (part of #38)

`test_priority_comparison.py`, `test_priority_boundaries.py` and
`test_priority_replay.py` use `test_priority_fixtures.py` public synthetic data.
They exercise real module entry, default no-effect admission, recorded and inert
HTTP execution, exact raw ACKs, native choice versus argmax, malformed neighbors,
model/time/resource failures, independent gold-sidecar ordering, whole-workload
firewalls, actual baseline capabilities and verified effect-free reopen. Publisher
write-then-raise is failure, never readback confirmation. The full support selectors
below remain regression controls for the shared transport/default projection.
No synthetic score, HTTPX transport or CLI test is a live-provider or scientific
quality/promotion study. Priority metric regressions retain class-specific timeout,
abstention and blocked coverage without invented negatives; mixed-provenance
sidecars disclose declared/missing class counts separately from synthetic status.
Same-family canonical duplicates and absent/empty ML training-family provenance
must fail admission before artifacts or requests. `test_priority_dependencies.py`
checks shared native authorization under the four-way projector, per-read aggregate
snapshot limits, already-correct blocked recordings and discriminating 0/60/100
provenance-qualified paired cohorts.

## Recorded-input replay regression

`test_recorded_support_binding.py` replaces a copied terminal and recomputes all
derived rows/report/hashes/ACK while leaving the frozen recording unchanged.
`test_support_replay_states.py` and `test_priority_replay_states.py` additionally
remove terminal references/bytes, forge recorded/native blocked states and request
identities, and preserve actual blocked/missing/wall/malformed-neighbor states and
coverage denominators. Numeric perfect cases remain by-construction wiring only.
Reopening must reject this input-to-output substitution, independently of unsigned
artifact authenticity. Existing recorded/native/error/reopen controls remain.

## Public support boundary regressions

`test_support_boundaries.py` covers direct native consent/guard/work admission, blocked
and wrong-kind recorded calls, remote TLS versus loopback HTTP, aggregate frozen-input
bytes, and 0/60/80 provenance-qualified paired cohorts without shrinking coverage.

## Offline support comparison (#37)

Parent-controlled focused selector (existing pytest installation, no dependency sync):
`pytest -q tests/test_support_comparison.py tests/test_native_decision_cli.py tests/test_support_metrics.py tests/test_native_decision_import.py`

- `test_support_comparison.py`: exact Unicode/repeated-span admission, source hashes,
  gold identity joins, dev/holdout firewall, actual binary baseline/omission preservation,
  unavailable artifacts, exclusive owner, corruption, write-then-raise and reopen.
- `test_native_decision_cli.py`: real parser/handler/HTTPX MockTransport/evaluator/artifact
  assembly; gold sentinels absent from captured requests; model/schema/vector failures,
  bounded raw/partial terminal retention, blocked arms, resource/work caps and production
  write/model-construction spies. Escaped high/low surrogate aliases fail only their
  pair; valid Unicode mismatches and valid neighboring cases preserve their results.
  No recorded or mocked response is a native smoke test.
- `test_support_metrics.py`: hand-computed error/Brier/denominator/class fixtures and
  equal-family binary-loss bootstrap floors; perfect synthetic data cannot imply a winner.
- `test_native_decision_import.py`: isolated import against installed Pydantic and
  absolute frozen-input admission; catches unavailable type imports before app execution.
  A real isolated module-entry subprocess also admits without writes, executes recorded
  responses, inspects without mutation and refuses a second invocation's existing owner.
- `test_support_fixtures.py`: public synthetic on-disk fixture helpers, not production
  data or external human-labelled quality evidence.

Source delivery left these tests UNRUN. The first guarded parent execution hit three
collection errors: Pydantic exports no `AbsolutePath`. The standalone import regression
then failed normally before the correction. Runtime reruns remain separately recorded;
AST parsing alone never establishes runtime success.

Packaging retains the original non-shipped `tools.method_validation.client` transport and `practitioner.run_diagnostic` RSS driver with unchanged source and synthetic tests. These require explicit invocation; no app defaults, live inference, data collection, portable-path fixes or scientific acceptance are added. Existing pinned local test dependencies remain for review.

Original method comparison regression oracles are retained without assertion changes. This source-only packet does not establish compatibility with the newer contracts.

Retain original prefilter-metadata source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.

Search family-identifier regressions cover normalized retained IDs, transitive
merges after JSON reload, positive-key affinity, partial-reader uncertainty, and
persisted refinement with older misses and newly unresolved identities.
Controlled identifiers/readers establish machine contracts, not live-library
completeness or semantic paper equivalence.

`test_discovery_server*.py` covers API/SQL/privacy/race/effect contracts, including
all-response sentinels and alternate-action fences, using synthetic stores and
a controlled provider bridge. Scripted judgments do not prove usefulness.

`test_discovery_provider_*.py` covers inert OpenAlex JSON receipt contracts,
concurrent variant isolation and unchanged narrow ordinary callers for other sources.
Wider arXiv/Europe PMC receipt scope is deliberately excluded from this version.
`test_discovery_recovery_joint.py` and `test_discovery_existing_only.py` cover real
temporary native SQL, strict current no-op ACKs, save gaps, malformed receipts,
WAL visibility and refusal before mutation/backup/pruning. Read-only ACKs remain
available with the connector running; ordinary writes still refuse. These
machine controls do not qualify live providers or semantic recommendation quality.
Discovery's disabled-reranker route regression uses the actual scorer and fails
on any model construction; disabled mode retains real lexical scores. An absent
native reader leaves external membership unknown, never recommended as new.
Held-provider and assessment-upgrade reads also hide private context and default
recommendations until Discovery completes; failed retrieval retains no private view.
Real RuntimeState client construction is covered with prestige off and with/without
an existing cache, avoiding fake dependency objects that hide ownership errors.
Disabled optional metadata clients still prepare the existing cache schema before
Discovery identity, preventing first-client self-invalidation.
Identifier-linked unmaterialized app notes (direct and transitive DOI/arXiv aliases)
are submitted only to the consented model; edits/clears stale and hide prior context.
OpenAlex location-alias controls use real leaf parsing/federation to exclude a
renamed/re-DOIed saved seed by DOI or versioned arXiv location while retaining a
same-title distinct-ID paper. Receipt-on/off and lookalike-host cases are covered.
Candidate identity regressions also distinguish public metadata changes from later
derived operation/question-evidence enrichment; source inheritance is not identity.

Original discovery regression oracles are retained without changing assertions. Source-only retention does not establish current-contract compatibility.

See [architecture verification](../docs/architecture.md#verify-a-change) for the
isolated backend environment, native-library fork caveat and repository gates.
Production code must not import test helpers. Every Python test/helper stays
within the same500physical-line cap as other Python files.

## Disconnected review

`test_sync_keep_server.py` checks no-op receipts, stale choices, full canonical
note/comment evidence beyond outbound caps, and conservative comparison after a
lost online ACK without duplicate field history or native effects.

`test_offline_review_browser.py` and `_offline_review_browser.py` exercise an
owned installed Chrome persistent profile against actual local TCP/FastAPI,
app SQLite and IndexedDB. The fixture stops/restarts only its own listener and
browser, keeps exact outbox UUID/payload observations, and checks canonical
history, immutable replay and both conflict choices. It imports no question
submission backend to obtain offline review coverage.

Opt-in requires `ZS_OFFLINE_REVIEW_BROWSER_SMOKE=1`, explicit
`ZS_OFFLINE_REVIEW_BROWSER_SYNTHETIC=1` and parent serialized-slot admission.
Run through the repository's selected foreground resource guard; environment
flags by themselves are not a memory/process/authorization guard. An installed
browser and a verified built frontend are prerequisites; no browser/model
installation or downloads are a fallback.

All databases, profiles, PDFs, logs and screenshots are owned synthetic artifacts
under data. AI/training/network sources are disabled. The retained review packet
is constructed through the existing cache/identity owner as synthetic input,
not produced or scientifically validated by a model. Connector detection is
suppressed only for the owned synthetic native database. This is not physical
iOS, remote HTTPS/revocation, private-library or real-user native-write evidence.

Frontend fake-indexeddb tests are a separate scope. Keep their async ownership,
transaction-complete ACK and negative/error assertions; they do not establish
service-worker/browser-storage behavior. Returning a mock function from a Vitest
setup hook registers a cleanup—use explicit undefined setup returns. With Vitest
fake timers, assert at explicit awaited clock/act barriers rather than leaving
an unadvanced testing-library async-wrapper timer.

Existing-question review stack: original immutable question storage, revision/sync storage, composite note intents and migrations9/10 are retained here. Original HTTP/UI and native delivery wiring follow separately; this part does not claim complete question submission or native delivery.

Telegram source-review part: original transport/session/credential and runtime code is retained unchanged. This part registers no Telegram CLI commands; its collection/store/worker dependencies are supplied by following original-source parts. No account/session, network collection or live model is exercised by packaging.

Existing Telegram source-review part: saved posts, collector/composer/rendering, model selection and migration11 are retained from the original working source. CLI worker/wiring and combined live-schema/native dependencies follow separately; no live collection, model loading or issue completion is inferred.

Original native source dependencies include the existing condition marker and unchanged direct-editor SQLite fixtures. Synthetic tests are not private-library or native-acceptance evidence.

Original native-preview client source is retained: displayed approval hashes, explicit refresh/force and direct-editor outcomes use the existing backend; no new controls or protocols are designed. Original synthetic client tests remain unchanged.

The original Telegram private worker/auth command registration and combined schema13 are retained here. Only fake-child command composition is exercised by packaging; armed lifecycle/platform/account tests remain unchanged and deferred.

Original question API/sync source is retained: immutable save/replay before Q&A, separate receipts and existing composite-note delivery. Related/discovery endpoint additions are not mixed into this question slice. No new requirements or acceptance are implemented.

Retain original question-native source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.
Retain original interpretation source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.
Retain original api-storage source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.
Retain original infrastructure source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.
