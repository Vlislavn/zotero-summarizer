# feedback_analysis — retained inputs and descriptive reports

Contract for changing this package: capture retained inputs, replay them, analyze them, and publish an immutable
private report through a dedicated child process (manual `goldenset analyze-feedback`; opt-in weekly schedule via
`feeds serve`). Reports never change labels or install models. The version-1 analytic wire/result contract is
[INTERFACES.md](INTERFACES.md); product description: [docs/features.md](../../../../docs/features.md).

## Files

| file | responsibility |
|---|---|
| `__init__.py` | exports `freeze_inputs`, `verify_inputs`, `FreezeBudgets`, `BudgetExceeded`, `analyze_inputs`, `AnalysisBudgets`, `run_feedback_analysis`, `OperationBudgets` |
| `inputs.py` | path/boundary admission, work/safety guard, file capture (`freeze_inputs`), replay verification (`verify_inputs`) |
| `sqlite_snapshot.py` | WAL-aware read-only online backup; schema/revision metadata read from the retained database only |
| `provenance.py` | version-1 manifest wire validation and deterministic `content_digest` |
| `history.py` | bounded retained-source readers; ambiguity-safe current alias graph |
| `analysis.py` | `analyze_inputs(destination, *, budgets=None)`: retained-only result (no publication/training/live mutation) |
| `metrics.py` | separate unique-paper descriptive channels, Wilson intervals, eligibility metadata |
| `report.py` | deterministic presentation and bounded publication inventory; `__all__` = `hash_file`, `inventory`, `make_immutable`, `markdown`, `read_protocol`, `stage_report`, `verify_report`, `write_private`, `write_protocol` |
| `operation.py` | `run_feedback_analysis` / `run_scheduled_feedback_analysis`: private attempt, single-run flock, guarded child, validation, no-replace publication, receipts |
| `worker.py` | strict offline clean-process capture + analysis entry; never starts the app |
| `schedule_state.py` · `scheduler.py` | weekly intent, prepublication identity, confirmed-report recovery, cursor · `run_due_feedback` catch-up loop for `feeds serve` |

## Capture (`freeze_inputs`, `verify_inputs`)

`freeze_inputs(settings, destination, *, cutoff, provenance, budgets=None) -> dict` creates a new directory beneath
`settings.feedback_analysis_dir`. It refuses existing bundles, source overlap, parent traversal and symlinks.

| source key | retained path | capture boundary |
|---|---|---|
| `triage_db` (required) | `inputs/triage.sqlite` | read-only URI + `query_only` + SQLite online backup, including committed WAL pages |
| `golden_csv` | `inputs/golden.csv` | opened-file bytes (an atomic replacement can keep the complete older inode) |
| `interaction_log` | `inputs/interaction-events.jsonl` | prefix through EOF observed at open, including torn/malformed records |
| `classifier_runs_log` | `inputs/classifier-runs.jsonl` | same bounded-prefix contract; Settings path matches ledger producers |

- Every source records status, relative path/null, full SHA/null, captured size, locator, UTC capture interval, EOF
  offset/null and consistency caveat. Optional absent files are explicitly missing; read, schema, integrity, mutation
  and budget errors propagate. Sources and sibling bundles are never overwritten, chmodded or migrated.
- Only successful exclusive creation arms cleanup: initialization/capture failures remove that owned staging directory
  (cleanup errors propagate with the original as context); a preexisting target is never removed; no manifest is
  returned on failure.
- Database integrity, canonical schema SHA, `user_version`, page bounds, namespace versions and sync revision min/max
  come only from the retained SQLite; old absent migration/sync tables are explicitly unavailable. Admission needs
  core triage result, feedback and verdict reading columns, not a migration floor. Duplicate migration namespace rows
  are rejected; an uninitialized/empty store is refused (it cannot establish absence of decisions elsewhere). No live
  repository opener or writer backup is used, and `immutable=1` is never used on a live file.
- Cutoff must be aware UTC. The caller declares secret-free JSON provenance (`code`, `config`, `environment`, `seed`,
  any available inference identity/unknowns); nothing here inspects config, environment, Git or gate/model state or
  guesses inference identity. `content_digest` covers schema version, named status/hash/size, cutoff and the full
  declared provenance — not capture clocks or locators.
- `verify_inputs(destination) -> dict` validates the wire format, exact artifact inventory, full SHA-256/size, content
  identity and retained SQLite metadata without reading source locators; it refuses incomplete, changed, extra or
  symlinked artifacts. It is integrity checking, not authentication against someone who replaces bytes and recomputes
  the manifest.
- Not a cross-file atomic transaction: capture can follow the logical cutoff, so analysis reconciles by cutoff and
  revisions and keeps unrecoverable gaps; CSV row timing is unknown. Logs are read twice over the same opened prefix
  (incompatible mutation fails). No lock covers adversarial write-and-restore or same-user namespace races.

## Budgets and permissions

- `FreezeBudgets` (or a known-field dict) accepts only smaller bounds: `max_source_bytes` 256 MiB per source (SQLite
  main+WAL), `max_snapshot_pages` 65,536, `backup_pages_per_step` 128, `max_progress_callbacks` 4,096 (also bounds
  busy/restart backup work). Reads stream in 64 KiB chunks; manifests cap at 1 MiB. Caps are provisional safety
  limits.
- One wall cap: default 60 s, max 600 s, env `ZS_FEEDBACK_FREEZE_WALL_CAP_SECONDS` (`budgets.wall_cap_seconds` takes
  precedence; a malformed env value raises even when overridden); verification uses the same env cap. SQLite progress,
  backup callbacks and file-chunk boundaries check it; blocking filesystem calls cannot be interrupted in-process.
- New directories/files request POSIX 0700/0600; source permissions never change. Manifest replacement reuses
  `services._common.atomic_write` (no fsync, no power-loss durability).

## Analysis (`analyze_inputs`)

- Consumes only verified retained inputs; cutoff and provenance come from the manifest; no live source locator is
  opened and replay needs no original stores. `AnalysisBudgets` caps records, source/line/decoded/output bytes, CSV
  field characters (`max_csv_field_chars`, default/hard cap 64 Mi codepoints; lower cap raises `BudgetExceeded`) and a
  safety wall (`ZS_FEEDBACK_ANALYSIS_WALL_CAP_SECONDS`). Physical lines/positions are consumed lazily so the record
  cap stops short-row inputs. Marker-only ambiguity candidates and SQL proof stay inspectable without unioning
  alternatives or trusting a stale alias; invalid marker JSON/type fails at admission. SQL observation/alias keys must
  be nonblank text; a present journal needs `source`; UTC-conversion overflow is located invalid/unknown time.
- The CSV parser ceiling is process-global, so analysis runs only in the dedicated clean child (never call
  `analyze_inputs` on shared API/daemon CSV state; use `run_feedback_analysis`). Owned calls serialize with
  deadline-aware locking and restore the saved ambient limit on every exit; an observed outside change refuses and
  preserves the foreign setting. Other readers in that process see the temporary ceiling; races between final check
  and restore are unsupported.
- Evidence semantics: journal revisions establish label-category/retraction evidence, not lost prediction context.
  JSONL has no durable event IDs (equal content/time is unconfirmed consistency; multiplicity/locators kept). Journal,
  log and current-feedback channels are never summed as total decisions. Saved predictions are never replaced by
  retriage; comments stay local evidence. Naive/backfilled timestamps are excluded; current aliases/categories
  describe snapshots, not historical features. Chronological partitions are eligibility only; historical topic changes
  and P@10/NDCG@10 stay unscorable without recorded exposure; intervals describe selection-biased unique support.
- `label_transition` recognizes `offline_sync` and `sync_conflict_resolution` beside `user`/`zotero`, for transitions
  only: either sync source on `human_feedback` stays `nonpreference`; outcomes stay outcomes; `machine_add` feedback
  stays provisional. Raw provenance is retained, not authenticated.
- Mixed ordinal/binary log evidence: conflicting latest ordinal observations remain a conflict (never a binary
  substitute); binary class support is reported separately; a later unambiguous ordinal observation resolves it.
- Report Markdown wraps recorded surfaces in code spans longer than any recorded backtick run (literal evidence text).

## Manual operation

Flow (`goldenset analyze-feedback --project-root ROOT [--cutoff AWARE_ISO]`): operation (private attempt, single-run
flock, guarded child) → worker (byte-hash code/config, freeze `retained/`, analyze, stage JSON/Markdown) → operation
(bounded inventory/hash validation, atomic no-replace rename) → one receipt (`published` / `insufficient_data` /
failure); no cursor.

- `run_feedback_analysis(settings, *, cutoff, provenance, budgets=None) -> dict` is the only entry for parent
  API/daemon threads. The CLI prints a compact status/report/receipt pointer, reuses its Settings, and never starts
  the app, migrates stores, loads model weights, touches Zotero or contacts a provider. Child TCP is denied, only an
  explicit minimal environment is passed, no dotenv/live config parser runs, and source-tree bytecode writes are off.
- The source-checkout command needs Git HEAD, `pyproject.toml` and `uv.lock` (a wheel without them fails before
  capture). Caller provenance accepts only `seed` (default 42, `ZS_FEEDBACK_OPERATION_SEED`, unsigned 32-bit int,
  bools rejected). Child provenance holds full HEAD, application Python/dependency SHA-256 inventory, import root,
  Python/SQLite/package versions, the `Settings.config_path` bytes hash (or explicit missing; no values) and
  resolved/hashed bounds. Historical inference/training unknowns stay unknown; comments, logs and sources stay local.
- Layout under `Settings.feedback_analysis_dir`:

```
attempts/<attempt_id>/ request.json, imports.json, child-status.json, receipt.json
                       failure.json on failure, run-staging/ when incomplete
runs/<run_id>/         retained/{inputs/,input-manifest.json}, result.json, result.md, output-manifest.json
```

- IDs come from `run_log.make_run_id`. Complete runs are 0500 directories/0400 files, attempts 0700/0600: logical
  immutability, not authentication or hostile same-user protection. An existing run is never reused or overwritten.
  Publication uses Darwin `renamex_np(RENAME_EXCL)`; unsupported platforms or missing libc symbols fail without an
  overwrite fallback. No fsync/power-loss/cross-filesystem guarantee; the staging root stays writable until the
  cross-parent rename.
- Nested budgets `{operation, freeze, analysis}`; frozen work caps only shrink. `ZS_FEEDBACK_OPERATION_<UPPER_NAME>`
  overrides (malformed values always raise). Defaults/hard caps: wall 120/600 s; tree RSS 1 GiB/1 GiB (default raised from 512 MiB: a real ~200 MB DB peaks ~650 MB);
  swap growth 2 GiB; available-RAM floor 20%; poll 1 s; terminate grace 1/5 s; cumulative I/O 2 GiB (counted per guard
  over provenance/protocol/report artifacts); source inventory 4096 entries; application/dependency inventory 64 MiB;
  goals hashing 1 MiB. psutil measures tree RSS and swap growth (never old residue); the child's `getrusage` peak RSS
  (positive integer within the cap) is reported apart from the parent's sampled peak. Child blocking calls are
  externally terminated and reaped; parent filesystem calls cannot promise an absolute deadline.
- Signals: main-thread SIGINT/SIGTERM are deferred during child launch until cleanup is armed, then kill/wait only the
  owned process group, verify absence and restore owned handlers without clobbering a foreign replacement; the child
  unblocks the inherited launch mask. Threaded callers get no signal ownership. SIGKILL, parent loss, crash and
  foreign handlers can prevent cleanup or receipts. Failures keep staging/phase/safe class and propagate (nonzero
  CLI); cleanup errors are never swallowed; invalid admission before attempt creation refuses without output mutation.
- Failure receipt `published` is bool or null and describes owned publication, not destination existence: staging
  `lstat` device/inode at only the destination proves an owned rename, only at staging proves not published; both/
  neither or inaccessible observation gives `published=null, publication_state=unknown`. Owned rename followed by
  chmod/final-receipt failure is `published=true, publication_state=published_but_not_confirmed`. Unknown or failure
  never permits cursor advancement or rollback.
- The failure receipt is attached in memory before bounded `failure.json` I/O; `receipt_persistence` is
  `not_written|written|failed` with a safe `error_class`. An I/O `OSError` records `failed`, keeps the original
  exception, and the CLI still prints the receipt then propagates; a non-I/O exception is classified but propagated
  loudly. `receipt_path` is an attempted locator; `failed` does not prove the file is absent.
- `published` means a descriptive report with eligible observed support, not scientific sufficiency; with none,
  `insufficient_data` still publishes concrete gaps and hashes. Chronology and ranking stay unscored.

## Weekly operation

```
feeds serve -- selected Settings / explicit main-thread capability
  -> scheduler: UTC Monday cutoff / bounded oldest-first catch-up
  -> shared operation: clean child -> verify_report
  -> schedule_state: FULL content key -> reuse confirmed report OR native publication
  -> durable scheduled receipt -> cancellation yield -> atomic cursor
server/background feed tick -> NO weekly activation
```

- Enabled by default (#218); the owner switch is `weekly_switch.py` (`weekly.json`, read by the daemon each tick,
  set from the app). Settings resolves strict `ZS_FEEDBACK_SCHEDULE_ENABLED=true|false|1|0`,
  `ZS_FEEDBACK_SCHEDULE_MAX_WINDOWS` (default 2, 1..4) and `ZS_FEEDBACK_SCHEDULE_WALL_CAP_SECONDS` (default 240, max
  600); state lives under `Settings.feedback_schedule_dir`. `ZS_FEEDBACK_SCHEDULE_ENABLED=false` skips the scheduler import; `fcntl` is
  activation-only. The aggregate wall cap must exceed the resolved fixed operation cap (incompatible settings fail,
  never pend forever) and reserves a whole operation cap before each child.
- `run_due_feedback(settings, *, now, budgets=None)` is async but calls the synchronous clean-child dispatcher on the
  MAIN thread only; ticks, server startup and generic jobs never do. Anchor Monday 1970-01-05 00:00 UTC; first
  activation processes only the latest closed window; resumed catch-up advances oldest first by arithmetic, at most
  the configured windows per cycle. The schedule adds no CLI flag, timer, callback or subprocess runner.
- `run_scheduled_feedback_analysis(settings, *, cutoff, provenance, schedule_intent, budgets=None)` always captures
  fresh. Its key binds full retained-input identity, code, config, environment, seed and weekly window/rules (UUIDs
  and clocks excluded); an identical candidate reuses a bounded-verified immutable prior report before publishing
  again, and any change derives a new key. Reused receipts say `publication_action=reused`, `owned_published=false`,
  `confirmed_report=true`; destination existence never proves a current owned commit. Required hashes and private
  read-only modes are reverified.
- A stable intent reference is written before launch and an immutable candidate/key record before publication; cursor/
  index recovery adopts that exact frozen confirmed candidate, never a fresh analysis. Source admission precedes
  namespace I/O; acknowledged writers/cleanup precede the owned finalizing→finalized native move (the finalizer's last
  I/O). Missing/mismatched proof HOLDs. With no cursor, bounded lookup recovers the oldest retained active intent
  across a weekly rollover. A missing/failed success receipt HOLDs the candidate; before any candidate exists, an
  identity-bound failure proving no publication and no destination allows one fresh bounded attempt (child reaped with
  group absent, or phase `admission` with an empty child record). Old evidence stays intact; no attempt pile.
- `cursor.json` is the authoritative last committed cutoff/status/report pointer (`cursor_commit=pending` in
  `status.json` is a diagnostic, never a commit). `no_due` is a calendar fact; `no_new_data` needs an actual captured
  source-byte match with the prior weekly report and still publishes its own report. Cursor links are refused before
  launch; no failed or unconfirmed report advances the cursor.
- Cancellation is bounded drain: a yield before cursor commit and between windows delivers asyncio cancellation only
  after the child is reaped. Only explicitly tagged owned SIGINT/SIGTERM interruptions stop the daemon cleanly (no
  feed tick, no cursor advance); other faults propagate.
- Every report retains all-time observations; `late_observations` counts retained observations at/before the prior
  cutoff (not newly arrived actions). Journal revisions are durable IDs; JSONL/CSV locators stay ambiguous, never
  manufactured event IDs.

Extension rule: reuse the clean-child, atomic-publication and signal primitives; add no scheduling surface in ticks/server
threads, status flag, dashboard or framework.

## App surface (#218)

`api/routes/feedback_analysis.py` (`/api/feedback-analysis`: `GET status|runs|latest`, `POST run`, `PUT weekly`)
over three small modules: `manual_run.py` (one background "Run now" thread; the service lock still serializes
against the daemon, 409 when running), `summary.py` (distils the 64 MB `result.json` into a plain-English
`summaries/<run_id>.json` cache beside, never inside, the immutable run) and `weekly_switch.py`. UI: Ops → System →
"Your decisions" (`frontend/src/components/FeedbackAnalysisCard.jsx`), plus a read-only row in Settings. The analysis
never changes labels or models. First real run on a ~200 MB DB needed two fixes: `processed_feed_items` bulk text
(`abstract`, SHAP, review JSON) is no longer loaded (it blew the 64 MB decoded cap) and the default tree-RSS cap is
1 GiB (peak ~650 MB). A run is refused when free RAM is under 20 %.

## Stored-proposal agreement (#224)

`label_verdicts.proposal_json` (written at decision time) gives owner verdicts a durable prediction. `analysis._snapshot`
reads its `priority` into the event `prediction`; `metrics.periods` adds a `verdict_snapshots` channel (agreement, ordinal
error, disagreements) over user verdicts that carry one. Older verdicts have none and stay out of it. `summary.py` (UI) does
not read the channel yet.
