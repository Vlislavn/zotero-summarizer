# services/research_feed — weekly Research Intelligence

The RSS command projects existing app RSS and current deep-review artifacts into
a project-specific engineering digest. An explicit saved-input Telegram mode
composes the existing reranker and default-provider client for news, without a
paper classifier, PDF extraction or scientific review fields. Explicit live CLI
collection now runs in a dedicated owned child before that same saved-news path.

```text
rss_items → source/dedupe → prior triage → budget → existing deep_review
          → engineering card → data/research_feed/weekly-<run-id>/{*.json,*.md}
                                      └─ optional reviewed Zotero tag queue
```

| file | responsibility |
|---|---|
| `profile.py` | Versioned user-editable profile and controlled topic taxonomy under `data/research_feed/profile.json`. |
| `source.py` | Bounded date-range RSS/dedupe, whole-row identity/text admission and shared strict consumed JSON/numeric parsing. |
| `ranking.py` | One pure whole-current-cohort call to the existing blend before budgets; scored, unscored and invalid inputs stay separate. |
| `cursor.py` | Bounded successful-stage identity/checksum replay through the existing state pointer and immutable reports. |
| `reports.py` | Selected-root report/path/schema admission, listing and explicit cached-only web projection. |
| `runner.py` | Budgeted triage, optional missing deep reviews through `library.deep_review`, successful-stage reuse, per-paper failure isolation, metrics/watermark, and dry-run/idempotent writeback. Tag idempotency looks up pending changes by target Zotero item, so unrelated older queue rows cannot push a matching writeback beyond a global scan cap. |
| `card.py` | Source-qualified projection of same-call typed project assessments; profile/source binding and AI-unverified state remain explicit. Missing assessment is not no-fit. |
| `profile.py` | Versioned user-editable profile and controlled topic taxonomy under `data/research_feed/profile.json`; `persist_default=False` reads missing defaults without creating a file (existing default remains True). |
| `source.py` | Bounded date-range adapter over app-owned RSS plus DOI/source/title dedupe. |
| `runner.py` | Budgeted triage, optional missing deep reviews through `library.deep_review`, per-paper failure isolation, metrics/watermark, and dry-run/idempotent writeback. Tag idempotency looks up pending changes by target Zotero item, so unrelated older queue rows cannot push a matching writeback beyond a global scan cap. |
| `card.py` | Pure engineering-card projection; only exact validated artifact URLs survive. |
| `render.py` | Shared staged JSON/Markdown persistence; optional retained inputs and invocation-time renderer selection. |
| `telegram_source.py` | Strict bounded version-1 JSONL posts, source line diagnostics; no live transport or embedded assessments. |
| `telegram.py` | Selected-store imports, content/identity-bound reuse, shadow ML before default-provider LLM, explicit stage states and bounded new attempts. |
| `telegram_render.py` | News-only cards, separate uncertainty and pending/error/skip diagnostics; no paper quality projection. |
| `telegram_collect.py` | Explicit internal async injected-client collector over selected-store page state; no lifecycle, SDK/auth, CLI, models or publication. |
| `telegram_runtime.py` | Dedicated auth/collect child composition: read-only profile, existing retry/error helpers and collect_pages once, actual cursor receipts, lazy M2a/auth leaves and integration-owned lifetime. No models/default activation. |

Run weekly: `uv run zotero-summarizer research-feed run --from 2026-08-22
--to 2026-08-29`. Add `--venue NeurIPS` for conference mode, `--cached-only`
to avoid new model work, or `--queue-zotero` to opt into reviewable tags. A
cron/launchd entry can invoke the same command weekly. Edit the generated
`profile.json` to add a theme/project. Each report name includes a deterministic
fingerprint of its date window, venue, profile, and output-affecting run options,
and a run suffix so repeated executions retain immutable results. JSON and Markdown
are staged together and published by one same-filesystem directory rename; the root
`state.json` pointer is atomically updated only after publication. A state-write
failure leaves the prior pointer and complete bundle intact. Independent runs ending
on the same date keep separate artifacts. Reversed date windows fail before
profile/source reads or writes. The sole RSS adapter is `load_candidates`;
the unused source protocol and state-only class wrapper are removed.

Offline acceptance is `uv run python tools/eval_research_feed.py --check`.
The shipped 30-paper fixture records human inclusion and verified artifact labels
but lacks frozen pre-human abstracts/scores/summaries and review outputs. The
checker exits nonzero and reports unavailable (`null`) production metrics,
not fallback-path zero precision or by-construction code-link precision. The
separate 17-paper fixture supplies a clearly labelled reading-policy diagnostic.
See `docs/issue-evidence-research-feed.md` for the required source snapshot;
ARE's deterministic hard-check principle is used, but event-trajectory judging
is deferred because this is a fixed paper projection, not an agent trace.

Engineering cards preserve a withheld reading action as `worth_reading="unknown"`
and include current/stored reading-policy flags in `evidence_gaps`; absence is not
converted to skip. The existing JSON/Markdown projection and optional tag writer
carry this state rather than treating a failed goal assessment as a negative result.
Without a qualified typed producer, reproduction effort stays `unknown`: code
availability and scientific checklist coverage establish neither duration nor
complete inputs. A qualified same-call reproduction estimate retains its exact
original quote and AI-unverified label. Verified artifact URLs retain their own
fields; scientific criteria join `evidence_gaps`, not `missing_reproduction_inputs`.
No separate assessment call is made.

Newly generated weekly paper Markdown exposes the card's existing reproducibility tier and
rationale, missing reproduction inputs, and scientific evidence gaps separately:

```markdown
**Reproducibility tier:** unknown

**Reproducibility rationale:** Reproduction effort and required inputs have not been assessed.

**Missing reproduction inputs:** not recorded (not assessed)

**Evidence gaps:** not recorded (not assessed)
```

An empty or absent field is unrecorded, not evidence of completeness, readiness or
absence of gaps. Nonempty values retain their full text, Unicode, item order and
duplicates. Scientific gaps remain separate from reproduction inputs; artifact
links do not authorize a reproduction-duration estimate. This adds visibility,
not another assessment or a scientific/human-usefulness claim. Legacy cards
without these optional fields still render.

The existing verbatim Markdown boundary used for paper titles and prose is
retained; these reports are untrusted Markdown, not newly sanitized HTML. JSON
remains canonical, publication and retained inputs use the same shared persistence
path, and saved Telegram news continues through its separate custom renderer.

CLI and direct `run_weekly` calls share strict work-budget admission before
profile/source I/O: shortlist 1–100, cards 1–20, source rows 1–5,000, review wait
1–86,400 seconds. Omitted shortlist/card budgets use the saved profile; zero,
booleans and fractional counts are invalid. These are one-shot operational caps,
not measured model capacities. A short wait is used exactly, not raised to 30s;
it does not cancel an already running review job (outcome handling remains A170).
Future profile schema versions are rejected without modifying the stored file.
Cached-only CLI projection skips app startup entirely; it needs existing RSS and
optional review artifacts, not a goals file, classifier or provider initialization.
Generation uses the existing no-background startup mode: no prewarm, server-job
recovery, classifier retraining or slate rescoring. Enabled model prerequisites
must already be prepared for generation; they do not apply to cached-only reads.

Source admission is newest-first (row ID breaks equal-time ties), filters venue
case-insensitively, then streams the existing DOI/source/title deduplication before
the unique-result cap. Earlier unrelated or duplicate rows cannot consume the
budget. `discovered`/`deduplicated` count admitted unique matching papers, not all
raw rows in the date window. Cursor consumption stops at the cap; sorting/scanning
the underlying window can still require database work. Malformed nonempty source
timestamps raise instead of silently becoming absent dates.

## Source-qualified RSS projection

```text
current review + bound source identity + explicit full_text digest -> card
missing/abstract/unbound review -> unqualified_reviews + reason (not missing PDF)
actual needs_pdf + acquisition unavailable outcome -> manual_full_text_required
bound source available + card failure -> availability count, separate failed card
```

The canonical current-review reader owns freshness. Projection checks its existing
source kind/path/SHA-256 and generation identity/contract, without opening a PDF,
acquiring it or treating a truthy legacy identity as qualification. Availability
observations live in `metadata.full_text_evidence`, scoped to card-budget candidates;
zero confirmed sources is not proof all candidates lack full text. Bound availability
is counted before card construction. Missing/stale reviews, abstract basis and unknown
acquisition remain inspectable in `unqualified_reviews` and Markdown, with one candidate
body or exact source-history pointer. Actual acquisition outcomes remain recorded.
Cursor code/input hashes and the source-qualified projection marker prevent replay of
old unqualified cards; old bundles remain immutable and are not retroactively repaired.

### Same-call project assessment

The existing explicit generation run supplies a snapshot of the configured profile
and exact candidate `source_id` to the existing deep-review digest call. The
code-selected `FeedDigestResponse` adds typed project records plus optional impact
and reproduction assessments; it returns a plain `PaperDigest`. There is no second
summarizer, judge, route, retry budget or cache loader. Cached-only builds never
request generation. A qualified base without a matching feed assessment remains
readable, but an explicit generation run requests its missing/stale assessment in
the same digest call rather than treating the base as completed project fit.

Each applicable record names the exact declared project/source, closed use kind,
mechanism, target component, actual proposed change, minimal validation experiment
and verbatim original quote. The service retains deterministic first-occurrence
extraction offsets, original-text hash and existing source binding; these are not
PDF page coordinates or semantic support. Generated map notes cannot authorize a
quote. Every record remains `ai_generated` / `unverified`, including assessed no-fit.
Missing/malformed input yields `not_assessed`/`invalid`; omitted targets or explicit
unknowns yield `partial`. A valid partial response can be reused without claiming
completion. `project_uses=[]` alone is not an assessment state; inspect
`project_assessment_status` and `project_assessment_reason`. Exact GoalSummary hits
remain unassessed context and cannot supply a use or experiment.

Typed `project_assessments`, evidence spans and extractable `ResearchIdea` records
preserve the actual change/experiment rather than generic comparisons. Original
profile/context, selected producer/schema, prompt/input/decoder hashes, configured
model/provider provenance and timestamp accompany the augmented generation. Its
binding includes the exact normalized base digest, verified digest, source/extraction
and base identity/audit. Profile, source, code/schema or digest drift withholds all
optional positives without removing the base reader. Cursor stage identity reuses
the producer identity, preventing old positive cards from bypassing qualification.

Impact/reproduction remain explicit AI estimates supported only by retained literal
source membership, not scientifically validated warrants. With no qualified typed
impact, compatibility integers are zero and `impact_assessment=null` (unknown, not
measured zero); implementation prose, checklist gaps and repository URLs cannot
create positive scores or effort. Artifacts and the explicit `full_text` admission
guard are unchanged. Map-reduce producer plumbing preserves original authority but
does not relabel its `map_reduce` digest as `full_text` to pass this guard.

The existing per-item single-flight can already be occupied by an ordinary review;
if it finishes without feed output, the explicit run reports an incomplete
assessment and leaves a later explicit retry to the caller. No automatic second
call or expanded timeout is added. Source-only machine controls do not establish
human project usefulness, benefit metrics or whole-issue completion.

## Offline saved Telegram news

```text
saved JSONL -> TelegramPost -> selected triage SQLite v11
           -> every theme/project interest (existing reranker, max score)
           -> configured DEFAULT provider/model (one structured verdict per admitted post)
           -> shared staged {JSON, Markdown, inputs.jsonl} -> separate Telegram state pointer
```

Use `research-feed run --source telegram --input-jsonl POSTS --project-root ROOT`.
This explicit processing mode may load the configured reranker and send originals
and declared interests to the configured default LLM: obtain permitted-content and
provider consent first. It is not live Telegram ingestion. `--collect-only` imports
and exports originals without either external gate or a goals file. `--cached-only`
needs no new input; it checks retained results against the saved profile/current
configured identity without models/providers, migrations or store writes. It needs
that profile and config to distinguish stale results. `research-feed view --report
SAVED.md` prints only the retained Markdown, without reconstruction or publication.
Neither these modes nor processing calls app startup, Zotero or paper-state writers.

Canonical JSONL has one post per line: explicit `schema_version: 1`, nonzero signed
SQLite-integer `channel_id`, positive `message_id`, `username` (optional), absolute
HTTP(S) `permalink`, exact Unicode `text` (including captions), timezone-aware original
`published_at`/optional `updated_at`, `urls`, and optional explicit `skip_reason`.
Empty text requires a skip reason. Unknown fields (including assessments/session
credentials), duplicate JSON fields/identities, nonfinite values, invalid dates and
unsupported versions fail with line diagnostics before import. The entire admitted
file validates before its caller-owned transaction; no inferred entities or URL fetch.
The existing version-1 profile gains `telegram_channels=[]`; saved input does not
certify allowed live channels or advance a confirmed cursor.

Source cap defaults to 1,000 using the existing 1–5,000 admission type. Reports select
the newest bounded saved window and process it oldest-first; older rows remain stored,
not certified as complete. RSS dates/review timeout may be supplied for CLI compatibility
but do not filter or time news inference. The selected Settings request budget bounds
reranker waiting; LLM deadlines remain those of the existing provider factory.
No silently truncated input text or permanently advanced live watermark is created.

ML is **shadow only**, including low scores: all declared nonempty themes/projects
finish before any LLM call. Persist maximum and per-interest scores; threshold is null,
hard gate false, revision/truncation coverage unavailable (the unchanged wrapper uses
512). A fresh explicit process waits once through the reranker's public readiness
method on its existing Future, using the selected Settings `SUMMARY_TIMEOUT_SECONDS`
(the same configured request budget already present in evaluation identity).
There is no new knob, inflated minimum, poll loop, private news-side `_load` or
second executor. Instant and delayed successful initialization continue to scoring
in this same command. Expired/still-unready waiting remains `pending/ml_loading`
with `load_wait_budget_seconds`; actual load failures are ML errors, including a
loader's own TimeoutError. Neither is a negative label, provider fallback or silent
retry. Missing interests remain pending without starting loading.
This wait bounds waiting, not downloading, inference, background threads or total
CLI exit: Python may join unfinished executor work. Hard termination remains with
the separately admitted outer resource/process guard. No standalone whole-command
deadline is claimed. `request_timeout_seconds` in evaluation identity is the selected
Settings input used for reranker waiting and replay binding, not an effective-provider
timeout receipt. The CLI's OpenAI factory path forwards that setting; its native
Anthropic branch retains the adapter's existing 600-second default. This slice
changes neither provider deadline policy nor retries and does not measure effective
request duration. The existing default route is used, not feed/legacy overrides
or a new routing stage.
LLM keep/drop/uncertain results require a literal original quote and declared-interest
membership; this checks attribution, not semantic entailment or measured usefulness.

Shortlist budget caps new admitted LLM work, including failures; successful unchanged
keep/drop/uncertain evaluations spend no new budget. Pending/error work can be retried
only by another explicit processing run. Card budget caps confirmed cards, not uncertainty.
Provider/schema failures retain error class/stage; actual token/usage receipts are null,
not estimated savings. Content/profile/prompt-schema/configured model/default route and
request identity bind reuse; unknown immutable weight revision remains a limitation.
Changed originals invalidate only that composite row; conditional evaluation saving
refuses an intervening edit. Cached mismatches are pending without old summaries.

Every publication has a fresh run directory under `data/research_feed/telegram/`.
JSON, Markdown and canonical inputs share the existing staged rename; pointer updates
happen afterwards and never certify ingestion. Previous bundles remain untouched.
Publication failure stays loud, without a new outbox or crash/exactly-once claim.
No RSS/triage/label/pending/feedback row, golden file, corpus or interaction log is written.
Live/auth composition is described below; real SDK/account behavior and human-labelled
science, consent/manual smoke and hard-gate activation remain unverified.

**Boundary:** RSS may queue explicitly requested reviewed Zotero tags; Telegram never
queues or writes Zotero and uses only its two same-DB tables and local report artifacts.

## RSS source, native admission and ranking

`_assess` admits only the latest processed row with exact source key, explicit
`app_rss` type, nonempty exact title, compatible DOI and nonconflicting nonempty
abstracts. There is no older-row/alias/fuzzy rescue. An accepted stored original
fills only blank RSS text, byte-for-byte; generated summaries are never originals.
`metadata.source_history` covers every admitted source, including failures and
budget cuts, with one candidate body or exact published JSON pointer. Identity
compatibility leaves `currentness` and `content_version` explicitly `not_attested`.

One `_summary` parser distinguishes missing/null objects from malformed present
JSON. One `_number` parser excludes booleans/nonfinite/out-of-domain values while
preserving real zero and finite numeric strings. Composite and confidence use
`[0,5]`/`[0,1]`; only missing/null uses existing operational defaults, not observations.
Public `triage_candidate(candidate,row,profile)` keeps three positional arguments.
After consumed data validates, native selected includes; user/gate/low-score vetoes
and duplicate states exclude. Skipped-error fails; pending/awaiting-review/cutoff/
elbow/user-approved/black-swan states use ordinary triage, not an approval bypass.
Invalid native decisions fail per paper without exposing raw payloads. Admission
status/reason/native decision remains separate from source-history compatibility.

`rank_candidates(assessed,accepted_rows,source_order_ids)` runs once over the whole
current eligible cohort before shortlist/card limits. Non-null native composite
(including zero) wins, then observed summary fallback. Both absent means unscored,
not an observed operational one. Only eligible observed rows consume signed goal
similarities `[-1,1]` and citation percentile `[0,1]`; malformed consumed inputs fail
without affecting healthy cohort normalization. The unchanged `blend_scores` owns
weights, normalization, missing signals and citation median. Scored ties preserve
source order; the unscored tail retains descending ordinal/confidence/title order.
No per-budget normalization, new score/default/model or review-card backfill.

`metadata.ranking` declares `weekly_rank_v1`, cohort scope and source-keyed input
order plus `not_applicable`, `unscored`, `scored` or `failed`. It records actual
observed components, blend key and pre-budget order without duplicating originals.
B1 eligibility and a later ranking failure may coexist; neither certifies usefulness.
Markdown retains reproduction rationale, missing reproduction inputs and scientific
evidence gaps separately, without inferring reproduction effort from a code URL.

## Weekly consumption

React `/weekly` lists retained bundles, opens `/weekly?run=...`, and downloads JSON
or Markdown through `/api/research-feed`. Reports render as text, not untrusted HTML.
Paths stay within selected Settings data; traversal/symlinks are refused. Unsupported
or corrupt reports remain unavailable/422. Project idea groups must be lists before
individual validation; empty lists are valid, malformed groups are not rewritten.
`POST /run` reuses the existing runner with `generate_reviews=False`, `dry_run=True`,
`queue_zotero=False`, inclusive UTC dates and 1–10 cards. No new model/download/write
pipeline is introduced. Empty cards, missing current reviews, per-paper failures and
unknown artifacts remain distinct. A missing review does not prove a missing PDF.
The page is server-only, not an offline report cache. A lost response can leave a
published bundle; refresh saved runs before another explicit build.

## Persisted RSS candidate cursor

The existing `state.json` gains a versioned `candidate_cursor`, atomically committed
with its report pointer after complete JSON/Markdown publication. At most 5,000
recently used source entries retain the latest successful stage, matching the
existing source-row ceiling. Eviction causes ordinary processing, not omission.
Triage identity binds exact effective candidate, accepted raw history, profile,
stage sources and DTO schemas. Fresh source/native admission precedes reuse; cached
membership/order never replaces current whole-cohort ranking or budgets.

Cards reference immutable published records with source/candidate/triage/checksum
and freshly qualified canonical current-review identity. Missing/stale reviews
cannot reuse old cards. Only canonical callbacks can reuse; explicit injected
triage/card/review-loader callbacks retain their invocation semantics. Missing or
failed stages stay retryable. Absent legacy state is cold; malformed present cursor
fails loudly, stale inputs reprocess, and unavailable/mismatched reports reprocess
with bounded metadata reasons. Prior bundles are not repaired or overwritten.

Reuse counts are observations, not measured time/token savings or scientific
freshness. Last-writer-wins state is not a process lock or exactly-once guarantee;
concurrent runs and evictions can repeat work. Optional approved tag idempotency is
unchanged. Authentic cohorts, project usefulness and human-time evaluation remain
separate; unrelated live-Telegram schema/SDK/collector adoption is not part of RSS.
queues or writes Zotero. Its ingestion state stays in the selected triage SQLite
store; reports use the existing Settings data root.

## Explicit live CLI composition (additive)

`research-feed telegram-auth` and `research-feed run --source telegram` compose the
existing M3 process owner, not a new news runner. The optional closed `purpose`
argument in `telegram_runtime.collect_once_cli` defaults to unchanged collection;
auth needs no profile/channels, and uses the existing durable admission/error
callbacks around the small integration auth callback. Auth FloodWait persists at
failure time before cleanup. Normal collection reads only declared public
broadcasts, one existing M1b page each, preserving all page/cursor/hash semantics.
The service appends actual durable per-channel cursor state to its collection
receipt; mismatch/error statuses remain explicit and stop public model processing.

The dedicated child publishes via `run_saved(collect_only=True, live_collection=…)`
without models/config/Zotero. Only clean child exit plus verified same-bundle
JSON/Markdown/input hashes permits parent `run_saved` shadow ML → default LLM.
Actual collection counts, channel cursors and retry_at are retained, not replaced
by import counts/null placeholders. `metadata.collection` keeps the complete
bounded lifecycle/channel/account receipt, including committed-page skip counts;
the ordinary top-level skipped count remains the selected saved-window projection.
The exported originals are compared to current stored originals before model work;
a concurrent edit fails rather than replaying old originals over new storage.
Existing conditional evaluation writes still reject per-row intervening edits.

Saved JSONL, cached-only and view do not launch Telegram. Default RSS imports and
profile behavior remain unchanged. The process-held M3 fuse/lease cannot be released
to run ML in that child; the parent never disarms it. See the
[CLI contract](../../cli/README.md#explicit-telegram-auth-and-live-collection) for
TTY, own app identity/keyring, private paths and bounded lifecycle. No auth/live
account work has been performed by this source change. Three permitted channels,
manual/restart checks, the 400-post RU/EN calibrated/holdout study, scientific
usefulness and hard-gate activation remain separate unverified acceptance.

queues or writes Zotero. Its ingestion state stays in the selected triage SQLite
store; reports use the existing Settings data root.

## RSS accepted-history boundary

Before the existing pure triage function, `_assess` admits only the already-selected
latest processed row with exact source key, explicit `source_type="app_rss"`, and
an exact nonempty title. Both present DOIs must agree through the existing
`domain.normalize_doi`; neither original is rewritten. Missing/legacy source type
is not inferred from the key. Identity failures and byte-different nonempty RSS
and stored abstracts withhold that whole paper through per-paper assessment
failure. There is no older-row, alias, title-fuzzy or DOI-alias rescue.

Accepted stored abstract text replaces only an empty/whitespace RSS abstract,
byte-for-byte, before triage. Otherwise RSS text stays exact, including whitespace
when both texts are unavailable. Generated summaries are not original text.
Only the accepted row map can supply existing scores, summary evidence or the
optional materialized Zotero tag target. RSS and processed source rows remain
read-only; the existing explicit tag queue behavior is unchanged.

`metadata.source_history`, keyed by admitted source ID, covers every source input,
including failures, all budget cuts and rejects beyond the displayed ten. Entries
record `history` (accepted/missing/withheld), a bounded `reason`, `text_basis`
(rss/stored/not_available), and `currentness`/`content_version="not_attested"`.
Accepted rows alone contribute native integer `row_id` and present string/null
run/model/timestamp provenance. No scores, auxiliary payload or bad-row identity
are exported here. `original_rss_abstract` exists only when recovery changed it.

Each entry has either `candidate_ref`, a JSON pointer to its exact effective
candidate under `cards`, `rejected_near_threshold` or
`manual_full_text_required`, or one inline `candidate` when unrepresented there.
Consumers resolve the pointer in the same published JSON; no second input store
or duplicated candidate body is added. Deep-review `provenance.basis` is untouched.

This source-history boundary establishes compatibility, not freshness, current
assessment, scientific relevance or human usefulness. The consumed numeric and
native admission rules below are separate from identity/text compatibility;
existing source-history fields, exact originals and one candidate home remain
unchanged. Ranking, review generation, budgets, persistence and rendering stay
unchanged. Regression controls: `tests/test_research_feed_history_source.py`.
These checks establish machine wiring, not scientific or human benefit.

## RSS consumed-history and native admission

The existing `_summary` parser distinguishes absent/null `shap_contribs_json`
(unrecorded `{}`) from present malformed/non-string JSON or a non-object top
value. Inside a valid object, absent/null `summary` means `{}`; present false,
zero, empty string, list or any other non-object is invalid, not missing.

A single strict conversion helper validates only the currently consumed
composite and confidence values. Native integers/floats excluding booleans and
finite numeric strings are accepted in their raw domains: composite `[0,5]`,
confidence `[0,1]`. Empty, nonnumeric, nonfinite, boolean, container or
out-of-domain values fail per paper without exporting the raw error input.
A non-null native composite, including zero, wins over summary fallback;
only absent/null chosen values use the old operational defaults `1` and `0.5`.
These defaults are not observations or calibrated probabilities. Unused summary
fallback, goal and citation fields are not newly validated. Valid raw zero may
still project to descriptive ordinal 1; the existing priority mapping, rounding
and clamp are retained.

After compatible history, `_assess` still calls public
`triage_candidate(candidate, row, profile)` with three positional arguments.
Consumed shape/numbers must validate before any native bypass or veto. Valid
`selected` includes even off-topic. `user_rejected`, `gate_rejected` and
`rejected_low_score` are hard exclusions; `rejected_dedup_library` and
`rejected_dedup_processed` are duplicate exclusions, not scientific negatives.
Valid veto/duplicate rows retain their descriptive triage fields with only
`include=False`. `skipped_error` fails as unfinished. `triaged_pending`,
`awaiting_review`, `rejected_daily_cutoff`, `rejected_elbow`, `user_approved` and
`black_swan` use the existing ordinary triage rule, not an approval bypass.
Present unknown/missing/invalid native decisions fail. No prior row keeps the
pure operational no-history path. This native gate is only in `_assess`:
projected evaluator rows lacking native identity/decision remain legal pure
triage inputs. No source rows, decisions or scores are rewritten.

Each existing `metadata.source_history[source_id]` receives exactly one additive
object:

```json
{"admission":{"status":"eligible","reason":"triage_rule","native_decision":"triaged_pending"}}
```

`status` is pre-budget eligibility (`eligible`, `excluded`, `failed`), not card
selection, publication or human worth. Closed reasons are `selected`,
`native_veto`, `duplicate`, `triage_rule`, `no_prior_history`, `skipped_error`,
`invalid_native_decision`, `triage_history_invalid` and `history_withheld`.
`native_decision` contains only one of the compatible row's thirteen exact
producer strings, otherwise null. A valid veto observation remains visible even
if consumed triage fails; incompatible history contributes no native observation.
The existing history status/reason, text basis, provenance and candidate pointer
or inline body are not repurposed. Later review/card errors or budget cuts do
not rewrite this admission object.

Per-paper assessment failures retain a bounded reason (and exception class),
not raw exception messages. Existing review-stage diagnostics are unchanged.
Ordinal/confidence/title ordering and operational budgets remain unchanged;
continuous ranking, broader numeric lineage and evaluator cohort/benefit work
are B2 follow-ups. The thirty missing frozen machine inputs still yield null
unscorable metrics, not measured zero or a successful benchmark. New regression
sources are `tests/test_research_feed_numeric_admission.py` and
`tests/test_research_feed_native_admission.py`.

## Explicit injected Telegram page collection

`await telegram_collect.collect_pages(settings, usernames, client, now=clock)`
is an internal composition seam, not a public live CLI mode. Caller supplies an
already-owned async client, permitted content, invocation serialization and an
aware datetime clock. No connection/authentication/start/disconnect, event-loop
runner/thread, sleep, polling or automatic retry is performed here.

Injected client surface:

- `await client.resolve_channel(username=canonical_key) -> ResolvedTelegramChannel`
- `await client.fetch_page(channel_id=marked_id, after_id=None|int, limit=100) -> TelegramPage`

The complete exact-list input and every username validate before any directories,
migrations, DB/client calls; ordered canonical dedupe keeps the first supplied
spelling exactly. Empty returns zero work with account not_checked and no clock/I/O.
A nonempty invocation validates the first aware clock before I/O, then reads the
canonical durable retry deadline against a fresh aware UTC clock immediately before
EVERY resolution and fetch, including after separately committing the binding.
Future deadline stops all remaining account work; exact deadline permits requests.
Deadlines are never cleared, even when expired or after a hypothetical account change.

Reuse the existing selected triage migration runner/opener and M1a store. Resolution
is fresh before checking remembered bindings. Nonpublic results do not bind/fetch.
Binding commits separately before a fetch; mismatch never rebinds or creates the new
parent. Distinct configured aliases can bind the same numeric channel, but at most
one fetch attempt per numeric ID occurs per invocation, including failures. Later
aliases report same_channel_already_seen. Canonical spelling duplicates are removed
before any request; another explicit invocation can retry.

Bootstrap requests latest100 with after_id=None. Initialized-empty requests after0;
incremental requests oldest-first strictly after the confirmed ID. The injected leaf
must normalize the requested page into ascending order. Collector never sorts,
truncates or reconstructs a cursor from list_posts, timestamps or newest IDs. It
reuses the M1a private canonical page preflight to classify an invalid page locally,
then commits the complete original page/state in its own BEGIN IMMEDIATE transaction.
Commit's canonical boundary validation remains intentional. Invalid page means no
page writes; state/SQL/programming errors propagate and roll back that transaction,
without erasing earlier committed pages. There is no transaction across any await.

Typed network_error/channel_unavailable are local diagnostics; account session errors
stop remaining work. FloodWait uses fresh failure-time UTC plus validated seconds,
persists the later existing/new deadline in a short transaction, then stops. Failed
persistence/clock overflow propagates instead of claiming durable deferral. Only the
typed error is caught around client calls; unexpected exceptions remain errors.

Returned JSON-compatible dict has `channels`, aggregate `counts`, and `account`:

- channels remain in unique configured order with requested_username (first spelling),
  username (canonical), channel_id (validated resolved ID or null), and status.
  Only committed pages additionally carry counts, including all-zero empty commits.
- channel statuses: committed, binding_mismatch, same_channel_already_seen,
  invalid_page, network_error, channel_unavailable, missing_session,
  session_unauthorized, revoked_session, flood_wait, or not_attempted. An account
  stop leaves suppressed/current and remaining unattempted requests not_attempted.
- counts are fetched/new/updated/unchanged/skipped for committed pages only;
  new+updated+unchanged=fetched. Stored skipped observations can count as fetched.
- account has status not_checked (empty only), not_deferred, deferred, or an explicit
  session failure, plus retry_at (canonical observed/persisted UTC string or null).
  not_deferred is only no observed future deadline, never authentication/readiness.

No post bodies, raw exceptions/session strings, renderer/report artifacts or new
state pointer are returned/written. Caller composes later if separately admitted.
The account slot belongs to the selected Settings root and is caller-serialized,
not interprocess/account-wide coordination. M2 must separately own the session lock
before SDK construction through disconnect and obtain live/auth/content consent.
Existing saved/cached/default/RSS paths and publishers remain unchanged. Injected-client checks do not establish real SDK behavior, session ownership or
live-content permission.

## Optional injected SDK binding (no live activation)

The internal `telegram_collect.collect_pages` client contract now has a concrete
optional integration leaf, `integrations.telegram.TelethonPageClient(sdk_client)`.
This is not a new public mode or runner. The caller passes an already-owned SDK
client and a fresh leaf per invocation; no unchanged default/RSS/saved path imports
or constructs it. The sole dependency addition is optional `telegram` with
`Telethon==1.45.0`, not a base dependency or installation instruction for startup.

The collector remains unchanged: full-list validation, ordered canonical dedup,
per-request durable retry admission, fresh resolution before immutable binding,
one fetch attempt per numeric channel, separate binding/page transactions and
canonical hash/upsert/invalidation stay with the existing models/store/service.
The leaf uses literal fresh username resolution and a consumed native input peer;
it never repairs identity from a cache, joins channels or fetches forwarded sources.
It normalizes one whole raw history response into ascending IDs, using latest100
bootstrap and the SDK's oldest-first first-chunk offsets after initialization.

For known canonical representation gaps only (undated MessageEmpty/None original
dates or exact URL targets outside canonical HTTP(S)), the leaf returns
`TelegramPage(posts=(), complete=False)`. Existing M1b reports local `invalid_page`,
commits no posts/cursor for that page and continues healthy channels. No prefix,
fake publication date or dropped URL advances the cursor. Repeated responses may
stall that source indefinitely; full original preservation for these cases remains
open. Malformed values/spans/types/peers/order, unknown families and programming or
persistence failures are not disguised as incomplete pages or transport negatives.

SDK-bound after_id is None or strict integer 0..2147483646; native message IDs fit
positive signed32. The saved model's larger integer domain is not changed or
silently narrowed on disk. Out-of-wire-range cursors fail before the fetch request,
not as fabricated empty history. Raw captions/Unicode/whitespace and aware UTC
dates remain originals; exact URL targets/order/duplicates use UTF-16-aware SDK
extraction. The leaf neither hashes posts nor evaluates/serializes through JSONL.

Existing typed account/channel diagnostics and durable FloodWait policy remain
M1b-owned. There are no leaf readiness/auth probes, retries, sleeps, SQLite writes,
provider/model calls or output publishers. One explicit SDK call does not prove
one wire attempt/no SDK-internal RPC or sleep; resolution is point-in-time public
identity, not rename CAS. Neither `not_deferred` nor a completed page certifies
current authentication, content permission or whole-history completeness.

The M3 composition below owns the selected session lifetime around this unchanged
collector/leaf. The explicit public child adds auth/application identity as described
above; actual user consent and account activity have not been exercised. Live three-channel/manual/restart
proof, 400 RU/EN human labels, calibration, temporal holdout and hard-gate acceptance
remain unknown. Offline component/real SQLite test sources are not execution,
native-account or human-usefulness proof.

## Internal dedicated Telegram collect-only composition

```text
telegram_runtime.collect_once_cli(passed Settings, explicit factories, aware clock)
  -> existing load_profile(persist_default=False) -> whole-list M1b admission
  -> integrations.telegram_session.collect_once_cli owns native fuse/lease/Runner
       existing triage migration/opener + _admit_request BEFORE constructors/connect
       -> two factories -> connect -> unchanged TelethonPageClient/collect_pages ONCE
       -> mapped lifecycle error: existing _record_error BEFORE teardown
       -> one owned disconnect -> Runner.close -> receipt
  -> dedicated process MUST EXIT (fuse/lease still held)
```

This Python composition seam is now called only by the dedicated public CLI child
or owned tests. Factories remain explicit; the CLI provisions identity solely in its
leased construction callback. The two phases are `session_factory(session_path)`
then `client_factory(session)`; collection never logs in or discovers credentials.
Connect may perform SDK GetState/GetDifference/bootstrap work; connected is not
readiness or allowlist-only account access. The user is not asked for API keys.

Missing profile defaults are read-only for this entry; existing RSS/default callers
still persist defaults. Empty channels do no account/triage/SDK work. The complete
configured list reuses M1b validation and first-spelling dedup. Nonempty collection
requires a dedicated macOS CPython main-thread/main-interpreter process with Runner.
The integration checks existing private session metadata before either factory.
Retry checks use the real selected SQLite and fresh aware UTC before construction,
before connect and, unchanged in M1b, every resolve/fetch. Lifecycle FloodWait uses
M1b's existing fresh-time/max-later persistence BEFORE teardown, with no channel
falsely attempted. The callback captures failure UTC before opening SQLite and
passes a constant failure clock; blocked cleanup cannot postpone/shift retry_at.
Persistence failure remains loud/fail-closed while owned teardown still runs.
Only triage migrations run; no corpus/config/model construction is added.

Exactly one requested page per numeric channel: latest100 bootstrap, then the
existing oldest-first cursor contract. No extra loop/truncation/50-post workaround.
Existing original hashes, binding/page atomicity and committed-only counts remain
M1b/M2a-owned. Interruptions/errors may leave earlier commits without a returned
collection receipt; null is unknown, not fabricated zero. Unexpected SQL/programming
failures stay loud and no negative/auth label is invented.

The safety wall defaults90/max300 (`ZS_TELEGRAM_COLLECTION_WALL_SECONDS`); cleanup
reserve defaults10 (`ZS_TELEGRAM_CLEANUP_RESERVE_SECONDS`), finite/positive and less
than wall. These are unmeasured safety defaults. Native alarm and stable raw file
lease survive successful return through interpreter exit. **Do not run later ML/LLM
in that same process, embed/reuse it, disarm or unlock.** Public full-command
composition uses the separately terminated child described above. No normal/default CLI/bootstrap
`.env` behavior is changed. Other platforms and cached-native-signal/TOCTOU/ABA,
external-client/cross-root and OS delivery/exit/reaping limitations are not solved.
See integrations README for the exact process/cleanup restriction.

Authored tests cover real SQLite composition and owned-child lifetime boundaries;
platform/optional skips are not coverage. No live account/session/network, executed full ML→LLM/artifact slice,
permitted-content pilot or human acceptance is claimed by this source component.
Separate M2b work must own the selected Settings-derived session target before
retry admission/file checks/SDK constructors through completed disconnect, ensure
missing sessions cannot be created accidentally, and supply process serialization,
permissions, cancellation-safe cleanup and explicit consented activation/auth.
Live three-channel/manual/restart proof, 400 RU/EN human labels, calibration,
temporal holdout and hard-gate acceptance remain unknown. Offline component/real
SQLite test sources are not execution, native-account or human-usefulness proof.

## RSS whole-cohort continuous ranking

This B2 production stage supersedes only the earlier RSS statements that included
ordering remains ordinal/confidence/title based. Source compatibility, exact
original recovery, the three-positional public triage callback, thirteen-state
native admission and existing budgets remain separate and unchanged.

```text
existing bounded source cohort -> B1 admission/triage -> valid observed cohort
                                                       -> same blend once
                                                       -> unscored legacy tail
                                                       -> shortlist -> card budget
                                                       -> existing reviews/cards
```

`source.py` owns the single stored-history `_summary` JSON parser and `_number`
conversion used by triage and ranking. Default summary parsing, fixed B1 errors,
raw composite/confidence domains and operational defaults remain unchanged.
The parser also selects nullable `aux_context`; numeric conversion additionally
supports signed goal values. No second parser, score carrier/cache, reader or
profile/model/configuration switch is introduced.

`ranking.rank_candidates(assessed, accepted_rows, source_order_ids)` is pure.
It orders only B1-included pairs and uses accepted stored continuous composite,
not descriptive `ResearchFeedTriage.score` or confidence. A non-null native
`composite_score`, including zero, wins; only absent/null uses observed
`summary.composite_relevance_score`. Both missing/null means **unobserved**, not
an observed operational one. Eligible unscored papers follow the scored cohort
in the old descending ordinal/confidence/title order, with exact ties stable on
source order. They do not consume aux or participate in normalization.

Only eligible observed-score papers consume `aux_context.goal_sims` and
`citation_percentile`. The goal map may be absent/null/empty; otherwise every
member must be a non-null finite nonboolean number or numeric string in `[-1,1]`
before taking the maximum. Negative and zero cosines remain observations; a null
member or present wrong shape is invalid. Citation is a known finite nonboolean
number/numeric string in `[0,1]`, or unavailable None; known zero is not missing.
Author priors, display prestige, counts, prose and unused fields are not substitutes.

The unchanged `model.rank_blend.blend_scores` runs once over the entire valid
observed cohort **before both budgets**. Existing min-max normalization, absent
signal weight fold-back, missing-goal contribution, median-of-known citation
handling and degenerate/sole-positive-goal rules remain its responsibility.
There is no robust-percentile normalizer, per-row weight renormalization,
quality-first/bonus policy, new model work or weight/default change. Scored ties
retain explicit source-admission order, not confidence/title/ID order.

One separate `metadata.ranking` has `contract="weekly_rank_v1"`,
`cohort_scope="b1_eligible_valid_observed"` and source-ID-keyed `inputs` for every
admitted source. Every input has zero-based `input_order` and a status:

- `not_applicable`: no further fields; B1 exclusion/failure is recorded only in
  the existing source-history admission object.
- `unscored`: reason `no_observed_composite`, null relevance/blend key,
  `relevance_origin="unobserved"`, and one-based pre-budget `order`. Aux is omitted
  because it was not consumed, not falsely declared known absent.
- `scored`: finite `relevance`, origin `native_column` or `summary_fallback`,
  signed/null `goal_sim`, known/null `citation_percentile`, actual `blend_key`,
  and one-based pre-budget `order`.
- `failed`: only bounded reason `ranking_input_invalid` beyond input order/status;
  one corresponding `ValueError: ranking_input_invalid` joins operational failures.

Invalid consumed papers never affect cohort statistics, review selection or tag
targets; healthy peers continue. Duplicate/missing cohort identities are loud
programming errors, not scientific exclusions. Existing `source_history` fields,
admission/provenance and one candidate body/pointer remain unchanged; no bodies,
raw goal maps, summary payloads or model/configuration blobs are duplicated in
ranking evidence. B1 eligibility can coexist with a separate B2 ranking failure.
Triaged count remains the existing assessed count. Ranking metadata stays
pre-budget after cuts, missing full text or review/card failures; there is no
review/card backfill or additional source query.

The current offline evaluator remains **legacy ordinal projection**, not a replay
of this B2 policy. Explicit v2 frozen pre-human B1-output/cohort/order/provenance
replay and its reviewed versioned oracle migration are the next component, not
implemented here. The thirty missing machine inputs still mean NULL/unscorable,
not measured zero or acceptance; the separate 17-row reading-policy diagnostic is
not paper-inclusion evidence. Synthetic ranking controls establish machine-I/O
contracts only, not scientific/human usefulness, calibrated probabilities,
production uplift or whole-issue readiness.
