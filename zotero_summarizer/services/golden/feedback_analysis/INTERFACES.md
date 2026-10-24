# Feedback contract — version 1

This contract covers input capture/replay, retained-data analysis, manual report
operations and opt-in weekly scheduling. Version-1 identities and wire fields are
unchanged. The [README](README.md) describes current execution and recovery;
source/contract tests are not installed-host or scientific acceptance.

```
verified retained SQLite + CSV + JSONL
                  |
 history.py: strict parsers + current family graph
                  |
 analysis.py: separate journal / log / current-state observations
                  |
 metrics.py: unique-paper descriptive cohorts + explicit gaps/nulls
                  |
 analyze_inputs -> local dictionary (no publication or live writes)
```

## Analysis boundary

`analyze_inputs` returns a dictionary without publishing a report. Manual and
weekly dispatchers own the dedicated worker and publication boundaries below.

CSV physical byte lines are consumed lazily before record-budget expansion;
there is no whole-file line/offset list. SQL item/alias identities must be nonblank
text and journal `source` is required; incompatible rows/schemas fail admission.
Aware offsets outside UTC's representable datetime range remain located
invalid/unknown times. The other frozen envelope, CSV-isolation and provenance
limits below are unchanged.

## Entry and failures

`analyze_inputs(destination: Path, *, budgets=None) -> dict`

`budgets` is `AnalysisBudgets`, a known-field dictionary or `None`. No live
Settings/repositories/model opener is called. Verification runs before and after
parsing; replay needs only the retained bundle. Inventory/path/schema/hash errors,
SQLite/read failures, invalid config and exhausted bounds raise. They never become
optional-empty success. Concurrent adversarial write-and-restore/namespace races
are not excluded by two verification passes; authentication is not claimed.

| Budget | Default / hard cap |
|---|---|
| max_records | 100,000 total SQL rows + CSV records/header + JSONL lines |
| max_source_bytes | 256 MiB per retained source |
| max_line_bytes | 1 MiB per physical CSV/JSONL line |
| max_decoded_bytes | 64 MiB conservative cumulative raw/decoded representations |
| max_csv_field_chars | 64 Mi Unicode codepoints per parsed CSV field, including header |
| max_output_bytes | 64 MiB canonical result JSON, including digest |
| wall_cap_seconds | 60 seconds / 600 seconds |
| split_at | aware ISO timestamp, normalized UTC; default cutoff minus 30 days |

Integer limits may only be reduced; booleans, unknown keys, nonfinite/invalid
values raise. `ZS_FEEDBACK_ANALYSIS_WALL_CAP_SECONDS` overrides the wall default;
malformed env is rejected even if an explicit budget overrides it. Wall cap is a
safety net, not a work budget. Existing verification has its own frozen limits
and freeze wall env. Blocking I/O and library calls require an external process
cap for a hard deadline. A configured split after cutoff raises. No new runner.

### CSV bounds and mandatory consumer isolation

Before CSV parsing, raw bytes consume the decoded budget. UTF-8 field characters
cannot outnumber those bytes. The finite stdlib parser ceiling is therefore
`max_decoded_bytes`, independent of entry ambient `csv.field_size_limit`. Each
parsed header/row field is also checked against `max_csv_field_chars`; a smaller
cap raises `BudgetExceeded`, never a malformed-record success that drops a valid
tail. `config` records `csv_parser_ceiling_chars` and `csv_effective_field_chars`
(the minimum of the two declared caps). Genuine strict quote damage still yields
the original malformed remainder line/byte locator, without resynchronization.

A deadline-aware lock serializes **owned** CSV parser calls, not unrelated stdlib
readers/setters. The saved entry ambient limit is restored on success, syntax
failure, budget failure and cancellation while the global still equals the owned
ceiling. An **observed** outside change raises `RuntimeError` (restoration
unavailable), preserves that foreign value and retains original exception context.
Unrelated readers can observe the temporary ceiling. Outside change-and-restore,
a setter choosing the same ceiling, and a setter racing between the final check
and restoration cannot be isolated/detected; stdlib supplies no compare-and-set.
This function is **not safe for arbitrary concurrent shared-process CSV consumers**.

**Required downstream acceptance:** actual manual CLI/weekly analytics must run in
a dedicated externally bounded **analysis process**, with no unrelated CSV
readers/setters, not a shared API/daemon CSV thread. This patch does not implement
or prove that isolation. No broader reader/scheduler/CLI changes are authorized.
Verbatim P2 approval and race clarification are retained in
`data/issue-validation/feedback-analysis/p2/parent-decision.txt`.

## Manual operation

`goldenset analyze-feedback` uses the dedicated worker/publication dispatcher.
Weekly scheduling reuses this operation; neither path changes the analytic envelope.

## Exact envelope

Top-level keys are exactly:

```
schema_version: 1
input_content_digest: full verified retained-input SHA-256 identity
cutoff: manifest UTC cutoff
provenance: unchanged manifest-declared code/config/environment/seed/unknowns
revision_bounds: retained manifest sync_changes availability + min/max
config: all resolved bounds + split_at + split_default name
rules: versioned identity/time/label/prediction/CI interpretations
coverage: sources, tables, raw counts, unique current paper groups,
          content multiplicity, exclusions, date coverage, snapshot profiles
papers: [current canonical families + alias proof locators + snapshot references]
events: [local normalized observations, one per accepted source record]
periods: {last_7d, last_30d, all_time}
chronological: unique-paper eligibility partition; no scored evaluation
ranking: null P@10/NDCG@10 + unscorable reasons
gaps: [{reason, locator?}]
result_digest: SHA-256(canonical UTF-8 JSON of all other fields)
```

Canonical JSON uses sorted keys, compact separators, Unicode preserved, no NaN.
Result identity includes configured limits and every located observation/gap;
there is no generated clock or invented inference model. Source capture metadata
remains accessible in the input manifest. Unknown result versions require explicit
consumer rejection, not fallback interpretation.

### Evidence and coverage

Each event includes `observation_id`, `locator`, `content_group`, `paper_key`,
`item_key`, `identity_status`, `channel`, `kind`, normalized `time`, `time_basis`,
`recorded_time`, `eligibility`, prior/value, source/surface, saved prediction/null,
actual local comment, revision/null, reconciliation, event-time gate identity and
null historical inference-model identity. Optional fields retain recorded
categories, feed name or an unconfirmed overlap group.

- Observation IDs identify **source locators**, not human actions. JSONL locators
  carry line and exact byte offsets; CSV carries multiline line/byte ranges;
  SQL carries retained path/table/row ID or revision.
- Full-record `content_group` identifies equal content only. Multiplicity and all
  locators remain. Identical legitimate repeats are not erased as duplicate actions.
- Malformed/torn JSONL and duplicate JSON keys/nonfinite values are counted and
  located. Unknown schema/event records are counted separately. Wrong known-field
  types are malformed. Missing prediction is null; missing/unproven time excluded;
  missing source cannot become an explicit preference. Blank lines are malformed.
- CSV requires unique nonempty columns including item_key, exact-width nonblank-key
  records and strict quoting. Missing/invalid headers and torn rows are located;
  a quote failure marks the unreadable remaining record, not invented resynchronization.
- Optional SQL tables are checked once at the boundary. Absence is unavailable;
  incompatible present columns raise. No migration/backfill/writable store opener.
- `snapshot_profiles` counts unique paper/category pairs from **recorded structured
  tags**, labels, feed names and final outcomes. They describe the retained current
  snapshot, including potentially post-cutoff state, not historical topic/source
  change, effective training labels, exposure or preference. Comments are not mined.

### Canonical and event semantics

Reuse `storage/review_notes.py::_keys`'s explicit edge rules in one bounded graph:
stored legacy alias; unambiguous positive numeric feed ID; stable feed/materialized
library key; processed-row key. Reuse `storage/feed_identity.py::row_feed_keys`.
Conflicting numeric candidates or the ambiguity table quarantine the numeric key,
even if a stale alias is present. DOI/arXiv stable_id joins require a single
retained family; conflicts stay unresolved. No title matching or fuzzy merge.
Canonical spelling prefers a stored/computed stable feed key. Current mappings
prevent cross-partition alias reuse but do **not** establish historical identity
availability. Ambiguity marker JSON must be a list of exact stable-feed strings
accepted by the existing identity predicate; malformed JSON/type raises at the SQL
boundary. Recorded alternatives and retained path/table/row proof survive without
processed rows or observations, including stale aliases. Alternatives are **not
union edges**. Paper records include available ambiguous candidates and
`retained_latest_journal` locators, including tombstones; these references are
retained revision state, not an inferred cutoff snapshot.

Journal revisions are authoritative **category/retraction** evidence where explicit
source=user is recorded. First observed prior is unknown; machine rows never
supply an earlier user category. A same-category revision is a non-category-change
observation; rationale/origin edits cannot fabricate reversals. Earlier unknown,
ineligible or clock-inconsistent rows cannot supply a temporal prior. Current
verdict/note/feedback tables are not complete trajectories.

JSONL schema 1 lacks durable event/revision identity. Same-paper/value/comment/time
within the same recorded second yields only `consistent_unconfirmed`, never
confirmed identity or restored prediction/context. It neither merges nor discards
observations. A missing append can leave a durable journal category change, but its
prediction, surface, inference identity and earlier-history context remain unknown.

Never sum journal, log and current-feedback channels as total decisions.
Human priority/trash/keep observations remain inspectable auxiliary evidence, not
extra preferences beside label transitions. Independent approve/reject feedback
is a distinct binary observation cohort. If ordinal and binary observations cover
one log paper, ordinal support is preferred in the combined latest-paper summary;
binary class support is reported separately, never added. machine_add is provisional;
auto_quality, outcome Trash and collection removal are not explicit preferences.
A missing action/review failure is not a negative label.

### Temporal and numerical semantics

Only aware recorded timestamps enter `[cutoff-window, cutoff]`, including exact
7/30-day boundaries. Naive/missing/invalid/date-only fields remain unknown.
Even SQLite UTC defaults/triggers do not prove the individual timestamp's origin;
this stage conservatively excludes **all** naive/backfilled timestamps from
cutoff claims. No host timezone, browser/navigation/device clock is inferred.
CSV labels, comments, predictions and current result fields never enter historical
comparison or features. Saved feedback original_priority and supplied JSONL model
priority remain unchanged; invalid/missing predictions are null, never filled
from retriage/current models. Event gate_sha and classifier-run metadata are not
historical inference identity.

Each period has separate journal/log/current-feedback summaries, raw observation
counts, latest unique-paper/class support, recorded transition matrices, saved
prediction disagreements by locator, binary agreement and Wilson 95% CIs, source
and surface breakdowns. Category/retraction/reversal **lower bounds** are journal
only; JSONL action lower bounds are null, with raw transition-observation matrices
and multiplicity. Same-time incompatible latest observations are reported as
conflicts rather than chosen arbitrarily. Mean/median summarize absolute **recorded
priority rank** error, with surface breakdown; no competence-overall scalar.
CI denominator is unique observed papers (or matched predicted papers for agreement),
not unbiased population performance or confidence that data are sufficient.

Chronological partition uses first eligible explicit observation per current
canonical family against named split_at; later observations of earlier papers
cannot enter the later group. Future/unproven labels/comments/items are excluded
from eligible observation locators. This is eligibility metadata, **not scored
chronological evaluation**. Historical features, exposure and inference identity
remain unavailable. Raw excluded evidence stays local and located.

Historical topic changes are unscorable without historical recorded categories.
Current schemas have no immutable exposed ordered slates or fully timed slate
judgments: P@10/NDCG@10 are null with those exact reasons. Triage rank/batch fields
alone are not exposure. No unbiased improvement claim, scheduler, CLI, Markdown,
publication, cursor, model fit, label mutation or overall #47 acceptance.

## Weekly operation

The part-1/2/3 implementation notices above are prerequisite review records.
This head implements manual and opt-in weekly entrypoints; all input, analysis,
report and operation safety contracts remain. This is not issue #47 acceptance.

The weekly runtime validates cursor paths before existence checks. Its aggregate
wall cap must strictly exceed the fixed operation cap before schedule output.
Without a cursor, the oldest retained active intent is selected by a work/byte/
safety-bounded inventory before newer closed cutoffs. Catch-up remains bounded.
Missing/failed candidate success receipts HOLD. Before candidate creation only,
a matching persisted no-publication failure, a reaped/absent child group and
an absent destination permit a fresh bounded attempt; this negative evidence
is never report confirmation or cursor permission. Unknown evidence still HOLDs.
Only explicitly tagged owned analysis SIGINT/SIGTERM interrupts are clean daemon
shutdowns after drain, without tick/cursor; unrelated errors propagate.

These amendments supersede only the inherited weekly activation/retry/signal
wording. No wire envelope, hash identity, live mutation, authentication or
scientific guarantee changes; old attempts and immutable reports remain intact.

## Reference/method binding

- CLOSE recorded clocks/locators and explicit unknowns: ARE
  `/Users/vladnikulin/code/from GH/meta-agents-research-environments/are/simulation/replay.py:18-79`.
  DIVERGE from side-effecting replay and continue-on-error; this consumer is read-only
  and fails on integrity/schema/I/O errors. DEFER simulation/judges/framework.
- CLOSE retained dataset/config/revision provenance: Inspect EvalSpec:1092-1177,
  frozen public source `a1dbea8db3cc85e04ff532760d0eaaeb64df6d3a`, MIT; receipts in
  `data/issue-validation/feedback-analysis/references/`. DEFER adopting framework.
- arXiv `2610.11858v1` and `2610.11754v1` abstracts: evidence linkage and approved
  claim boundaries, not empirical superiority for this app. Raw XML retained.
- UX source: supplied issue47 public Laws-of-UX basis; internal result only. Occam
  subtracts framework/dashboard, Cognitive Load keeps one result boundary, Tesler
  defers live coordination to the parent-owned later phase. No new UI/CLI surface.

## Parent contract approval (verbatim)

APPROVE the three-file responsibility split, public analyze_inputs(destination:Path, *, budgets=None)->dict + named validated AnalysisBudgets/caps/env safety-net and version1 envelope, retained-only RO/no publication, with these load-bearing corrections BEFORE writes. Persist this decision verbatim in assigned-root evidence + INTERFACES contract for reviewer.
1) NO confirmed revision↔JSONL identity from same-second/value/comment matching. Schema1 has no durable event ID: mark unique exact-content/time candidates merely consistent/unconfirmed, don't promote to causal identity or reconcile away discrepancies. Journal authoritative for category/retraction trajectory, JSONL observations/current feedback distinct channels; never sum overlapping channels as total decisions. Report lower bounds and raw observation counts/gaps.
2) Full-record digest is CONTENT group, not durable event ID. Preserve each distinct source/line/row locator and multiplicity; identical legitimate repeat actions can exist. State machine same category may not create a new category transition, but don't delete rows or claim dedup identity. Dedup only shared persisted revision/mutation ID where actually present.
3) Parent opened _repo_feedback.py:38-45 SQL omits created_at on INSERT and sets datetime('now') on UPSERT; _repo_sync.py triggers omit changed_at. A column default ALONE does not prove actual individual origin (explicit/backfilled values possible). SQLite bare UTC is allowed as clearly labelled APPLICATION UTC storage-contract interpretation only with retained schema/trigger compatibility AND actual normal producer proof, not historical inference/clock proof; keep ambiguity/assumption in time_basis/gaps. Other naive dates unknown. Regression explicit ambiguous/backfilled timestamps must remain excluded from temporal claims absent proof. No host timezone/current environment guessing.
4) Chronological eligibility metadata only for this stage is ACCEPTED BUT does not satisfy scored chronological eval/overall47. Reject arbitrary 'midpoint cutoff-30d' phrasing. Declare a named, recorded configurable split boundary (UTC supplied or named default cutoff-minus30days, not midpoint), clear unique-paper group assignment and future label/comment/item exclusion proof. Current-only aliases/topics/history can't establish historical exposure or features; mark unscorable/unknown where required.
5) Ranking null with exact missing immutable-exposure/timed-judgment reasons ACCEPTED for current producer schemas; never claim P@10/NDCG done from this null. CI denominator clearly unique observed explicit papers/class prevalence/agreement, selection-biased sample, not confidence in sufficiency/unbiased model improvement. Mean/median only meaningful numeric metrics, no manufactured scalar competence.
6) Keep raw observations/comments/locators LOCAL, minimal result evidence not duplicated entire SQL rows in several arrays; work/output/decoded-byte bounds must prevent multiplicative expansion and quadratic matching. Missing optional history explicit; unknown/schema/programmer failures propagate, not optional-empty fallback. Existing verified bundle identities/signatures unchanged. No producer/schema/lifecycle/CLI/ledger edits; parent's later stages own these gaps.
Proceed owned synthetic RED/GREEN + foundation preservation. Source proof via exact fields/tests, no language meaning regex/keyword grading. Escalate narrowly if data schema invalidates this contract, don't increase files/budgets or invented history.

## Additive operation sidecars — version 1 (analytic envelope unchanged)

Public dispatcher: `run_feedback_analysis(settings, *, cutoff: datetime,
provenance: dict, budgets=None) -> dict`. Dispatcher-only caller provenance is a
seed-only allowlist; no caller code/config/environment values are forwarded.
`OperationBudgets` supplies resolved operation safeguards, with nested existing
freeze/analysis budget dictionaries. The CLI is exactly existing-group
`goldenset analyze-feedback --project-root ROOT [--cutoff AWARE_ISO]`. No startup,
scheduler, cursor, model fitting, source setter or broader command family.

The dispatcher writes a private attempt request, launches `sys.executable -I`
with an explicit actual package root, and never analyzes in its own process.
The clean offline worker hashes code/config, captures `run-staging/retained`,
uses the unchanged analytic envelope, then writes JSON/Markdown and a sidecar.
Nested retained inventory preserves the frozen verifier's exact two-entry root.

Output manifest keys are exactly `schema_version, attempt_id, run_id, status,
outputs, input_content_digest, result_digest, cutoff, code, config, environment,
seed, analysis_budgets, gap_reasons`. `outputs` maps each retained input/original
manifest/result JSON/Markdown relative path to full `sha256` and `byte_size`.
Unknown versions/inventory/hash mismatches fail; no permissive interpretation.
Child protocol keys are exactly `schema_version, attempt_id, run_id, phase,
status, error_class, pid, import_root, output_manifest_sha256, peak_rss_bytes`.
No raw exception/config/environment/secret values are serialized. The original
child exception's safe type/phase is retained and a known type is re-raised at
parent boundary (unknown classes become ChildProcessError); the child exits
nonzero. Parent exceptions carry the computed failure receipt before persistence I/O.
Failure-only `receipt_persistence` is `{status:not_written|written|failed,
error_class:null|safe secondary class}`. In-memory `written` requires bounded
write-return; the file candidate carries written/null. Failure-file OSError records
failed/class and preserves the exact original exception/ownership. Non-I/O secondary
failure is classified and propagated loudly with the same receipt attached; original
exception context also retains it. CLI exposes this metadata, not raw messages.
`receipt_path` is attempted, not guaranteed usable; failed does not prove absence
(atomic replacement can precede later cleanup/I/O failure), nor fsync durability.

Receipt status: `published` or `insufficient_data` after bounded validation and
Darwin fail-if-exists atomic rename; `failure` on exceptions. Both legitimate
published statuses include output hashes and concrete gaps. Distinct attempt/run
IDs and full code/config/input/result identities support a later idempotency
owner, but this slice does not implement it. No status asserts sufficient unbiased
evidence, scored chronology/ranking, scientific usefulness or overall issue success.
The published tree is logically read-only (0500/0400); failures retain inspectable
attempt/staging artifacts. New receipt `published` is bool|null: true only for a
known owned commit (or staged device/inode observed only at destination), false when
owned staging remains, null when both/neither match or observations are inaccessible.
Foreign destination existence never proves ownership. Known owned commit survives
later chmod/receipt failure as `publication_state=published_but_not_confirmed`;
null uses `publication_state=unknown`, with optional safe
`publication_observation_error`. Neither failure nor unknown is confirmation/cursor
permission. Identity is private cooperating-namespace observation, not hostile-user
or inode-reuse authentication. Frozen analytic/input contracts are unchanged.
Parent loss/SIGKILL/power-loss may leave only partial attempt
evidence. Unsupported publication platforms fail rather than overwrite. See
[README](README.md#manual-operation)
for named caps, environment precedence, signal and safety measurement scope.

## Additive weekly sidecars — version 1

Final commit.json keys: schema_version,attempt_id,run_id,content_key,operation_receipt_sha256,schedule_receipt_sha256,directory_identity{device,inode}. Owned native finalization follows acknowledged writers; publisher AND active-attempt proofs are required. Prepared state HOLDs.

Manual `run_feedback_analysis` signature/payloads and all frozen result/input/worker
schemas above remain unchanged. New sibling:
`run_scheduled_feedback_analysis(settings, *, cutoff, provenance,
schedule_intent: dict, budgets=None) -> dict`.
It is main-thread only and shares the existing private dispatcher path; static
schedule-state helpers run after bounded `verify_report`, before `_publish`.

Intent exact keys: `schema_version,intent_id,window_start,cutoff,schedule_rules_sha256`.
Cutoffs are Monday00UTC, width7d, ID `weekly_YYYYMMDDT000000Z`. Candidate exact keys:
`schema_version,intent,content_key,attempt_id,run_id,run_path,
operation_receipt_path,output_manifest_sha256,payload`. Candidate/key records precede
publication; immutable attempt references + bounded active pointer provide recovery
without unbounded directory scans. Paths must match selected Settings and recorded
IDs. Content keys bind full input/code/config/environment/seed/window/rules, never
attempt/run IDs or clocks. Unknown wire versions/paths/hashes fail loudly.

Scheduled receipt keys: `schema_version,intent_id,window_start,cutoff,status,
publication_action,owned_published,confirmed_report,content_key,run_path,
operation_receipt_path,schedule_receipt_path,late_observations,recovery,source_identity`.
`status` retains `published|insufficient_data`; no scientific sufficiency claim.
`committed/owned_published=true` requires successful original publication/receipt.
`reused/owned_published=false/confirmed_report=true` describes a prior verified
confirmed report, not a current owned commit. Recovery completion is explicitly
`recovery=true`, not a new capture. Missing or failed original receipts HOLD; never
infer success from an existing destination or retry equivalent publication.

Async `run_due_feedback(settings, *, now, budgets=None)` requires explicit selected
Settings/main-thread ownership. Disabled performs no state I/O. First activation
selects latest closed cutoff; catchup is arithmetic oldest-first, max2/default,
cap4. A fixed operation cap is reserved from the configured aggregate safety budget.
`no_due` does no snapshot/source rehash. `no_new_data` means equal captured source
hash/status/size identity, not a skipped analysis or unchanged live-source proof.
Cursor commit follows durable scheduled receipt and a cancellation-delivery yield.
The cursor envelope is exactly `schema_version,activation_cutoff,
last_confirmed_cutoff,last_receipt,status`. Cursor is authoritative; status-file
precommit diagnostics are not proof of cursor commit. Task cancellation drains the
synchronous owned process before yield; immediate shared-event-loop interruption
is not claimed. Signal restoration, no fsync and post-rename ambiguity caveats apply.

All-time retained evidence handles late old dates. Late sidecar count is raw retained
observations at/before prior cutoff, not newly arrived action count. Its result path
preserves locators and multiplicity. Durable journal revisions stay distinct from
CSV/JSONL content groups. No extra timer/command/callback/runner, server/thread
activation, model or label mutation. See additive weekly README for exact settings,
private artifact layout, mode checks, HOLDS and remaining installed-host scope.
