# Zotero Summarizer frontend

React 18 + Vite 5 + Tailwind 3, served by FastAPI at the app root.
Public tests use generic provider/model fixtures; private deployment identities
and live-library screenshots do not belong in the shared source.

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api/*` to `http://localhost:8000`; `npm run build` writes
`dist/` for FastAPI. Local `node_modules` directories and dependency symlinks
are both gitignored; shared worktree dependencies must never enter a release diff.

The development API proxy leaves `changeOrigin` unset, preserving the browser's
Host and Origin while connecting to the configured `VITE_API_TARGET`. This does
not relax the backend's loopback peer or exact-origin requirements. The additive
`tests/test_vite_proxy_contract.py` contract loads this config and executes the
installed Vite header/target helpers without opening sockets, then checks the
production boundary for default and overridden local targets. It follows Vite's
installed package metadata `exports["."].import.default` ESM entry, never the
CommonJS entry, and binds metadata/source observations. Cross-origin refusal,
unchanged Origin and handler reachability remain required.

This cross-language check is opt-in: set `ZS_VITE_PROXY_CONTRACT_SMOKE=1` and
`ZS_VITE_PROXY_TEST_NODE` to an absolute executable Node path, with frontend
dependencies already installed. Ordinary backend runs skip it without needing
Node or frontend dependencies. An admitted contract run must execute all four
cases, with zero skips and successful source observers; prerequisite/observer
failures are not product RED. No dependency installation or CI change is required.

The source-bound regression actually observed two same-origin writes rejected
with 403 after Host rewriting, plus two passing cross-origin refusals, before
removing `changeOrigin`. The config subtraction is implemented; separate
four-case GREEN and backend compatibility validation remain pending. This is
synthetic header-helper/ASGI evidence, not live proxy, browser, TLS or mobile-device
proof. Serving the built frontend from FastAPI does not use this development proxy.

## Product surfaces

- **Library** — Read next, labels, reviews, figures, paper Q&A, and acquisition.
- **Search** — query-driven discovery and review.
- **Today** — feed triage and Zotero filing. Author h-index badges render only for positive OpenAlex evidence; unresolved author metadata is not shown as `h=0`.
- **Settings** — AI, research profile, Zotero, sources, and advanced controls.
- **Ops** — feeds, jobs, pending writes, diagnostics, and retraining.

Legacy `/annotate`, `/review`, `/triage`, and `/pending` links redirect without
dropping query parameters. `/review?state=gate_rejected` reaches the Feed Review
tab in Ops with its pile filter intact.

Library and Today share `todayHelpers.fulltextMessage` for unavailable-PDF
accounting from per-item outcomes. The full-text API no longer exposes partial
per-reason counters; deploy frontend and backend together. Library reports a
missing/failed result explicitly. A failed status request stops local polling
and reports that the server job may still be running; it never implies success
or silently retries forever.

Ops' Triage Monitor lists every `active_items` entry from the job API, rather
than displaying the last completed paper as current. Polling continues through
`cancelling` until workers drain; only the terminal transition refreshes
calibration. The singular current-item fields are removed from the shared
API/MCP/UI contract, so server and client must be updated together.

The monitor's **Triage feedback** panel reports observed agreement/recall and
false-negative counts against saved at-decision triage predictions. It shows
matched-prediction / feedback coverage and explicitly limits the claim to
reviewed items; the former “Gate recall” / counterfactual-audit labels are removed.
Unknown ratios render `n/a`, distinct from measured `0%`. This does not turn
ordinary approve/reject feedback into an unbiased ML-gate audit.
The monitor restores each selected job's saved approve/reject decisions from the
feedback endpoint, so reviewed cards remain marked after reload.

Settings' Current model card describes the loaded gate, not the newest artifact
or evaluation run. Its API exposes only the four displayed fields; no gate loaded
is an explicit empty state even if trained artifacts exist on disk.
Settings deep links to `#university-access` open the matching disclosure, and
saving an AI-mode change round-trips unsurfaced config fields unchanged.

Search coverage hits reuse their existing Zotero key: the card says In library
and the server command cannot create a duplicate even if called directly.
Search keys cards and Add requests by the server's persisted candidate ID, never
by title. The query panel consumes `plan.display` so tight/broad variants and
OpenReview are not omitted. Backend and frontend must be deployed together.
Today/Search's shared collection picker consumes the API's `items` tree, including
nested collections; it does not mistake the response envelope for the tree.

## Setup and AI

`/setup` is a skippable Mode (Full local / Hosted / No LLM) → Zotero → AI
profile (skipped for No LLM) → research wizard. Local mode shows no API-key
control; hardware-incompatible profiles cannot be selected. Saving unlocks
Today only after explicit Doctor verification passes; saving alone never
claims the configured pipeline works. The wizard and Settings
share the compact server-side Doctor checklist. The gate uses backend
`configured`; stricter operational `ready` remains diagnostic.

Typed AI recovery opens the existing disclosure in Settings; wizard recovery
navigates to `/settings#ai-models`. Neither verifies, saves, selects a model or
grants consent. Other typed rows label scoped Retry separately from recovery
guidance. UNKNOWN/HOLD cannot publish Ready.

The shared AI editor supports hosted presets, Local, and Custom. Secrets go to
the OS credential store; the UI receives only presence/source metadata. Local
profiles are hardware-gated and show an explicit pull command—the app never
starts a download. Advanced routing and unsurfaced config round-trip through
`utils/configForm.js`. Provider/model saves hot-swap; path changes need restart.
ML-only mode is a first-class classifier/search-only setup state: AI reviews,
Ask Paper and new feed Adds to Zotero remain unavailable without a prior
full-text review; manual Zotero imports and immediate Trash still work. Today
links directly to Settings to enable AI instead of offering an impossible
Generate review action. Settings can re-enable AI later.
Wizard progress exposes the current step and completion state in text, and each
step change is announced politely to assistive technology.
Optional browser preferences use guarded storage access, so privacy settings that
block Web Storage do not prevent setup, Today, Library, or Search views from rendering.
Search session pointers/drafts are best-effort in `sessionStorage`; blocked storage
simply disables reload restoration for that tab.

Offline sync pulls use the complete paper snapshot and revision cursor; the
protocol's legacy `changes` field is empty because the PWA does not consume history.

## Paper review

Library keeps Rescore available after a queue-read error, including when the
display retains a previously scored list. Rescore can rebuild a corrupt score
cache; the existing status poll reloads the new snapshot after completion.
The progress notice promises a complete result, not streaming partial scores.

Library band filters and the automatic review work list share one effective-band
calculation, matching the server's demote-one-band prestige policy. Only known
citation prestige below the supplied floor demotes top bands; missing evidence
does not. The displayed count uses the current queue's floor, and each fleet
round uses its freshly fetched snapshot's floor. One click drains every undecided
cool item in the fetched queue (up to the server's 5,000-row queue limit), in
five-item fleet batches. A cross-language matrix checks
the client filters/work list against the server rule without new API fields.

Provenance shows the explicit `label:*` source when it determines the exported
priority, replacing the inapplicable additive table. A separately edited final
priority remains visible as a manual override.

Library rows share `PaperDetailView`; `/paper/:key` adds located findings,
Paper map, figures, actions, and grounded Q&A. Independent Idea/Evidence/Writing
signals cap recommendations while provenance retains raw output. HTML briefs and
figures live beside source PDFs for Zotero compatibility.

Compact cards and the full paper page share the explicit verdict editor. Picking
a priority changes a draft; Save/Update is the write boundary, and Cancel restores
the saved priority and comment. The mobile bar jumps to this same editor instead
of issuing a second, comment-erasing write. Clearing a comment remains an explicit edit.

The paper reader's stale notice covers changes to the PDF source, parser setting
or renderer; the existing one-shot rebuild uses the backend's extraction identity.
Blocking or unverified audits arrive as errors: the reader shows the failure and
rebuild action without exposing old figures or artifact links. Build notices
describe audited HTML/figures, not Markdown notes (which are no longer generated).

Missing-full-text recovery is one shared notice on every review surface. A missing
optional browser package links to Settings; an attempted authenticated fetch links
to the University access control that opens the app's persistent browser profile.
Opening the publisher in an unrelated default profile is never presented as recovery.

Fleet suggestions are proposals only: Confirm/Override writes them, and
low-confidence or flagged proposals offer Override only.
The fleet API wrapper preserves an explicit `itemKeys: []` as an empty selection;
only omitted/null keys request automatic top-K selection. The server deduplicates
explicit keys and treats an empty list as a no-op without changing job status.
Native paper review consumes the server's policy-projected cached decision. An
absent decision renders REVIEW with its reason; it no longer synthesizes SKIP or
DEEP-READ from goal counts and quality bands. Confirmed goal counts exclude hits
whose summary was withheld. The unused alternative verdict helpers are removed.

Feed Review bulk confirmation sends only the visible, not-yet-actioned row IDs,
shows the saved-verdict count and removes acknowledged rows from the queue.
The active pile is encoded in `?state=`; switching piles updates the URL and
same-mounted query-string navigation updates the view.
Individual Review actions acknowledge the saved decision without claiming a CSV
append or pending-change enqueue; the server stores training metadata atomically
with the verdict and returns only the processed ID and state.
Pending treats a partially failed HTTP-200 batch as an error and points to the Failed tab.
Today, Library and Pending derive batch counts and write IDs from the same current
filtered selection. Hidden selections cannot enter Add/Trash/Triage/Apply/Reject;
an empty filtered selection is a no-op. Library/Pending also block retained rows
during loading or a failed read. A started batch keeps its captured IDs through retries.
Shared list shortcuts operate outside interactive controls only. Native inputs
(including checkboxes), selectors, links/buttons, rich-text editors and ARIA
widgets keep their own keys; composition and already-handled events are ignored.
Pending's collection selector cannot issue Apply/Reject, while the explicit batch
buttons and shortcuts on the non-interactive list surface remain available.

Today now reports top-level full-text attachment failures as unavailable
(in addition to typed per-paper outcomes), rather than silently omitting them.
Spot-check distinguishes a saved label with pending Zotero sync or failed
mark-read from a successful Zotero write and leaves its warning visible after
the last card is dismissed.
Today shows `review_ready` only after a current, non-empty deep review. Add is
disabled when every selected paper needs review; mixed batches surface per-item
`review_required` errors without claiming blocked papers were added. A full-review
link provides the existing Generate review flow; Trash is always available.
Feed Review similarly disables only positive relabels on unreviewed rows,
leaving `dont_read` available. Switching hosted/local resets per-stage routing;
Finish checks every resolved stage endpoint (not just the default provider).
The existing-local-endpoint profile can use a custom loopback URL without
adding an API-key field. A hosted provider sharing a local model name does not
automatically select a local profile. Doctor success is re-fetched after Save;
credential replacement immediately clears cached Ready in Settings, even if a
previous verification mutation had completed. Missing Zotero/RSS prerequisites
link to Settings.
Relevance conclusions in both interactive Review and the HTML brief are
case/punctuation deduplicated across goals, capped on first view and disclosed
on demand; goal labels, evidence quotes and accessible score meters remain.
Located evidence is not automatically a positive match: a `hit` with
`relevant=false` now reads as not supported in both React and the HTML brief.
Header/badge/tile counts share the supported-goal rule, including legacy rows
without an explicit abstention flag. When a page has no TOC, its desktop review
uses the freed column instead of squeezing goal cards into the TOC width.

The full paper reader puts contribution, material caveats/coverage, findings,
methods and limitations before the folded assessment/reference tail. Findings and
reading rationale have one displayed home; original goal summaries, evidence and
all secondary digest fields remain available. The page header does not repeat review
grades. Full-page prose uses 16px text and a 66ch measure; sidebars yield below 1280px.
Semantic headings and native keyboard disclosures are reused. Compact cards remain
folded; explicit abstention is separate from unsupported and not-retrieved evidence.
Synthetic browser checks exercise 390/1024/1440px, 320px reflow, text resizing,
spacing and computed contrast. They are not native-user comprehension acceptance.

## Offline boundary

The installable local-first PWA caches only its shell. IndexedDB holds compact
snapshots, a sync cursor, and a UUID mutation outbox. Verdicts/manual notes queue
on network failure; conflicts require Keep mine / Use server. Review questions
always commit their UUID, immutable create payload, local row and outbox together
before network work. PDFs, AI answers, annotation, acquisition, and rescoring
remain server-only.

After reconnect, the server gives queued verdicts and notes the same training,
feed-materialization, and Zotero-mirror effects as online saves. Once a snapshot
is reconciled, open paper details refetch: a rejected optimistic verdict no
longer appears as a saved priority, and the transient green device-save notice
clears when that verdict is acknowledged or rejected. Rejected drafts and
conflicts break long stable paper keys inside the mobile header instead of
expanding the page horizontally. The primary tabs wrap below narrow mobile
widths instead of pushing the viewport beyond its edge. An offline or unchecked review model cannot
activate Generate or auto-review; a status transport error is not called a failed
review job. This is a
same-machine loopback PWA boundary; remote mobile needs a future authenticated
HTTPS deployment rather than exposing `/api/sync` directly.

Sequence allocation, mutation insert, optimistic paper state, and cached-detail
update share one IndexedDB transaction; each full pull replaces the compact
paper snapshot while retaining local papers with unresolved mutations, and
refreshes cached review details. A 15-second sync deadline releases a stuck focus sync,
and protocol incompatibility explicitly asks for an app refresh. The IndexedDB
test kills/reimports the client module and proves cached context
plus ordered concurrent mutations survive while the server is absent.

Ask Paper first confirms the comment-only `/api/library/questions` save, then
requests an answer with the same submission UUID and bounded session history.
Local-save or server-comment failure stops QA; offline submissions remain on the
device. Retry answer reuses the frozen question/origin/preset/mode/history;
a deliberate repeat gets a fresh UUID. Reconnect/restart replays only
`review_question/create` through sync, never automatically reruns QA. A comment
response is not a sync receipt: the outbox retires only on validated sync results.
Questions are bounded to 2,000 Unicode code points; valid original paper keys are
never trimmed or rewritten when aliases/canonical snapshots arrive.

The existing My notes surface in shared Today/Library details and the full-page
rail lists saved questions read-only, separate from editable `user_note`.
Question/detail refresh uses the existing review-detail query callbacks. Both
cache-aware detail queries (shared `usePaperReview` and `AnnotationVerdict`) use
`networkMode: 'always'`: the existing network-to-IndexedDB reader must run during
offline invalidation and cold mounts, not pause before reading durable local data.
Server-only queries and retry defaults are unchanged. Dirty
manual drafts survive both question-array and concurrent manual-note refreshes;
questions never enter manual Save payloads. Online detail merge/cache and sync
pull preserve unresolved local questions in existing IndexedDB transactions,
including incompatible same-ID server rows and rejected drafts. Local comment,
server confirmation, QA failure/abstention and Zotero delivery are separate
states. Ask reports the supplied composite-note receipt; My notes shows one
revision-bound delivery status, not a receipt per question. Transient receipts
are bound to the saved manual body and immutable question payloads; new
submissions, offline changes, changed details and navigation retire previous
completion callbacks, even if the old content returns. Each save captures its
expected payload before HTTP; that expected snapshot may arrive before its receipt.
A confirmed current-projection no-op is acknowledged without claiming another
external write. Empty/no-intent and absent/malformed receipts stay unknown;
no answer/execution ledger or guessed
mirror success is added.
Existing Ask/PDF visibility gates remain unchanged, but the submission save itself
needs neither a PDF, Add nor a successful review. Deploy with the matching backend
question/detail/sync contract; actual Zotero delivery and browser smoke are
integration checks. The backend compacts older answer evidence to verified
extraction handles, and the UI labels verified quotes without inventing pages.

Valid online review details remain readable when IndexedDB open/read/cache writes
fail. The frontend returns `offline_cache_error` with `stage`, `message` and
`local_questions_available`, and logs storage-error metadata without note/question
content. Available local question unions survive cache-write failures; a denied
readwrite transaction may use a readonly overlay. These diagnostics are never
cached or treated as save/delivery acknowledgements. Malformed server JSON or
question arrays/rows remain protocol errors, not storage fallbacks. Local submission commits
still must succeed before comment POST/QA; HTTP rejections and original network
errors remain distinct even when the offline read cache is unavailable.

Paper-review HTTP fixtures include explicit `questions: []` when no questions
exist. This test-contract alignment does not relax live server validation or
migrate old cached detail objects; the existing offline cached-response fallback
remains unchanged.

Sync sends ordered batches of at most 100 with acknowledged predecessor UUIDs.
Receipt storage and outbox retirement share an IndexedDB transaction; restart
or timeout preserves the unsent tail and its causal base without rewriting UUID
bodies. Input bounds are checked before optimistic saves. Invalid legacy drafts
are retained as rejected, with copyable text and paper links in the navigation
status, while valid rows continue. Unknown acknowledgement IDs/status/revisions
cannot retire pending rows; HTTP rejections are distinct from connection failure.
The 15-second deadline still bounds one sync attempt, not all future attempts.

Targeted Search polls one session at a time; transient poll errors keep the last
session visible and retry, while terminal status and unmount stop further polls.
Verdict saves remain successful when the separate best-effort reject-tag request
fails; the page refreshes the saved verdict and displays a warning for the tag.
Deploy this build with the matching backend; earlier protocol-v1 servers do not
understand predecessor receipts.

## Structure and conventions

Both paper-review surfaces reuse `useDeepReviewRunner` and a per-item React Query
poll. Status requests time out after 15 seconds, start requests after 30 seconds;
poll errors end the spinner and expose retry, without cancelling the server job.
Late start/status responses cannot replace another paper's state after navigation.
Auto-run waits for the initial status and never restarts a resumed completed job.

Targeted Search offers Recommended, Relevance, and Relevance + prestige orders,
persisted with the tab's session. The optional blend uses 85% query relevance and
15% log-normalized citation count, with median-known citations for missing values;
when citations are absent/zero throughout, it reduces to relevance. Counts are a
limited prestige proxy (field/age bias), disclosed beside the selector. Explicit
sorts show the whole pool in that order; only Recommended collapses weak matches.
Retracted and unscored candidates stay last. This display preference does not
change server review selection. RSS refresh messages include partial failures.

```text
src/
  api/          thin fetch wrappers
  components/   shared UI and workflows
  hooks/        server state
  pages/        route orchestration
  utils/        config and presentation helpers
  offlineStore.js / syncClient.js
```

Use React Query for server state and component state for transient UI. Reuse
`Button`, form primitives, and `CHIP_TONE`; avoid ad-hoc status colors. Keep API
tests beside wrappers and run `npm run build` for every UI change. Page tests
cover route/query compatibility, setup validation and completion, Today’s
source-filtered batch selection, Library mode deep links, settings save errors,
Ops tab state, paper-review writes, and Search materialization; hook/component
tests cover keyboard isolation and verdict comment editing.

The optional built-app check runs real Chromium → FastAPI → temporary SQLite:
`ZS_APPLICATION_BROWSER_SMOKE=1 .venv/bin/pytest -q --forked tests/test_application_browser_live.py`
from the repository root after building. It uses the installed browser, blocks
service workers/external requests, and disables AI, training and background jobs.
No live Zotero/profile is used; the missing integration's collections/tags 503s
are asserted explicitly. It covers first-save/reload, mobile verdict editing,
setup/route navigation, keyboard isolation and filtered pending persistence—not
model quality, actual Zotero delivery or full offline service-worker behavior.

The separate `tests/test_review_question_browser.py` opt-in requires
`ZS_REVIEW_QUESTION_BROWSER_SMOKE=1`, `ZS_REVIEW_QUESTION_BROWSER_SYNTHETIC=1`,
and a parent-reserved `ZS_REVIEW_QUESTION_BROWSER_SLOT=reserved` after a guarded
build. It serves the actual app on an owned ephemeral loopback port, uses an
isolated persistent installed-Chrome profile with service workers, and exercises
Today/Library questions through real API/sync/SQLite and synthetic native Zotero
notes. Browser network offline, full context/API restart, unchanged UUID replay,
manual-note separation and no automatic QA replay are asserted. The connector
probe alone is suppressed to avoid probing a user's Zotero port. IndexedDB write
denial is a separate injected error, not the offline proof. Main-world CDP probes
stay attached until context close: detaching resets `navigator.onLine` in the
installed browser despite continued transport failure. Receipts/screenshots and
cleanup remain in `data/issue-validation/question-browser/`. This synthetic check
certifies neither real AI usefulness, live Zotero, mobile nor human acceptance.

The My notes Save action retains its intrinsic width beside save feedback, while
feedback remains visible and wraps. This local layout boundary does not change
manual Save payloads, dirty drafts, saved questions, or Zotero-delivery semantics.
The opt-in `tests/test_review_notes_layout_browser.py` reuses the reviewed question
browser fixture and its existing opt-ins, installed Chrome, viewport and cleanup.
It checks actual text-node/glyph Range line geometry and retains computed styles,
fixture observations and screenshots under `data/issue-validation/question-browser/`.
Per-run source and built-asset binding remains with the parent guard/capture;
geometry observation errors or skips are not wrapping RED. This synthetic layout
check is not live-Zotero, mobile, model-quality or human acceptance.

The Search journey also verifies the full query projection and independent Adds
for two identical-title cards through real HTTP/session persistence and reload;
only the external Zotero writer is substituted.

Deep-review failures on both paper review surfaces share `ReviewFailure`: show
the server's typed recovery and disclose code/stage/original error under Technical
details. Legacy string-only failures still render normally. The keyed runner
passes diagnostics through without interpreting model prose as HTTP/login state.
Sensitive attempt capture is CLI-only; no capture switch or new dashboard appears.

Targeted Search folds confirmed exact-phrase/type constraints into one optional
panel. Topic-derived restrictions appear as confirmation-required plan rows;
confirm by entering the desired filters and searching again. The existing plan
panel also shows authority, observed retrieval accounting and coverage limits.

Targeted Search's folded explicit constraints are forwarded through `searchApi`
only when supplied; the natural-language topic alone never submits confirmed
filters. The existing plan panel shows confirmation proposals and unknown
zero-result retrieval states.

The flat full review leads with Contribution before recommendation/grade/rationale;
compact and legacy assessment presentation is unchanged. Exact key-finding strings
also present as overstated claims appear only in the explicitly qualified critique
panel; near-paraphrases remain untouched. Goal reasons live inside the native
Evidence disclosure, while states, citations and quotes remain available. The
mobile verdict-editor link stays in document flow rather than covering prose.

Non-paper findings stay visible when the scientific overstated-claims panel is
suppressed; exact-repeat exclusion applies only when that replacement renders.

Full-page caveats factor only exact contiguous shared introductions in adjacent
warnings. Complete suffixes remain initially visible with their own source links;
joining each prefix and suffix reconstructs the original warning. Internal words,
negations and scope are never deleted or inferred equivalent from a criterion or
approximate location. Original wording is retained for audit. Identical goal sets
share one finding heading. No semantic classifier, rewritten summary, rubric or
scoring change is introduced; compact presentation remains unchanged.

Full reading views fold goal findings into one native disclosure; states and citations remain visible. Saved quality/digest source basis is shown literally, with reviewed extent explicitly unrecorded. Rubric-literal warning frames preserve each modifier, complete suffix, and original wording.
