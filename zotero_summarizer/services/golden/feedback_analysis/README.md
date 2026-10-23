# feedback_analysis — retained inputs and report primitives

## Available in this PR (stack part 1/4)

The callable input boundary is `freeze_inputs` / `verify_inputs`, exported by this
package. `metrics.py` and `report.py` retain supporting primitives for dependent
parts; there is no integrated analysis, report operation, CLI command or weekly
scheduler in this checkout. No overall issue #47 completion is claimed.

`report.__all__` explicitly declares the existing low-level library boundary:
`hash_file`, `inventory`, `make_immutable`, `markdown`, `read_protocol`,
`stage_report`, `verify_report`, `write_private`, `write_protocol`. Direct callers
supply a version-1 result, attempt/run identity and bounded `guard.consume(bytes)`;
this is not an installed-host operation or analyzer. `stage_report` writes only
its caller-owned staging tree; `make_immutable` changes that tree's modes. Keep
raw retained inputs/results local and do not point these helpers at live sources.

Report evidence uses code-span delimiters longer than any recorded backtick run,
so recorded surfaces/locators remain literal text rather than active Markdown.

**Downstream reference only:** the analysis/manual/weekly flow, contracts and
sections below describe later stack parts, not functionality available here.
`history.py`, `analysis.py`, operation/worker/scheduler modules and
`goldenset analyze-feedback` are absent. Do not run those reference commands or
bypass the future clean-child isolation requirement. For this part, exercise the
real Python `freeze_inputs(settings, new_destination, cutoff=..., provenance=...)`
and `verify_inputs(destination)` boundary described below.

```
Settings sources --ro/WAL-aware backup + bounded EOF reads--> NEW private staging
                                                          inputs/ + input-manifest.json
retained bytes --verify_inputs--> analyze_inputs --> local result dictionary
manual / opt-in weekly --clean child(capture + analyze)--> immutable report + receipt
```

## Current checkout — stack part 2/4

The **part-1 availability block above is the prerequisite #162 reference**, not
this head's scope. This checkout additionally implements and exports
`analyze_inputs(destination, *, budgets=None)`; `history.py` and `analysis.py`
are present. `freeze_inputs`/`verify_inputs` and the report primitives remain
available with their safety contract above. Manual/weekly dispatchers and the
`goldenset analyze-feedback` command remain absent; do not run those later-stack
reference commands. No overall #47 completion is claimed.

Analysis must run in a dedicated externally bounded process because its temporary
CSV parser ceiling is process-global. Physical byte lines/positions are consumed
lazily, so the record cap stops short-row inputs without a full line/offset list.
SQL observation/alias keys must be nonblank text and a present journal must have
`source`. UTC-conversion overflow remains located invalid/unknown time, not a
whole-analysis failure. Snapshot duplicate-namespace refusal and literal report
Markdown remain intact.

Current coverage also includes `tests/test_feedback_analysis_{history,metrics,analysis,
conflicting_cohorts,sync_sources}.py` and the eight analyzer boundary regressions.
The later text's absent-history/analysis-suite statements describe part 1 only.
These library tests and synthetic smoke are not operation/weekly, production,
scientific or private installed-host acceptance.

`freeze_inputs(settings, destination: Path, *, cutoff: datetime, provenance: dict,
budgets=None) -> dict` creates a new directory beneath
`settings.feedback_analysis_dir` (`data/feedback-analysis`). It refuses existing
bundles, source overlap, parent traversal and symlinks. Optional absent files are
explicitly missing; read, schema, integrity, mutation and budget errors propagate.
Only successful exclusive destination creation arms cleanup: initialization
(including `inputs/` creation) and capture failures attempt removal of that owned
staging directory; no usable manifest is returned. Preexisting targets are never
removed. Cleanup errors propagate with the original failure as exception context.
Sources and sibling bundles are never overwritten/chmodded/migrated.

`verify_inputs(destination: Path) -> dict` validates the version-1 wire format,
exact artifact inventory, full retained SHA-256/size, content identity and retained
SQLite metadata. It never reads source locators. It refuses incomplete, changed,
extra or symlinked artifacts. This is integrity checking, not authentication
against an attacker who can replace the bytes **and** recompute the manifest.

## Current checkout — stack part 3/4 (manual CLI)

The part-1/part-2 availability blocks above are prerequisite #162/#163 references,
not this head's scope. This checkout also provides `goldenset analyze-feedback`
and the dedicated operation/worker that freezes inputs, analyzes them, validates
hashes, then atomically publishes an immutable private report and receipt. The
manual-operation section below is available here despite its inherited
later-stack heading; weekly scheduler activation remains unavailable. No overall
#47 completion, installed private-data acceptance or production claim is made.

The source-checkout command requires Git HEAD, `pyproject.toml` and `uv.lock`;
a standalone wheel without that provenance fails before capture. Its worker
suppresses source-tree bytecode writes. Signal ownership covers success/failure
receipt persistence; main-thread SIGINT/SIGTERM are deferred during child launch
until cleanup is armed, and the child unblocks the inherited launch mask.
Completed-worker RSS peaks must be positive integers within the configured cap,
in addition to live tree sampling; this is not instantaneous OS RSS enforcement.
The operation I/O bound counts provenance/protocol/report artifacts per guard,
not whole-process or shared parent/child cumulative I/O. Independent capture and
analysis source/page/record/byte/work caps are unchanged. These current controls
supersede the older operation wording retained below as prerequisite reference.

Parent lazy CSV, UTC-overflow, journal-source/SQL-identity, literal Markdown and
duplicate-namespace protections are preserved in the actual manual CLI path.

## Capture and identity

| Source key | Retained path | Capture boundary |
|---|---|---|
| `triage_db` (required) | `inputs/triage.sqlite` | Existing read-only URI + query_only + SQLite online backup, including committed WAL pages |
| `golden_csv` | `inputs/golden.csv` | Opened-file bytes; an atomic replacement can retain the complete older inode |
| `interaction_log` | `inputs/interaction-events.jsonl` | Prefix through EOF observed at open, including torn/malformed records |
| `classifier_runs_log` | `inputs/classifier-runs.jsonl` | Same bounded-prefix contract; Settings path matches existing ledger producers |

Every source records status, relative path/null, full SHA/null, captured byte
size, source locator, UTC capture interval, EOF offset/null and consistency caveat.
Database integrity, canonical schema SHA, user_version, page bounds, available
numbered namespace versions and sync revision min/max come **only from retained
SQLite**. Old absent migration/sync tables are explicitly unavailable/null.
Admission requires core triage result, feedback and verdict reading columns, not
a current migration floor. Present migration metadata must have unique namespace
rows; equal or conflicting duplicate versions are rejected rather than collapsed. An uninitialized/empty-file store is refused, not
successful empty history; it cannot establish absence of decisions elsewhere.
No live repository opener or writer backup is used.

Cutoff must be aware UTC. Caller declares already-secret-free JSON provenance
with `code`, `config`, `environment`, `seed`, and any **available** inference
identity/unknowns. These functions do not inspect configuration, environment
contents, Git, gate/model state, or guess historical inference identity. The one
budget environment override below is read, not retained. Schema version, named
status/hash/size, cutoff and the **full** declared provenance determine the full
`content_digest`; capture clocks and source locators do not.

**Not a cross-file atomic transaction.** Capture can occur after logical cutoff.
Later analysis must reconcile/filter records by cutoff and revisions, preserving
unrecoverable gaps. CSV historical row timing remains unknown. Logs are checked
with two bounded reads of the same opened prefix; incompatible observed mutation
fails. There is no lock protecting against adversarial write-and-restore or
same-user namespace races. SQLite may use its normal WAL shared-memory read
coordination; this layer performs no explicit live-file writes or mode changes.
It requires ordinary read-only WAL access, never live `immutable=1`.

## Budgets and permissions

`FreezeBudgets` or a known-field dict accepts smaller work bounds:
`max_source_bytes` (default/max 256 MiB per source, including SQLite main+WAL),
`max_snapshot_pages` (65,536), `backup_pages_per_step` (128), and
`max_progress_callbacks` (4,096). These caps are provisional safety limits,
not measured installed-host requirements. Callback count also bounds busy/restart
backup work. Copy/hash reads stream in 64 KiB chunks; manifests are capped at 1 MiB.

The single safety wall cap has named default 60s/max 600s and env override
`ZS_FEEDBACK_FREEZE_WALL_CAP_SECONDS`; `budgets.wall_cap_seconds` takes precedence,
but even an overridden malformed env value raises. Verification uses default
work limits and the same env wall cap. SQLite VM progress, backup callbacks and
file-chunk boundaries check the cap. Blocking filesystem calls cannot be forcibly
interrupted in-process; callers needing a hard process deadline must provide it.
New directories/files request POSIX 0700/0600. No source permissions change.
Atomic manifest replacement reuses `services._common.atomic_write`; this is not
power-loss durability (no fsync). These low-level frozen functions do not publish
reports or advance a cursor. The manual/weekly dispatchers documented below own
those boundaries; installed-host evaluation and the first private run remain
unverified. No model/label change or issue closure follows from these functions.

## Responsibilities and checks

- `inputs.py`: boundary/path admission, work/safety guard, file capture, replay verification.
- `sqlite_snapshot.py`: WAL online backup and retained schema/revision metadata.
- `provenance.py`: JSON wire validation and deterministic identity.
- Later parts only — `history.py`: retained readers and ambiguity-safe current alias graph (absent here).
- Later parts only — `analysis.py`: bounded retained-only `analyze_inputs` result (absent here).
- `metrics.py`: separate unique-paper descriptive channels, Wilson intervals and eligibility metadata.
- `__init__.py`: exported boundary functions and budget/error types.

The version-1 analytic contract is frozen in [INTERFACES.md](INTERFACES.md).
`AnalysisBudgets` caps records, source/line/decoded/output bytes, CSV field characters
and a configurable safety wall. Cutoff/declared provenance come from the verified
manifest; no live source locator is opened. Replay needs no original stores.
Marker-only ambiguity candidates and SQL proof remain inspectable without unioning
alternatives or trusting a stale alias. Invalid marker JSON/type fails at admission.

CSV uses a finite parser ceiling derived from the prechecked decoded-byte budget,
with an explicit `max_csv_field_chars` cap (default/hard cap 64 Mi codepoints) on
header/row fields. A lower cap raises `BudgetExceeded`; genuine damaged quoting
remains a located malformed remainder. Resolved config records the parser ceiling
and effective field cap. The same input/config produces the same digest under
different entry ambient CSV limits **when no unrelated CSV consumers interfere**.
Owned calls serialize with deadline-aware locking. Normal/error/cancel exits
restore the saved ambient limit; observed outside changes refuse and preserve the
foreign setting. Other readers see the temporary ceiling and are not isolated.
Outside change-and-restore, same-value setters, and races between final check and
restore are unsupported/unverifiable, not a complete no-clobber guarantee.

**Downstream acceptance requires manual CLI/weekly analytics in a dedicated,
externally bounded analysis process with no unrelated CSV readers/setters, NOT a
shared API/daemon CSV thread.** The manual/weekly dispatcher below supplies that
clean-child isolation; callers must not bypass it.
See [INTERFACES.md](INTERFACES.md) and the verbatim approval/race clarification in
`data/issue-validation/feedback-analysis/p2/parent-decision.txt`.

Retained `label_transition` events recognize the exact existing server-producer
sources `offline_sync` and `sync_conflict_resolution`, alongside `user`/`zotero`.
This compatibility is transition-only: either sync source on `human_feedback`
remains `nonpreference`; outcomes stay outcomes and `machine_add` human feedback
stays provisional. Raw provenance is retained, not rewritten or authenticated.
Existing reports are never recomputed in place; newly analyzed support/result
digests may change without changing input hashes or reconciliation guarantees.

Journal revisions establish label-category/retraction evidence, not lost prediction
context. JSONL has no durable event IDs: equal content/time is only unconfirmed
consistency, with multiplicity/locators retained. Journal/log/current-feedback
channels are never summed as total decisions. Saved predictions are not replaced
by retriage or current models; comments remain local evidence, never topic inference.
Naive/backfilled timestamps are excluded because per-row origin is unproven.
Current aliases/categories/outcomes describe snapshots, not historical features.
Chronological partitions are eligibility only. Historical topic changes and
P@10/NDCG@10 remain explicitly unscorable without recorded history/exposure.
Confidence intervals describe selection-biased unique observed support, not
unbiased improvement or sufficient installed-host data.

In mixed ordinal/binary log evidence, a paper with conflicting latest ordinal
observations remains a conflict, not a binary substitute in combined support or
agreement. Its binary class support remains separately reported. A later
unambiguous ordinal observation resolves the conflict through the existing latest
selection. No observation, timestamp, transition matrix or wire envelope changes;
existing reports are not rewritten.

Synthetic `tests/test_feedback_analysis_inputs*.py` cover numbered empty/populated
stores, committed WAL preservation, unavailable old history, missing versus broken
optional reads, replay after source removal, capture races, tamper/escape, budgets
and failed publication. Existing golden CSV snapshot/hash checks remain separate.
`tests/test_feedback_analysis_report_primitives.py` covers safe Markdown evidence
encoding. History/analysis/metrics suites described by later parts are absent here;
this checkout does not establish their integrated behavior or coverage.
Further subtraction: no parsing framework, inferred model identity, live-store
alignment lock, scored training evaluation, export knobs or UI. Weekly automation
below reuses the operation boundary without changing these frozen analytics.

## Manual operation — later-stack reference, unavailable here

```
goldenset analyze-feedback --project-root ROOT [--cutoff AWARE_ISO]
  -> operation: private attempt + single-run flock + externally guarded child
  -> worker: byte-hash code/config -> freeze retained/ -> analyze -> stage JSON/Markdown
  -> operation: bounded inventory/hash validation -> atomic no-replace rename
  -> one receipt (published / insufficient_data / failure); no cursor
```

`run_feedback_analysis(settings, *, cutoff: datetime, provenance: dict, budgets=None) -> dict`
uses only a dedicated child for capture and analysis, including the temporary CSV
parser ceiling. No unrelated CSV readers/setters run in that child. Parent API/daemon
threads must call this dispatcher, not `analyze_inputs` on shared CSV state. The
CLI prints a compact status/report/receipt pointer (full provenance stays local),
reuses its selected Settings, does not start the app, migrate stores, load model
weights, access Zotero, or contact a provider. Child TCP is denied and only an
explicit minimal environment is passed; no dotenv or live config parser runs there.

New caller provenance accepts **only** `seed` (default 42, env
`ZS_FEEDBACK_OPERATION_SEED`, unsigned 32-bit integer; booleans rejected). The
frozen functions' broader provenance contracts are unchanged. Child provenance
contains full HEAD, actual application Python/dependency-file SHA-256 inventory,
actual import root, Python/SQLite and relevant installed package versions. Selected
`Settings.config_path` bytes are hashed, or explicitly missing; no values are
serialized. Effective bounds/rules are resolved and hashed. Hashing goals bytes is
**not** resolving live model config; historical inference/training/config unknowns
stay unknown. Available event gate/training identities remain in retained evidence,
not substituted for historical inference. Comments/log/source bytes stay local.

Artifacts under `Settings.feedback_analysis_dir`:

```
attempts/<attempt_id>/ request.json, imports.json, child-status.json, receipt.json
                      failure.json on failure, run-staging/ when incomplete
runs/<run_id>/        retained/{inputs/,input-manifest.json} (original manifest)
                      result.json, result.md, output-manifest.json
```

Attempt/run IDs use `run_log.make_run_id`. Complete runs are 0500 directories/0400
files; attempts are 0700/0600. This is logical immutability, not authentication or
hostile same-user namespace protection. No existing run is reused/overwritten.
Publication uses source-proven **Darwin** `renamex_np(RENAME_EXCL)`; unsupported
platforms or missing libc symbols fail explicitly without overwrite fallback.
Linux/Windows portability is open. No fsync/power-loss/cross-filesystem guarantee.
The staging root stays writable until cross-parent rename (macOS requirement).

Nested budgets are `{operation: ..., freeze: ..., analysis: ...}`; frozen work caps
can only shrink. Operation names have `ZS_FEEDBACK_OPERATION_<UPPER_NAME>` env
forms; malformed env always raises even with explicit overrides. Named defaults /
hard caps: wall 120/600 seconds; tree RSS 512 MiB/1 GiB (provisional); swap growth
2 GiB/max 2 GiB; available RAM floor 20%/minimum 20%; poll 1 second/max 1; terminate
grace 1/max 5 seconds; cumulative I/O 2 GiB/max 2 GiB; source inventory entries (`max_source_files`, including directories/non-Python names)
4096/max 4096. Application/dependency-file inventory additionally caps 64 MiB;
selected goals hashing caps 1 MiB. psutil measures available/total,
tree RSS and swap **growth**, never old absolute swap residue. Child reports actual
`getrusage` peak RSS separately from parent sampled peak. Parent protocol/inventory
validation is size/work/safety bounded; blocking filesystem calls cannot promise
an absolute parent deadline. Child blocking calls are externally terminated/reaped.

Main-thread SIGINT/SIGTERM interruption kills/waits only the owned process group,
verifies absence and restores owned handlers without clobbering an observed foreign
replacement. Threaded callers do **not** get signal-handler ownership. SIGKILL,
parent loss, machine crash and foreign signal handlers can prevent normal cleanup
or receipt writing; no detached work is intentionally launched. Failures retain
staging/phase/safe class and propagate/nonzero CLI; frozen capture still performs
its own existing cleanup. Cleanup errors are not swallowed. Failure receipt
`published` is **bool or null**, describing owned publication, never mere destination
existence. Guarded staging `lstat` device/inode identity at only the destination
proves owned rename; only at staging proves not published, even with a foreign target.
Both/neither matches or inaccessible observation are explicit
`published=null, publication_state=unknown`; observation errors record a safe class
without masking the original failure. This is qualified private cooperating-namespace
observation, not hostile-user authentication or arbitrary inode-reuse immunity.
Known owned rename followed by chmod/final-receipt failure stays
`published=true, publication_state=published_but_not_confirmed`. No failure or
unknown permits cursor advancement/rollback. Invalid admission before attempt
creation refuses without output mutation.

The failure receipt is attached in memory **before** bounded `failure.json` I/O.
Failure-only `receipt_persistence` has `status=not_written|written|failed` and
`error_class=null|safe secondary class`, without raw exception messages. In-memory
`written` requires write-return; the candidate file payload describes that successful
write. An I/O `OSError` records `failed` and preserves the exact original exception
and ownership, so CLI still prints the computed failure receipt then propagates.
A non-I/O programming/interruption exception is classified but propagated loudly:
it and the original exception in its context share the attached failure receipt,
and the CLI exposes the safe secondary class. This is not success or silent recovery.
`receipt_path` is an attempted locator, not guaranteed usable. `failed` does not
prove absence: atomic replacement may precede a later I/O/cleanup error; a file may
exist despite the failed call. No fsync/power-loss guarantee is added.

`published` means a descriptive report with eligible observed support, **not**
scientific sufficiency or usefulness. With none, `insufficient_data` still publishes
concrete gaps and hashes. Markdown foregrounds coverage/unknowns, selection-biased
unique-paper class support/CIs, separate transitions and local disagreement locators.
Chronological eligibility and null ranking remain unscored. Weekly confirmation
and cursor behavior are documented below; daemon training provenance is described
in `services/model`. First private run, scored usefulness and overall #47 remain open.

## Weekly operation — later-stack reference, unavailable here

```
feeds serve -- selected Settings / explicit main-thread capability
  -> scheduler: UTC Monday cutoff / bounded oldest-first catchup
  -> shared operation: existing clean child -> verify_report
  -> schedule_state: FULL content key -> reuse confirmed report OR native publication
  -> durable scheduled receipt -> cancellation yield -> atomic cursor
server/background feed tick -> NO weekly activation
```

Automation is disabled by default. Settings resolves strict
`ZS_FEEDBACK_SCHEDULE_ENABLED=true|false|1|0`,
`ZS_FEEDBACK_SCHEDULE_MAX_WINDOWS` (default 2, range 1..4), and
`ZS_FEEDBACK_SCHEDULE_WALL_CAP_SECONDS` (default 240, positive/max 600).
State is under `Settings.feedback_schedule_dir` (`feedback-analysis/schedule`).
No new CLI flag, command family, timer, generic callback or subprocess runner.
`run_due_feedback(settings, *, now, budgets=None)` is async but invokes the existing
synchronous clean-child dispatcher on the MAIN thread only. The loop passes its
selected Settings explicitly; ticks, server startup and generic jobs never do.

The anchor is Monday 1970-01-05 00:00 UTC. First activation processes only the
latest closed window. Resumed catchup advances oldest first by arithmetic, at most
the configured windows per cycle. The aggregate safety cap reserves a whole fixed
operation cap before each child; remaining attempt-clock values never enter content
identity. Individual freeze/analysis/operation bounds remain unchanged. Blocking
parent filesystem calls retain the existing qualified safety-wall limitations.

The named sibling `run_scheduled_feedback_analysis(settings, *, cutoff, provenance,
schedule_intent, budgets=None)` performs a FRESH capture, even for a previously
analyzed cutoff. Its key binds full retained-input identity, code, config,
environment, seed and weekly window/rules. UUIDs and attempt/capture clocks are
excluded. Identical candidates reuse a bounded-verified immutable prior report
BEFORE another publication; changed input/config/code/environment/seed derive a
new key. The manual signature, receipts, worker protocol and result manifest stay
unchanged. Reused staging remains inspectable in its new private attempt.

A stable intent reference is written before launch and an immutable candidate/key
record before publication. Cursor/index failure recovery adopts that EXACT frozen
confirmed candidate, not a fresh analysis of current sources. Reused receipts say
`publication_action=reused`, `owned_published=false`, `confirmed_report=true`;
destination existence never proves a current owned commit. Missing/failed original
success receipts HOLD the attempt with no cursor and no automatic attempt pile.
Required retained inputs/output/provenance hashes and private read-only modes are
reverified. Logical immutability/cooperating namespace is not authentication.

`cursor.json` is the authoritative last committed cutoff/status/report pointer.
`status.json` contains failure or pre-cursor diagnostics; a `cursor_commit=pending`
diagnostic must be compared with the authoritative cursor, never treated as commit.
Daemon logs return actual completed-window status. Calendar checks are `no_due`,
not proof of unchanged input. `no_new_data` requires an actual captured source-byte
identity match with the prior weekly report; its own cutoff-specific report is still
published. `insufficient_data` retains the existing no-eligible-support meaning,
not an invented scientific sufficiency threshold.

Task cancellation is bounded-drain, not instantaneous: a yield BEFORE cursor
commit and BETWEEN windows delivers pending asyncio cancellation only after the
owned child is reaped. SIGINT/SIGTERM uses the existing nested main-thread signal
ownership/restoration checks. Parent loss/SIGKILL/power loss, foreign handlers,
post-rename I/O ambiguity and unsupported platforms retain the existing caveats.
No failed or unconfirmed report authorizes cursor advancement.

Every report retains all-time observations, so old-dated arrivals remain inspectable
at the next weekly run rather than being dropped by a timestamp watermark.
`late_observations` counts retained observations at/before the prior cutoff and
points to the full located result; it is NOT a count of newly arrived human actions.
Journal revisions are durable IDs. JSONL/CSV locators/content multiplicity remain
ambiguous, never manufactured event IDs. No models/labels change; private first-run,
scientific usefulness/scored evaluation and overall #47 closure remain unverified.

Source admission precedes namespace I/O. Default-OFF skips scheduler import; fcntl is activation-only. Acknowledged writers/cleanup precede owned finalizing→finalized native move, finalizer's last I/O. Missing/mismatched proof HOLDs. Cooperative namespace, inode-reuse/no-fsync limits remain.

Further subtraction: reuse clean-child, atomic publication and signal primitives;
no scheduling surface in ticks/server threads, status flag, dashboard or framework.
UX source remains issue47's public Laws-of-UX basis: Occam one daemon, Tesler owned
coordination, Cognitive Load one committed cursor/report pointer.
