# services/research_feed — weekly Research Intelligence

The RSS command projects existing app RSS and current deep-review artifacts into
a project-specific engineering digest. An explicit saved-input Telegram mode
composes the existing reranker and default-provider client for news, without live
collection, a paper classifier, PDF extraction or scientific review fields.

```text
rss_items → source/dedupe → prior triage → budget → existing deep_review
          → engineering card → data/research_feed/weekly-<run-id>/{*.json,*.md}
                                      └─ optional reviewed Zotero tag queue
```

| file | responsibility |
|---|---|
| `profile.py` | Versioned user-editable profile and controlled topic taxonomy under `data/research_feed/profile.json`. |
| `source.py` | Bounded date-range adapter over app-owned RSS plus DOI/source/title dedupe. |
| `runner.py` | Budgeted triage, optional missing deep reviews through `library.deep_review`, per-paper failure isolation, metrics/watermark, and dry-run/idempotent writeback. Tag idempotency looks up pending changes by target Zotero item, so unrelated older queue rows cannot push a matching writeback beyond a global scan cap. |
| `card.py` | Pure engineering-card projection; only exact validated artifact URLs survive. |
| `render.py` | Shared staged JSON/Markdown persistence; optional retained inputs and invocation-time renderer selection. |
| `telegram_source.py` | Strict bounded version-1 JSONL posts, source line diagnostics; no live transport or embedded assessments. |
| `telegram.py` | Selected-store imports, content/identity-bound reuse, shadow ML before default-provider LLM, explicit stage states and bounded new attempts. |
| `telegram_render.py` | News-only cards, separate uncertainty and pending/error/skip diagnostics; no paper quality projection. |

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
Live Telethon/auth/entities/rebinding/FloodWait/page recovery and human-labelled science,
consent/manual smoke and hard-gate activation are separately unimplemented/unverified.

**Boundary:** RSS may queue explicitly requested reviewed Zotero tags; Telegram never
queues or writes Zotero and uses only its two same-DB tables and local report artifacts.
