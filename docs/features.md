# Features

Find what you want to do below: each paragraph says what you do, what you get and the
limit to know, then links to the code and tests. AI output is never scientific validation.
Configuration defaults live in the generated [override reference](overrides.md).

## Get started

### Setup and configuration

Run Setup (it opens on first launch; `zotero-summarizer setup` works in a terminal) to
choose research goals, Zotero paths and AI or ML-only mode (ML-only uses the local
relevance model without an LLM). Missing `goals.yaml` and `.env` are created, existing
configuration is never overwritten, and app state lives in the gitignored `data/` folder;
keep `.env`, account sessions, library exports and model files out of Git. Zotero desktop
is optional for RSS triage but required for library and PDF work, and the app never
installs Ollama for you. Saving settings is not verification: **Ops → System → Check
readiness** (or `zotero-summarizer doctor`) tests the setup, and local mode passes only
after both a working model answer and a dry run that writes nothing. Local-model checks
report **unknown** instead of guessing when memory or runtime safety evidence is missing;
free RAM or a model list is not a safety certificate. [Code](../zotero_summarizer/services/setup/) · [Tests](../tests/test_setup_doctor_runtime_identity.py) · [Browser reflow](../tests/test_setup_surface_browser.py).

### Task settings

Settings shows a saved summary and opens one task at a time: AI mode and connection,
Zotero paths, library access or language; **Today** owns **Article selection** and **RSS
sources**, **Weekly** owns **Telegram sources**, and **Ops → System** owns readiness and
calibration. Each task saves only its own fields (there is no Save-all), and leaving with
unsaved or pending changes asks first; leaving never cancels or rolls back work already
sent. An uncertain save reply leads to a reread, never an automatic retry, and saved Zotero
paths apply only after you restart the app. **Calibrate reviews** measures how much input
your model handles; it does not certify output length or provider availability, and an
unfinished or unknown run blocks another start. Article goals and the terminal-managed
Telegram interests are separate and not synchronized. [UI](../frontend/src/pages/Settings.jsx) · [Navigation controls](../frontend/src/pages/Settings.navigation.test.jsx).

### Model routing

Use **Settings → AI connection** to choose the default model, its fallback and any
per-task model (feed triage, backlog, deep review). Pick a saved connection or **Add
server**; **Connect** explicitly approves that exact address and authorization and fetches
its model list, then you choose a model (or type its ID), select tasks, review the
before/after impact and **Use** it, leaving other tasks and Telegram untouched. Keys come
from a named environment variable or the OS keyring and are never shown; servers without
authentication need none, a saved custom server needs current approval before metadata or
tests, and invalid existing configuration is never overwritten. **Test saved model** and
**Test selected tasks** send one tiny prompt to the default or each selected task route and
show the reply or a specific failure (HTTP status, timeout, empty answer, or **unknown**
when local runtime safety evidence is missing) without provider error text; they do not
prove structured output or real-paper processing and never retry, reroute or download a
model. An uncertain save reply
leads to a reread, never a replay, and **Cancel** does not undo already-saved connections
or keys. [Code](../zotero_summarizer/services/llm/) · [Tests](../tests/test_provider_routing.py).

### Review depth

**Settings → Review depth** picks **Thorough** (default: the model reasons before writing, quality checks run 3 times) or **Fast** (no reasoning, one quality check). On 4 test papers Fast cut the writing step by 50–75% and quality calls by half, but wording changed on 3, one failed verification and one grade changed. It applies to the next review; reviews made under the other depth show as outdated. [UI](../frontend/src/components/settings/ReviewDepthChoice.jsx).

### Offline models

Run `prefetch-models` while online to download the configured ML models once;
`prefetch-models --check` lists what is cached without downloading. Strict offline mode
refuses anything not cached. Local-first does not mean offline: hosted
models and public-source lookups send their inputs over the network and stay unavailable
offline. [Code](../zotero_summarizer/cli/_app.py) · [Tests](../tests/test_offline.py).

## Find papers

### RSS subscriptions

Open **Today → RSS sources** to add feeds or import your Zotero feed subscriptions; Today
needs at least one enabled source. Saving a source, refreshing its entries and triaging
papers are separate actions, and results tell "nothing new", "skipped while offline" and
"source failed" apart. A feed with only undeclared XML namespace prefixes still loads with a
visible warning; other malformed feeds are rejected. Practitioner sources work through the same
path: HackerNoon tag feeds (e.g. `https://hackernoon.com/tagged/ai-agents/feed`,
`https://hackernoon.com/tagged/agentic-ai/feed`) get a practitioner triage prompt by host.
[Code](../zotero_summarizer/services/triage/feeds/) · [Tests](../tests/test_rss_api_routes.py).

### Search

Search by topic and confirmed constraints to get deduplicated candidates and selected
reviews. Result links are real web or identifier addresses, never guessed from titles.
Adding a result to Zotero is always your explicit action; after an uncertain Add, recovery
reuses the same planned item instead of creating a duplicate. Provider diagnostics tell an
empty result apart from a failure but say nothing about relevance or complete coverage. [Code](../zotero_summarizer/services/search/) · [Tests](../tests/test_search_materialize_recovery.py).

### Related saved papers

Open **Find similar papers** on a paper's review page and choose **Show related saved papers** to find similar saved papers, including
ones you have read. Versions sharing an identifier count as one result (titles alone never
merge papers), and versions of the seed and papers you rejected are excluded. Optional
note assessment sends the full seed and candidate context, including your notes, to the
displayed review model, possibly hosted, only with explicit consent, and editing those
notes makes an earlier assessment stale; without consent, results are metadata-only. Citations show which
comments the model used, not proof that they support its claims, and ordinary Search views
never show this private context. [Code](../zotero_summarizer/services/library/) · [Tests](../tests/test_related_client_consent.py), [session privacy](../tests/test_related_session_projection.py).

### Saved-paper discovery

Open **Find similar papers** on a paper's review page (saved or feed-only) and use **Find new relevant** to get public OpenAlex candidates that are not
already in your library. Search providers receive only public metadata; sending your
private seed comments to the named model needs separate consent. Stale results are
rechecked, and discovery never adds a paper: Add stays your explicit action. [Code](../zotero_summarizer/services/search/) · [Tests](../tests/test_discovery_server.py).

## Choose and read

### Daily triage

**Today** ranks RSS papers against your research goals and lets you **Add** or **Trash**
them; start and watch backlog triage there, while **Ops** monitors or cancels review jobs
started from Library. Add requires a current full-text review; Trash does not. The optional
background daemon runs the same pipeline but never adds a paper to Zotero. Abstract-only
triage never recommends a full read; that decision comes from the full-text review.
Today also records, locally only (`data/today-exposure/<day>.jsonl`; `ZS_TODAY_EXPOSURE_LOG=0`
turns it off), which papers it showed, how long each stayed on screen and which links
you opened, and adds up to two clearly marked "Random check" papers per day from just
below its bar (`ZS_TODAY_EXPLORATION_SLOTS`) so the ranking can later be checked against
unbiased data. A review that is outdated but still of the same PDF stays on Today with an *Outdated review · Regenerate* line (a different or unresolved PDF is left out). Below the cards, **Not reviewed yet (N)** lists the best-ranked papers that have no usable review (same ranking, no verdict buttons); opening one starts its full review on the paper page. [Code](../zotero_summarizer/services/triage/) · [Tests](../tests/test_review_eligibility.py) · [Source-mode boundary](../tests/test_abstract_reading_boundary.py).

### Today cards

Each Today paper has a **decision card**: the deterministic facts (full text, own code +
liveness, what's published, who's behind it) plus, with your consent, grounded AI fields —
an English TL;DR of at most four terse bullets — `New` (what is actually new), `Take` (a method,
protocol or prompt you could reuse), `Caveat` (overclaim / missing baseline / weak eval) and `Code`
(own repo or not); quotes are checked against the paper and a bullet is dropped rather than padded. Without
consent (or with AI off / offline) the card is facts-only and says why. Cards use a
separate `card` model (operational, never affecting a review) and are generated ahead of
time for Today's top papers, so opening one is instant; generation respects a per-paper
call cap, a daily token cap and a circuit breaker. [Code](../zotero_summarizer/services/card/).

### Reading reviews

Open a paper in **Library** to get a digest, a reading plan beside the action, and
figures; when the app downgrades **Read** to **Skim**, it shows why. Claims are checked
against the original source, not the generated summary, which is still not scientific
validation. On a remote model every judging step (digest, claim check, rubric, self-check,
section one-liners) reads the whole paper once cleaned: reference lists, acknowledgements,
funding/licence boilerplate and running headers are dropped, appendices stay. Only a paper
beyond the model's context window is read in parts, with a visible "Long paper" note. Stale, incomplete or unverified reviews and figures are withheld or marked
until rebuilt, a failed new attempt keeps the last valid review, and saved data is never
silently rewritten. If a job's status is unknown, use the read-only retry; it never starts
a duplicate review. Web articles are reviewed from extracted text, not a visual copy of the
page. [Code](../zotero_summarizer/services/library/) · [Tests](../tests/test_review_source_boundary.py).

### Paper questions and notes

Ask a question about a paper, even before it has a PDF or review, to get an answer quoted
from its source, or an abstention when the source does not support one; answer modes that
read the raw PDF need the PDF. Questions and manual notes are saved separately from
answering, and a failed save keeps your draft with a **Retry** button instead of resending
automatically. Leaving a paper with an unsaved note asks first: cancelling keeps the draft,
and leaving never saves it silently. A note saved in the app is not yet confirmed in Zotero. [Code](../zotero_summarizer/services/library/review_questions.py) · [Tests](../tests/test_review_questions.py) · [UI](../frontend/src/pages/PaperReviewPage.jsx) · [Navigation tests](../frontend/src/pages/PaperReviewPage.test.jsx).

## Save results

### Zotero changes

Every write to Zotero (an explicit **Add**, a metadata edit, an approved **Pending**
change, or a verdict and rationale mirrored as a note) backs up the Zotero database first.
Pending changes wait for your **Apply** or **Reject**, and a stale selection is refused as
a whole; mirrored verdicts never count as approving Pending changes. A generated review
note enters Pending only after its review is published, and staging it is not "saved to
Zotero". When a write's outcome is uncertain, the app shows it as unconfirmed and never
replays it automatically; because the app and Zotero keep separate databases, a partial
write may need repair in Zotero. [Code](../zotero_summarizer/services/zotero/) · [Tests](../tests/test_pending_confirmation.py), [rejection](../tests/test_pending_rejection_atomic.py).

### RSS read-state backups

Marking feed items read in Zotero backs up the Zotero database first and prunes old
backups (keeping five) only after a successful write. When the backup limit blocks a
sync, stop automatic syncs, export and inspect the snapshots, then make room before
retrying. A cleanup failure after the write does not undo it. [Code](../zotero_summarizer/integrations/) · [Tests](../tests/test_raw_feed_backup.py).

## Learn from decisions

### Labels and learning

Assign, change or retract reading verdicts to supply labels; the app keeps their history.
Retraining turns the labels into the relevance gate, the model that filters feed papers.
Label refresh and retraining are blocked while the classifier configuration is unknown or
failed, rather than guessing a model. Feedback describes the papers you reviewed, not
unbiased classifier recall. [Code](../zotero_summarizer/services/golden/) · [Tests](../tests/test_verdict_training.py).

### Model history and restore

Use `goldenset model-history` to list model snapshots and `goldenset restore-model` to
restore one; the current model is kept. Restart the daemon to load the restored model;
restoring does not show that predictions improve. [Code](../zotero_summarizer/services/model/classifier_backup.py) · [Tests](../tests/test_model_backup_rollback.py).

## Get digests

### Weekly digests

Open **Weekly** to browse and download saved research-feed reports, with
project-oriented engineering cards, as JSON or Markdown exactly as saved. Browsing never
generates anything; generating a report (`research-feed run`) and queueing optional Zotero
changes are separate explicit actions. Reviews still awaiting qualification stay visible
with their reasons and links that do not start a review, and only an observed download
failure lists a paper for manual full text. Matches to your project goals appear as
unverified notes, not confirmed uses or new ideas. Exports include source hashes but not
your private file paths or focus text, and malformed saved data is refused or shown as
unavailable, never repaired or regenerated. [Code](../zotero_summarizer/services/research_feed/) · [Tests](../tests/test_research_feed_web.py) · [History admission](../tests/test_research_feed_ranking.py).

### Telegram news

In **Weekly → Telegram sources**, choose public channels, groups or forum topics, a local
model and work limits and allow reading them once (a remembered, revocable checkbox). **Weekly** (linked from Today) shows
**Сегодня в каналах** with the digest age and one **Проверить каналы** button: it runs in the background with staged progress and Russian errors, and new posts appear when it finishes. **Тренды недели** come first: the top 5 of up to 15 topics (**Show all N trends**) the channels discuss now, each with a short label, a one-line gist, notable ideas/tools, a new/growing marker against the previous week and the channels (links to posts). Below, compact cards: channel, age, headline, a few discussion lines (one per picked comment, plus a «Спор:» line for the main disagreement) where each line expands to the exact comment(s) it cites; lines are checked by a local NLI model (`data/models/nli-mdeberta`), **Важное (n)** quotes, **All comments (n)**, **Подробнее**, a «Continues …» line for series posts and «Also in: topic». Authorize
your Telegram account once in a terminal with `research-feed telegram-auth` (shown as one
copyable command on the page; optional `telegram` extra); the app never joins
chats, sends messages or reads your personal inbox, and saving or viewing never collects or
generates. Each run reads a bounded newest window, so reviews are concise syntheses, not
complete histories; coverage, truncation and errors are saved with the report, and earlier
threads are kept when a later post fails. One pipeline now fetches incremental posts, drops
forward and near-duplicate reposts, keeps the posts closest to your goals (top per channel
above a threshold, always a minimum), and fetches comments only for those kept posts that
have replies; a Telegram rate-limit keeps the posts without comments and never drops the
channel. The kept posts get a 2–3 line discussion summary that cites comment ids, or the top
comments when no model is selected. Processing uses only the selected local model,
never a cloud fallback or silent clipping, but a model name does not prove the server's
limits or your hardware are safe, and linked papers are cited, not verified. Collection runs
only from the UI; the one-time `telegram-auth` command is the sole terminal step. Up to ~30
channels are allowed. Every run is also saved: **Past digests** lists them by date (with a lead topic, discussions · unread) and opens one with the same cards; unread come first, **Done** collapses a discussion to one line behind «n read» (**Mark all read** per digest, with Undo), and under **Details** one **Your feedback** group (Useful / New to me / Want to try / Good discussion, Not a fit, a note) is stored and downloadable via **Export feedback**. The digest is persisted under `data/research_feed/telegram/` and read
back (posts, summaries, weekly trends) at `GET /telegram/today`; saving sources without a
model never fails (no-summary mode). Summaries and trend labels use a local model by default; a non-local provider is used only after you explicitly allow sending channel text to it (the page shows the provider/model). **Не подошло** with optional reason chips on a post is stored in the database. The retired CLI saved-news path (`research-feed run
--source telegram`) was removed and its interest ranking folded into this gate. The live
collection path is verified with a fake client until Telegram is authorized. [Code](../zotero_summarizer/services/research_feed/telegram_workflow.py) · [Tests](../tests/test_telegram_sources_web.py) · [CLI](../zotero_summarizer/cli/_telegram.py).

### Feedback analysis

Run `goldenset analyze-feedback` to get local descriptive reports on your labels,
predictions and coverage. Weekly scheduling is opt-in; a scheduled run that clearly never
started may retry, while an uncertain one is held. Reports never change labels or install a
model, and they do not prove that anything caused an improvement. [Code](../zotero_summarizer/services/golden/feedback_analysis/) · [Tests](../tests/test_feedback_analysis_operation.py).

## Work across devices and clients

### Offline review

Sync a working set, then read saved context and edit verdicts or notes while
disconnected. Edits stay queued on the device until the server acknowledges them; when
versions conflict, compare both before choosing. If the server is unreachable, cached
details still show even when the browser claims to be online, while a server refusal is
still an error. PDFs and new AI work need the server, and browser storage can be cleared. [Code](../zotero_summarizer/services/sync/) · [Tests](../tests/test_offline_sync.py).

### Remote access

Run `make iphone` to use the app from your iPhone or another device signed in to your
tailnet (your private Tailscale network). It reads this Mac's tailnet name and your
Tailscale login, maps Tailscale Serve HTTPS (port 443) to the app on 127.0.0.1:8000 while
it runs, and starts `serve-mobile`, which admits only requests that Tailscale marks with
your login on the exact `https://<mac>.<tailnet>.ts.net` address. Ctrl+C stops the app and
removes the mapping. Ordinary `make run`/`serve` stays loopback-only and refuses the
tailnet address. The loopback port trusts other processes on this Mac, as `make run` does;
`serve-mobile`'s default private Unix socket suits Tailscale builds that can reach sockets. There is no LAN binding or public Funnel, and revoking access cannot erase data
already cached on a device. [Code](../zotero_summarizer/cli/_mobile_serve.py) · [Launcher](../tools/serve-iphone.sh) · [Tests](../tests/test_gateway_real_transport.py).

### MCP tools

Connect an MCP (Model Context Protocol) client, such as an AI assistant, to the app's API
to use its operations outside the browser. It follows the app's consent and confirmation
rules: paper content it reads never counts as permission to Add, approve or change your
library. Similar-paper search needs a nonblank query or seed title and real semantic
matching; it never presents the reading queue as recommendations. [Code](../zotero_summarizer/mcp/) · [Tests](../tests/test_mcp_search_contract.py).

## Experimental capabilities

### Search question evidence

Opt in for one Search to have original sources checked for quoted answers to your
question before reviews are chosen; a failed request keeps the opt-in for retry. Well
supported answers may reorder recommendations, while partial or unchecked evidence is not
a "no". Results link the exact source used (PubMed Central XML or open-access PDF), and
stale evidence never stays active. [Code](../zotero_summarizer/services/search/_question_evidence.py) · [Tests](../tests/test_search_question_policy.py).

### Support comparison

`python -m tools.method_validation support --config FILE` previews the configured
comparison arms and their effective limits without calling models or writing files;
adding `--run` consents to private writes and the configured model calls. The comparison
replays decisions over frozen inputs, and models never see the ground truth. A replay
counts only if it matches its original recording; matching output hashes alone prove
nothing. Time limits stop local waiting but cannot guarantee that a remote provider stops
working. [Code](../tools/method_validation/) · [Tests](../tests/test_recorded_support_binding.py).

### Priority comparison

Explicitly run `python -m tools.method_validation priority --config FILE --run` to
compare the app's priorities with matched ML and LLM results without changing production
ranking. Inputs dated after the frozen split are refused. Missing scores or provenance stay
"unavailable" rather than invented or dropped, and failed runs produce no readout. Replays
check consistency but do not authenticate server measurements. [Code](../tools/method_validation/PRIORITY-INTERFACES.md) · [Tests](../tests/test_priority_comparison.py).

### Collection comparison

Explicitly run `python -m tools.method_validation collections --config FILE --run` to
see which existing collections a model would choose, within a frozen copy of your
collection tree; it never proposes new names or changes Zotero membership. Missing corpus
or reference support stays unknown, and "no match" requires every eligible question to be
checked. Declared version links keep held-out papers out of audits, but they are unsigned
and not proof of ancestry. [Code](../tools/method_validation/COLLECTION-INTERFACES.md) · [Tests](../tests/test_collection_comparison.py).

### Interpretation experiment

Opt into digest and Q&A variants over frozen, human-approved sources; capturing sensitive
data and inspecting it are separate actions. An optional note card shows the same
Finding/Scope/Unproven summary as Library, but experimental data never enters ordinary
reviews or exports and never changes grounding or write permissions. Failed runs record
each assigned item as failed rather than inventing answers. Adopting or rejecting a variant
requires independent human scores; passing tests or execution errors alone cannot decide
it. [Code](../zotero_summarizer/cli/_interpretation.py) · [Tests](../tests/test_interpretation_experiment_review_fleet.py).
