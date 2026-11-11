# services/research_feed — weekly Research Intelligence

The fork-A pipeline is `telegram_collect.collect_pages`: per channel it fetches
incremental `min_id` posts and commits them, then (when a reply client, embedding
`scorer` and owner `goals` are supplied) runs the post-commit digest in
`telegram_digest.run`. `telegram_relevance` holds the pure helpers — `dedup`
collapses exact forwards (`fwd_from`) and near-duplicate normalised text keeping
the earliest; `gate` scores posts against the goals (SPECTER2 via `specter2_scorer`
for Latin text; non-Latin posts use the injected `keyword_probe`, because SPECTER2 cosine on
Russian is ~0.9 for every post) and carries the matched 'why-for-you' goal. **The digest is
discussion-first** (Todo #119): only posts with `replies_count > 0` are candidates; the
`POOL_FACTOR × summary_top` most-replied get a goal score (embedding is the slow part), then
comments are fetched for the `FETCH_FACTOR × summary_top` best by `telegram_ranking.prior`
(serial; a FloodWait stops comments, defers the account and keeps posts; a post whose
discussion cannot be resolved is skipped, not the run). The final order is
`telegram_ranking.score`: reply count + total comment length (primary; link-only, self-promo/CV and one-word
comments weigh a tenth, a link with real text counts in full) plus a soft goal
boost (`goal_fit`, 0 at or below `tau`), so a lively off-goal post can make the digest and a
silent on-goal one cannot; posts below `MIN_SCORE` are not shown. Snapshots go to
`research_feed_conversations`, `last_comment_id` into `evaluation_json`, and the top
`summary_top` (default 8) are read **in full** (Todo #121: ≤300 comments, `GetReplies` paged
100/request, `replies_per_thread`/`messages` ≤300) and get a **faithful gist** (Todo #174, `GIST_VERSION` g4; measured in `data/perf-20261107/telegram/faith/phase3/report.md`).
`telegram_extract`: **code picks up to 4 comments** (substantive ≥60 chars; score = replies underneath + bge-m3 relevance, MMR-diversified;
greetings/promo never reach it — `Thread` keeps only `ranking.weight ≥ 1` comments), then **ONE call per thread** writes one line (≤15 words,
the thread's language) per picked comment. **Ids come from code** (`k:` ↔ the k-th pick; the model never writes an id; unmapped rows are dropped). A **local gate**
(`telegram_nli`: mDeBERTa-v3 XNLI, CPU, ≈0.55 GB, loaded lazily from `Settings.nli_model_dir` = `data/models/nli-mdeberta`; get it with
`huggingface-cli download MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 --local-dir data/models/nli-mdeberta`) scores «parent +
cited comment ⇒ line» and drops lines below 0.5; **without the folder lines stay unchecked** (warning in the log). Measured on 30 threads
(hand labels, strict): lines supported 85 % (held-out) → 93 % gated, 0 contradictions; a «Спор:» disagreement line (g2) was dropped in g3: on the 30 labelled threads it was ≈63 % supported and no gate threshold reached 0.85 precision (best 0.80 at p ≥ 0.95, n=10). Sub-thread lines (root + replies per line) were tried and rejected: 65 % held-out. **Disagreement (g4, Todo #184).** The generated «Спор:» line is not coming back; a disagreement is *detected*, not written. `telegram_dispute`: candidate pairs are reply pairs touching a picked comment plus the picked comments among themselves (≤12, reply pairs first); ONE `integrations.jev.noul` request per thread asks Jev two Noul questions per pair («Комментарий B возражает комментарию A?» / «…утверждает противоположное…»); a pair counts when the smaller answer is ≥ `TAU` 0.70 and the best one (≤1 per thread) becomes a point `{kind:'disagreement', text, cites:[A,B]}` whose text is the two sides' own gated lines (or a ≤100-char verbatim «quote» when a side has no line) joined by « ↔ » — no new sentence; the page shows a «Disagreement» label and the usual expand opens both cited comments. Measured on 371 hand-labelled pairs (all candidates of the 30 threads; labels written before any score; single labeller): precision 19/19 at 0.70 (Wilson 0.83–1.00), 11/11 at 0.75; held-out 20 threads 11/11 at 0.70 (0.74–1.00) but 6/6 at 0.75 (0.61–1.00); dev-tuned thresholds did **not** transfer (held-out 0.51–0.65), so 0.70 is the docs' starter value, not a fitted number; it meets the ship bar on held-out (11/11, lower CI 0.74). Production `extract()` with real Jev at 0.70: 10/10 and 11/12 blocks right in two runs (≈1/3 of threads; one label-ambiguous pair moves across the threshold between runs). Local alternatives measured and rejected: mDeBERTa contradiction AUC 0.56, cue words 0.65, local 4B yes/no 0.52 (says «да» 97 %) → **no Jev (no `TYPESAFE_API`, `ZS_OFFLINE`, request failure) = no disagreement block**, never a weak fallback. Jev sees the pair's comment text (owner-approved 11-10); `integrations/jev.py::endpoint` is the single place that resolves URL + key, where the Cloud/Local engine setting (Todo #147, REQ-S35) plugs in. A line whose comment answers another picked comment whose line survived carries `reply_to` (the parent comment's stored id) and the page shows «↳ reply to …» — pure reply structure. `gist_points` replaced `gist`/`points` (points are built once, never re-parsed from text). `summaries()` returns per
post `points` (`{text, cites}`) and `continues`; the digest records `gist_version`. Comments are **not** in `telegram-today.json` (≈840 KB → ≈30 KB for 11 posts): the page loads them on expand through `GET /telegram/thread/{source_id}` (`telegram_today.thread`: `id`, `reply_to`, `text` merged over the post's `research_feed_conversations` snapshots; a malformed id → 404; `read_today` also strips `thread` from payloads written earlier).
With no model, no survivor line or no substantive comment the page shows the top 3 comment ids labelled 'no summary'. **«Важное»**:
up to 3 comments from anywhere in the thread (e.g. #250) that carry concrete facts (number,
tool names, link with text; two facts, or a number/tool names in a ≥60-character comment) or hit a goal probe
are stored as `important` (`id`, `goal`, `quote`) and shown on the card; a goal match is a
bonus, never required (comments are for staying in the loop, not goal matching). `telegram-today.json` posts are in rank order. Topic
labels are requested in the posts' language. The source cap is the 30-channel `max_length`
(the stored `limits.sources` is not enforced); measured 23 channels end-to-end ~2–3 min on a
local 4B model (collection wall 480 s, max 600 s).
`TelegramPost` schema_version 2 carries `fwd_from`/`reply_to_id`/`replies_count` inside
`post_json` (no SQL migration; `fwd_from` and `reply_to_id` are captured at ingest from now on, older rows stay empty, and `reply_to_id=None`
is left out of the content hash so old rows are not re-evaluated); `TelegramMessage.sender_id` (numeric user id only) is captured the same way.
`telegram_series.parents(posts)` links a post to the earlier stored post it continues: `reply_to_id`, or a `t.me/<channel>/<id>` link under a marker
(«часть N», «продолжение», «предыдущий пост») written within 40 characters of that link; a reply into another chat is not a parent (`reply_to_id` is None); the digest feeds the parent into the gist and `telegram-today.json` gets `continues`.
Posts in the today payload also carry `also_in` (the weekly topic they belong to, from `topics[].source_ids`). The Telegram model is chosen under
Settings → Telegram sources (own provider/model/limits; it is deliberately not a 'Choose models' scope). The source cap allows ~30 channels.

`telegram_trends.weekly_trends(conn, now, *, scorer=None, model=None, gists=None, cache_path=None)`
builds the page's **Trends** topics from the posts stored for the last 14 days (deduplicated
text posts; digested posts add their gist, other read threads their two longest comments).
SPECTER2 vectors are centred on the two-week mean (raw cosines on Russian sit at ≈0.93) and
joined with TF-IDF over 5-letter word stems; average-linkage groups over BOTH weeks (an oversized
group is split once) that hold ≥3 of this week's posts from ≥3 channels become topics (≤15,
most channels first), and the trend marker compares the same group's posts of the two weeks
(`new` ≤1 earlier post, `growing` ≥1.5×, else `steady`). **One** local-model call (`model` is a
`prompt -> text` callable from `telegram_processing.model`: local by default, remote only with
consent; none → headline labels) returns per topic a short label, a one-line gist (≤12 words, no «обсуждают/спорят» opener) and
1–2 notable ideas/tools, in the posts' language; a topic the model answers `-` for (memes, ads, digests) is dropped.
Result JSON: `topics` (`label`, `gist`, `ideas`, `trend`, `posts`, `baseline_posts`,
`channels`, `source_ids`, `examples` with permalinks), `posts` counts (schema 3); cached at `cache_path` for an
unchanged week (the fingerprint carries a prompt version, `tp5`, so a prompt change regenerates labels) — no table, no migration. The pass adds `counts.trends_s` to the today payload.

`telegram_workflow` owns narrow Sources save/status and `begin` (validate sources/provider via
`telegram_review.resolve_local`, publish the `collecting` state, return `execute()`), under one
cross-process lock. `telegram_job.start` is the **«Check channels»** background job behind
`POST /telegram/runs` (202): it needs the remembered one-time consent
(`telegram_processing.set_collect_consent`, `POST /telegram/collect-consent`, file
`collect-consent.json`, revocable; the old per-run checkbox is gone), takes `_exclusive`, runs
`execute()` on one daemon thread and returns at once; a second call while it lives returns the
running status (never a second run; another action holding the lock → `409 telegram_busy`).
`GET /telegram/status` adds `running`, `consented`, `started_at`, `elapsed_s`, `retry_at` (account
flood-wait still ahead) and `progress` (`stage` channels→comments→summaries with `done/total`,
written by the child through `telegram_progress`, a no-op outside the digest child). `operation.json`
keeps the server `pid`; a `collecting` state whose pid is dead or older than 10 min reads as
`error: interrupted` (server restart mid-run never sticks). Errors are codes the UI renders in
English: `missing_session|session_unauthorized|revoked_session` (the only case that shows the
`telegram-auth` hint), `deferred` (+`retry_at`), `busy`, `model_unavailable` (`telegram_live`
probes the model endpoint before the child starts), `telegram_incomplete`, `interrupted`,
`telegram_action_failed`. The job runs the **one pipeline** behind an
injectable `run_collect` seam: the default `telegram_live.collect` spawns the dedicated
Telegram child (`integrations.telegram_child.run_child` purpose `digest`), which runs `collect_pages` + digest and
persists the result through `telegram_today`. `telegram_live.digest_seam` builds the live
reply client (`integrations.telegram_reply_adapter.ThreadReplyClient`, reusing the bounded
read-only `telegram_threads` discussion fetch), the SPECTER2 scorer, owner goals, the
summary/label models and the trends model; the offline harness injects a fake collector so
the only code not exercised by tests is Telethon construction (verified live only after the
owner authorizes Telegram, Todo #99). `telegram_today.collect_and_persist` writes a
latest-wins `telegram-today.json` under `data/research_feed/telegram/` (and caches weekly
trends in `telegram-trends.json`); `read_today` serves it to `GET /telegram/today` (posts,
discussion summaries, weekly trends) without any collection, model or network call. This
one pipeline is the only Telegram collection path: the old `conversations` child
purpose, `telegram_conversations.collect/publish` and the topic-generation prompt
(`telegram_review.prepare/generate`, `models/telegram_generation.py`) were retired (Todo
#109). `telegram_selection` holds the saved source selection (`targets`) and the report
directory; `telegram_review` keeps only `resolve_local` (the Telegram-only loopback route
check) and `validate_topics`/`admitted_sources`, which `reports` uses to keep reading
historical v2 bundles unchanged.

Decisions (Todo #109). **Interests:** posts are ranked against the app's research goals
(`config.research_goals`, Settings → Article selection), so no CLI is needed; the legacy
profile themes/projects are only a fallback when that list is empty (they still drive the
weekly engineering digest). **Providers:** summaries and trend labels stay local-only
(`resolve_local`, loopback) unless the owner consents to a non-local provider (Todo #112,
below). **Times:** the page shows relative ages (`12 min ago`, `3 d ago`, `Updated 1 h ago`) and, for a Telegram wait, a clock time in
the browser's time zone (`frontend/src/components/telegramRunText.js`).

Decisions (Todo #112). **Remote summaries (brief §2.5):** `telegram_processing` admits the
saved Telegram provider/model if it is loopback (unchanged default) or if it is non-loopback AND
`data/research_feed/telegram/remote-consent.json` holds the token of that live boundary — the
`services/card/_consent.py` pattern, with the token a hash of provider name + endpoint + model
(changing any drops the consent; `ZS_OFFLINE` refuses remote). `POST /telegram/processing
{token, allow}` records/withdraws it; `GET /telegram/sources` returns `providers` (non-loopback
names) and `processing` (`provider`, `model`, `local`, `token`, `remote_consented`) so the UI shows
what will see the text. Saving sources, the run consent and the digest never imply it; without
consent a remote selection refuses the run (`telegram_local_required`) and the seam yields no
model (top-comment fallback). Only the summary and trend-label calls use it; collection stays
Telegram-read-only. **'Не подошло' chips:** `telegram_reasons` stores the owner's rejection in
`research_feed_telegram_posts.reasons_json` (numbered migration 16, nullable; NULL = not rejected,
`{"chips": [...], "v": 1}` = rejected, chips may be empty). A column was chosen over the
interaction/exposure log because that log is an append-only JSONL, not queryable per post, and a
post row already survives re-collection (upserts do not touch the column). Chip ids are Today's
negative set (`_daily_verdict`; services cannot import api, unification is Todo #108).
**Liked feedback (Todo #169):** the same column holds payload v2 `{v, rejected, chips, liked, note}` (v1 = rejected; no migration):
`liked` ∈ `useful|new_to_me|want_to_try|good_discussion`, `note` ≤1000 chars; `POST /telegram/feedback {source_id, liked, note}`;
`GET /telegram/feedback/export` downloads every post with feedback (text, permalink, reasons, liked, note, `at` = last change, `done_at`) as JSON. Stored and exported only — nothing ranks by it yet.
**Past digests:** `telegram_history.archive` writes each finished run once to `data/research_feed/telegram/history/<UTC stamp>.json`
(append-only, `-2`… suffix for a second run in the same second; `persist_today` also archives the file it overwrites; the today payload as is — comments stay in `research_feed_conversations`; cited ids are kept).
Feedback writes use `BEGIN IMMEDIATE` and `mark_done` a process lock (no lost updates); an unreadable trends cache is a miss; failed snapshot/cursor/deferral writes are logged and counted in `counts.write_failures`. `history/done.json` maps `source_id` -> time for discussions marked done (global across digests). `GET /telegram/history` lists runs
(`run_id`, `generated_at`, `discussions`, `unread`, `lead` = first trend label or busiest channel); `GET /telegram/history/{run_id}` serves a snapshot with the live owner state overlaid;
`POST /telegram/done {source_ids, done}`. `read_today` overlays `done`, `reasons`, `liked`, `note` the same way. Archived files are never pruned.
`POST /telegram/reasons {source_id, chips|null}` (null undoes; unknown chip 422, unknown post
404); `GET /telegram/today` overlays `reasons` from the rows. The workflow status no longer
carries `report_id` (no Weekly report is produced by the one pipeline).

Reports/read GETs never activate Telegram/models; Sources GET/save also remain
effect-free except the explicit save. Successful child authorization plus a clean receipt
permits historical verified status, not a session-file guess or continuing validity claim.

Connection-task impact includes Telegram's independent model/profile revision and
literal-loopback admission (ordinary `.is_local` is insufficient). Default changes
do not repoint this route. Source saves share the configuration publication lock
inside the existing operation lock. App credential replacement also requires that
operation lock, so a started Telegram config snapshot cannot later receive a newly
replaced shared slot through the app editor. This does not cancel a run, roll back
keyring effects or serialize external/uninstrumented credential/profile writers.

One bounded command projects the existing app RSS and current deep-review
artifacts into a project-specific engineering digest. Missing abstract triage
reuses the existing abstract pipeline; project proposals share the existing review
response. Missing abstract work resolves its lazy feed client through
`services.llm.factory` with the existing runtime cache; cached-only reads never
resolve clients. It adds no crawler, PDF extractor, judge or model backend.

```text
rss_items → source/dedupe → prior triage → budget → existing deep_review
          → engineering card → data/research_feed/weekly-<run-id>/{*.json,*.md}
                                      └─ optional reviewed Zotero tag queue
```

| file | responsibility |
|---|---|
| `telegram_reasons.py` · `telegram_history.py` · `telegram_processing.py` | Per-post feedback (negative chips, liked chips, note) on the post row; per-run digest archive + done state; local-by-default Telegram model resolution with token-bound remote consent (Todo #112). |
| `profile.py` | Versioned user-editable profile and controlled topic taxonomy under `data/research_feed/profile.json`. |
| `source.py` | Bounded RSS adapter/dedupe, history identity/text and native-state admission primitives, final one-home provenance. |
| `cursor.py` | Source/profile/config/model/schema-bound successful abstract-stage cache; no derived-card/report replay. |
| `ranking.py` | Continuous current-cohort relevance/goal/prestige ordering through the existing blend, before budgets. |
| `runner.py` | Budgeted triage, optional missing deep reviews through `library.deep_review`, per-paper failure isolation, metrics/watermark, and dry-run/idempotent writeback. Tag idempotency looks up pending changes by target Zotero item, so unrelated older queue rows cannot push a matching writeback beyond a global scan cap. |
| `card.py` | Base-qualified engineering-card assembly; only exact validated artifact URLs survive. No extraction or model calls. |
| `render.py` | Canonical JSON and compact Markdown persistence (title shows the run range as «14–20 Oct 2026»; one identical project-use sentence is written once for all its projects; snake_case reproducibility keys and rationales print as plain words, e.g. «Dataset provenance»). |
| `reports.py` | Read-only list/detail/download lookup of published v1 RSS, retained v1 Telegram news and additive v2 Telegram conversation bundles, validated with existing candidate/triage/card models and present source/generation provenance references. Missing legacy provenance stays optional; malformed present references refuse. Source observations can retain an unqualified generation field; card provenance requires a valid generation hash. Listing labels unopenable runs with a one-line `reason` (old flat-file digests appear once, not openable). Reject symlink/path escapes; never generate or migrate artifacts. |

Run weekly: `uv run zotero-summarizer research-feed run --from 2026-08-22
--to 2026-08-29`. Add `--venue NeurIPS` for conference mode, `--cached-only`
to avoid new model work (without it, new papers are screened serially, one remote call each; stderr shows one «screening N new papers» line, then «screened 40/432 (2 failed) · 3 min» every 10 papers and at the end, via the `progress` callback of `run_weekly`), or `--queue-zotero` to opt into reviewable tags. A
cron/launchd entry can invoke the same command weekly. Edit the generated
`profile.json` to add a theme/project. Each report name includes a deterministic
fingerprint of its date window, venue, profile, and output-affecting run options,
and a run suffix so repeated executions retain immutable results. JSON and Markdown
are staged together and published by one same-filesystem directory rename; the root
`state.json` pointer is atomically updated only after publication. A state-write
failure leaves the prior pointer and complete bundle intact. Independent runs ending
on the same date keep separate artifacts. Reversed date windows fail before
profile/source reads or writes. After budget admission, RSS output-root/state
symlinks refuse before reader/source/model work, including native-history runs.
The shared publisher rechecks its directory before staging, including custom
news renderers. These existing-link checks are not symlink-swap race containment.
The sole RSS adapter is `load_candidates`;
the unused source protocol and state-only class wrapper are removed.

Cards use original-quote-bound project assessments, not topical project-use or
implementation-to-idea heuristics. Each positive proposal names mechanism,
component, change and validation experiment; unknown/partial is not no-fit.
Legacy reviews without assessments project no positive uses/ideas. Recorded goal
observations with exact configured project keys, usable raw retrieval/boolean state,
nonblank summary and recorded quotes remain in the existing note home, explicitly
unverified. Text, order and duplicates are preserved; malformed/degraded observations
are not copied. This does not re-authenticate quotes, change project assessment,
score, impact or ideas, and never rewrites prior bundles or starts a model. Explicit
impact/reproduction assessments are AI-generated and scientifically unverified;
null impact means unassessed (legacy numeric zero fields are placeholders).
Code availability alone never implies effort. Existing blockers and artifact URLs
remain; an effort estimate cannot override missing verified code or critical inputs.
Markdown gives tier, rationale, missing inputs and evidence gaps one labelled home
each. An empty missing-input list says “none reported (AI assessment, unverified)”
only when a reproduction assessment exists; otherwise it remains “not recorded (not
assessed).” Direct legacy presentation uses explicit unrecorded values for missing fields;
this does not relax the typed saved-report reader or rewrite retained JSON/bundles.
`tests/test_reproduction_uncertainty.py` preserves all six prior oracles.
`tests/test_feed_engineering.py` covers producer, binding, cache, rank, error and
CLI publication contracts using scripted models, not real usefulness evidence.

Current reviews are source-qualified on every run; PDF bytes are hashed even for
same-size/timestamp replacements. Source-access and assessment failures remain
per-paper errors, including on warm runs; a usable legacy card cannot hide a
failed assessment attempt. Missing source has no scientific verdict. New JSON
schema v1 bundles keep existing keys plus additive assessment/provenance fields;
saved reports are never rewritten. CLI returns 1 when a partial run has errors.
Successful abstract-stage writes use the existing atomic JSON writer under
Settings.data_dir; reviews keep their existing owning cache writer. Neither is
an fsync/power-loss guarantee or a cross-process generation claim.
Missing abstract-stage caches are explicit cached-only failures, not low-relevance
rejections; reports and logs name failed papers by title (from `metadata.source_history`), never the `feed:g:` key, and `counts.not_triaged` counts those cache misses so the CLI exit code ignores them (a cached-only run exits 1 only for other failures). The report lists genuine failures first with plain reasons (`PLAIN_REASONS`, e.g. `history_abstract_conflict`) and folds the cache misses into one line, «N papers were not screened yet»; the log has one summary line instead of a warning per paper. Optional cache errors affect only candidates needing that stage;
valid history neighbors still publish. Generating runs may reacquire missing PDFs
through existing headless sources, never a browser. V1 provenance keeps numeric
triage confidence; assessment confidence remains separately unknown.

Source availability and review qualification are independent observations. Missing
reviews/provider failures stay unknown in `unqualified_reviews`, not the manual
PDF list. Only observed `offline_uncached`, `needs_login`, `fetch_failed` or
`no_oa_source` acquisition outcomes make that list; a skipped browser, missing
browser capability or unknown code does not establish unavailable full text.
Bound source identity remains available when generation/basis qualification or
card projection fails. `full_text_evidence` retains the binding and qualification
state; it is not a second source reader or scientific verification. Direct card
projection shares the same retained-base-audit gate, including when optional feed
assessment is absent. Missing/stale and malformed evidence remain explicitly
unqualified without losing observed source availability or rendering rejected claims.
Known rejected base proof remains a per-paper error in the existing failed home,
not an ordinary pending/missing-review result; partial-error exit behavior is preserved.
New report publication projects source bindings to their kind/source/generation
hashes and replaces the full optional receipt with its status/origin/verification
labels plus `retained_payload_sha256`, an explicit reference to private evidence.
It exports neither original source paths nor focus input and never re-signs a view.
Private reviews and invocation evidence remain intact; prior report reads/downloads
still return their original saved JSON/Markdown without retroactive migration.
Receipt qualification reads code-contract identities, not a second paper extraction.
The default current-review reader still owns byte/config freshness; injected loaders only supply scoped
observations. The existing Markdown pending section and one-home history ledger
carry unqualified candidates without retry/backfill or automatic work.

Offline acceptance is `uv run python tools/eval_research_feed.py --check`.
The shipped 30-paper fixture records human inclusion and verified artifact labels
but lacks frozen pre-human abstracts/scores/summaries and review outputs. The
checker exits nonzero and reports unavailable (`null`) production metrics,
not fallback-path zero precision or by-construction code-link precision. The
separate 17-paper fixture supplies a clearly labelled reading-policy diagnostic.
The evaluator requires frozen pre-human machine inputs independently of labels;
inspect its current fixture and tests rather than interpreting missing inputs as
measured inclusion quality.

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
Cached-only CLI projection skips app startup entirely. Qualifying existing review
or abstract-stage caches requires the EXISTING valid goals config, read through
`read_config` into invocation-local context; absent/invalid config is an explicit
qualification error, not unsupported science. It constructs no clients/classifier,
creates no config, and performs no dotenv/keyring discovery. Source-current but
config-unverified entries cannot qualify. Already-published report viewing is a
separate outcome, not current card generation. Cached abstract summaries validate
strictly: a matching input/checksum cannot coerce booleans or numeric strings into
typed observations. Refusal preserves bytes and does not implicitly regenerate.
Generation uses the existing no-background startup mode: no prewarm, server-job
recovery, classifier retraining or slate rescoring. Enabled model prerequisites
must already be prepared for generation; they do not apply to cached-only reads.

Stored triage `summary` and `aux_context` may be absent or null: both mean an
unobserved object, not a malformed paper. Non-null non-object values still refuse;
numeric zero remains an observation and stored payload bytes are never rewritten.
Only a missing/None top payload is unobserved; present blank/non-string JSON,
non-object payloads and malformed consumed objects refuse. Consumed numeric values
admit finite numeric strings within the caller's domain, not booleans/containers.
Pure parse diagnostics are bounded; failed triage projection reports/logs
`triage_history_invalid` without echoing the bad scalar or relabeling other stages.
Operational triage defaults do not become invented ranking observations.

History identity/text admission returns the effective candidate plus bounded facts;
its caller retains every input before failures or budgets. Only identity-compatible
native rows contribute typed row/run/model/time provenance, marked `not_attested`.
Whitespace controls availability, not retained bytes: substantive stored text can
replace blank RSS text; substantive RSS survives a blank stored observation.
Native state admission is separate from the public three-positional descriptive
projection. Consumed numbers/callbacks run before native disposition; valid native
observations survive projection failure, while unknown raw states are withheld.
Missing native history still requires the existing qualified abstract cache (or
explicit generation); synthetic cache decisions never become native observations.
Callback failures retain only their type and bounded triage diagnostic, isolating
peers without inventing a result. Final `metadata.source_history` points to one
exact existing candidate body or retains it inline, including failures/cuts; list
order/duplicates matter. Saved v1 reports are not rewritten or recertified.

Source admission is newest-first (row ID breaks equal-time ties), filters venue
case-insensitively, then streams the existing DOI/source/title deduplication before
the unique-result cap. Earlier unrelated or duplicate rows cannot consume the
budget. `discovered`/`deduplicated` count admitted unique matching papers, not all
raw rows in the date window. Cursor consumption stops at the cap; sorting/scanning
the underlying window can still require database work. Malformed nonempty source
timestamps raise instead of silently becoming absent dates.

Report listing merges RSS/Telegram by saved timezone-aware `generated_at` before
the shared limit; unavailable/undated bundles use non-following bundle mtime, with
names only breaking equal-time ties. Reads scan/validate retained bundles one at a
time and retain only small list entries; no new index or report-byte rewrite.

The **Weekly** page opens existing bundles from the CLI, including older v1
snapshots without optional provenance or grouped ideas. Missing values stay
unrecorded; reads return saved fields unchanged. The list retains unavailable
entries without hiding valid neighbors. Missing bundles/files are 404; malformed
JSON is 422 `report_unavailable`, while unsupported schema/source is 422
`report_unsupported`, including downloads. Card counts must match saved cards.
A missing report folder is empty; directory scan failures are 503, never empty.
Optional unqualified-review rows validate their existing candidate and nonblank
reason before display/download. Legacy absence stays absent in returned JSON;
valid saved text/order/duplicates are unchanged. The UI lists these rows with
review-status reasons beside existing source needs, not as unavailable PDFs, and
its existing paper links never auto-start review.
Markdown is served verbatim only when canonical JSON validates. A missing Markdown file does not prevent
reading valid JSON. Downloads return the qualified JSON or selected Markdown bytes captured before the response,
so later file changes cannot replace bytes during streaming. There is no browser RSS run/build endpoint or cached-card replay; Telegram’s separate explicit bounded source action is described above.

**Boundary:** may compose library services and storage reads. Zotero writes are
optional pending actions only; default runs are dry-run.

The [Telegram source](../../../docs/features.md#telegram-news) has explicit CLI
authorization, collection and saved-report paths. Transport/session state remains
private; report reads do not activate an account or model. Saved-input processing
selects the explicit JSONL post identities from stored rows, including older posts
outside the global newest window; an empty input yields an empty report. Cached
and live collection still use their bounded newest saved window. New reports label
the selected scope; retained bundles are not rewritten. Known parent/news/state
symlinks are rejected before inputs or models; the parent is rechecked before
publication projection. Direct publisher
leaf-directory checks remain separate; none is filesystem-race containment.
Live account/network
acceptance is separate from the synthetic contracts.
