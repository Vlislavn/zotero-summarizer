# Zotero Summarizer frontend

React 18 + Vite 5 + Tailwind 3, served by FastAPI at the app root.
The shared Today/Library setup nudge retains one Finish setup action and describes
AI or ML-only operation, not an API key as a universal prerequisite.
Public tests use generic provider/model fixtures; private deployment identities
and live-library screenshots do not belong in the shared source.
The installed React data router owns navigation admission without changing App's
routes/layout. One paper-note blocker covers links, programmatic transitions and
history; native unload uses the browser confirmation. Cancelling preserves the
draft, accepting discards it explicitly; clean/saved notes and same-paper anchors
do not prompt. The previous callback-only Prev/Next guard is removed. Regressions
exercise actual transitions, not a mocked blocker.
Only the five data-router fixture modules opt into matching AbortController and
AbortSignal.timeout/any with Node's native Request/fetch; other tests retain their
original jsdom environment. Production browser globals remain unchanged. Search
owner-retirement assertions retain per-request abort-state diagnostics.
Discovery status rechecks hide prior private evidence; ordinary session restoration
does not block a new explicit search. Both use the same request-owner fence.
Ordinary wire controls retain public absence of empty Discovery through actual
screen/URL/storage adoption, polling failure and unchanged no-Discovery UI.

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api/*` to `http://localhost:8000`; `npm run build` writes
`dist/` for FastAPI. The dev proxy preserves browser Host/Origin for the backend's
local admission checks; do not expose Vite as an unauthenticated remote proxy.
The deployed SPA keeps relative API/sync URLs at the explicitly approved gateway
origin, without frontend credentials or a second mode selector. See
[remote-access boundary](../docs/features.md#remote-access).

Root `make check` reuses lint, test and build serially,
with build output in a disposable directory so existing `dist/` stays intact.
The dependency ignore also permits a shared `node_modules` symlink in worktrees.
See the [checked PR workflow](../CONTRIBUTING.md#check-and-publish).

## Checklist-audit corrections

Manual-note HTTP saves require literal `saved: true` before advancing the dirty
baseline or requesting sync. Invalid/empty receipts preserve the draft and route
protection; device-queue saves remain separate. Ops cannot refresh labels or train
until the configured classifier is known; it never guesses `logreg` on a failed read.
The existing reading disclosure explains missing original value/targets, uses the
shared supported-goal predicate for legacy rows, and owns applicability rationale
without a duplicate full-digest row. No ranking or scientific judgment changes.

## Product surfaces

- **Library** — Read next, labels, reviews, figures, paper Q&A, and acquisition.
- **Search** — query-driven discovery and review.
- **Weekly** — read-only saved RSS research digests, engineering cards, paper links, and JSON/Markdown downloads.
- **Today** — feed triage and Zotero filing. Author h-index badges render only for positive OpenAlex evidence; unresolved author metadata is not shown as `h=0`.
- **Settings** — AI, research profile, Zotero, sources, and advanced controls. Sources offers explicit HackerNoon tag-feed subscriptions through the existing RSS API; saving, fetching and Today triage remain separate actions.
- **Ops** — feeds, jobs, pending writes, diagnostics, and retraining.

Today cards label the unchanged composite bands as **Relevance** (moderate,
not “Fair”), separately from current AI evidence grades. Legacy row grades are
unconfirmed; unknown review evidence never means neutral/poor science or proves
no review was attempted. A current needs-full-text/not-ready entry is explicit;
a join miss cannot distinguish absent, stale or failed reviews. The full-review
page retains an explicit **Generate review** action for empty, failed or
needs-full-text cached entries, even when a legacy grade remains visible. It
stays disabled while AI is off, unreachable or offline; cached placeholders do
not trigger an automatic retry. A running retry shows progress instead. One native
**Why selected** disclosure replaces the separate reason-chip row and contains
existing reasons, the actual slate policy and recorded cohort/picker keys.
Exploration roles are labelled as exploration. Interleave withholds the new
review/selection diagnostics. Rendering preserves server order, scores and labels.
The opt-in `tests/test_today_selection_browser.py` reuses the existing isolated
application-browser fixture for real API/SQLite, keyboard, contrast and reflow
checks with synthetic cache variants; it does not demonstrate scientific ranking
quality or native-user comprehension.

Legacy `/annotate`, `/review`, `/triage`, and `/pending` links redirect without
dropping query parameters. `/review?state=gate_rejected` reaches the Feed Review
tab in Ops with its pile filter intact.

Library and Today share `todayHelpers.fulltextMessage` for unavailable-PDF
accounting from per-item outcomes. The full-text API no longer exposes partial
per-reason counters; deploy frontend and backend together. Library reports a
missing/failed result explicitly. A failed status request stops local polling
and reports that the server job may still be running; it never implies success
or silently retries forever. Ops retraining likewise stops automatic reads on a
status error, including with retained running data. **Retry status** reads only
the same job; Retrain remains blocked until a terminal status is observed. A
successful running retry resumes normal polling; terminal success/failure stops it.

Ops' Triage Monitor monitors selected-item jobs started in Library; its duplicate
backlog-start shortcut is removed. Feed backlog triage stays in Today with its
existing status query, separate from these jobs. The monitor lists every
`active_items` entry from the job API, rather than displaying the last completed paper as current. Polling continues through
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
Settings adopts cold and same-page `#ai-models`, `#rss-feeds` and
`#university-access` destinations after the form loads, opening the matching
disclosure and focusing an existing control. Typed checklist recovery navigates
without verification or source refresh. Saving an AI-mode change round-trips
unsurfaced config fields unchanged. University login requires its displayed access
fields to match Settings’ saved configuration and waits for any Save to finish;
unrelated unsaved edits do not block the unchanged destination. Login never saves
configuration implicitly. A failed/unconfirmed Save cannot admit the edited URL.
The readiness query's older URL is not the saved-destination authority.

Search offers an unchecked-by-default original-source question-evidence experiment.
The checkbox sends `evidence_policy=question_evidence` for that search only; sessions
restore the saved policy/status, not future opt-in consent. QuestionEvidence shows
scoped states, original-character locators and evaluator/capability provenance.
Recommended preserves server promotions (including weak-band cards); explicit sorts
still show the whole pool. Late restore/Add responses cannot overwrite a new session.
No automatic Add or measured quality/latency claim accompanies evidence. See
[feature overview](../docs/features.md#search-question-evidence).
Ordinary Add acknowledgement retires the existing poll generation, not the session;
fresh reads continue review progress and cannot publish an older pre-ACK DTO.
Explicit same-session retry GETs share read-generation fencing without retiring
pending Add owners; later acknowledgements invalidate older direct-read snapshots.
Search textareas retain visible native labels after typing; Ops System supplies its
section heading before the reusable card headings.
Expanded Discovery evidence reuses reading-prose wrapping and zero-minimum card/grid
sizing at narrow/wide widths, preserving full copyable IDs, locators and quotes.

Search coverage hits reuse their existing Zotero key: the card says In library
and the server command cannot create a duplicate even if called directly.
Both ordinary and discovery Search reuse the canonical paper-link resolver:
HTTP(S) addresses remain unchanged, identifier-only results receive fixed-host
DOI/arXiv/PMID/OpenAlex links, and unsupported addresses show a non-action message.
Search keys cards and Add requests by the server's persisted candidate ID, never
by title. The query panel consumes `plan.display` so tight/broad variants and
OpenReview are not omitted. Backend and frontend must be deployed together.
Today/Search's shared collection picker consumes the API's `items` tree, including
nested collections; it does not mistake the response envelope for the tree.

The shared moderate-relevance pill uses the existing amber-600 palette token:
white text contrast is 5.58:1 (amber-500 was 3.95:1). This display-only correction
does not change score bands, ordering or saved labels.

## Saved weekly digests

Open **Weekly** (`/weekly`), select a saved CLI run, or reopen its bookmark
`/weekly?run=<bundle-name>`. Up to 100 bundles are listed in reverse bundle-name
order (not publication time); other retained runs remain reachable by URL. **Refresh saved runs** refreshes both list and
selection after CLI publication. A valid zero-card digest, a missing report and
an unreadable/unsupported report remain distinct. Legacy missing provenance and
ideas stay unknown/unrecorded; missing reviews do not imply missing PDFs.
Cards expose project use, artifacts, reproducibility, ideas and selection audit,
with current-paper links through the existing `/paper/:itemKey?autoRun=false`
route. These links and subsequent Prev/Next navigation never auto-start reviews;
Generate review remains an explicit action. Other paper entry points are unchanged.
Unsupported formats and invalid/unreadable reports show different errors; folder
scan failures never appear as an empty saved-run list.

This page needs the local server and does not persist reports for offline use.
It never builds a report, acquires sources, calls AI or writes Zotero. Use the
existing `research-feed run` CLI to publish a new bundle. The focused tests are
`src/pages/Weekly.test.jsx`, `src/api/researchFeedApi.test.js` and the optional
`tests/test_research_feed_browser.py` built-app check using the existing fixture.
The scripted Weekly test also preserves retained unverified observations as literal
visible list text, without HTML/link execution, invented project actions or mutation
of the received snapshot; this is not evidence of scientific usefulness.
Pending-review regressions separately require visible links/reasons for unqualified
reviews, without calling missing reviews unavailable PDFs or starting generation.
The existing Needs a current review / full text section shows both source needs
and separately labelled unqualified-review links. None recorded applies only when
both are absent; opening a linked paper still uses autoRun=false.

## Setup and AI

`/setup` is a skippable Mode (Full local / Hosted / No LLM) → Zotero → AI
profile (skipped for No LLM) → research wizard. Local mode shows no API-key
control; hardware-incompatible profiles cannot be selected. Static compatibility
is derived from each stage's current routed model and the shared preset query,
including resumed research steps; a remembered card cannot override configuration.
Existing local endpoints retain one editable model-name input before listing,
with native suggestions afterward. Listing fetches metadata only, never loads or
downloads a model. Catalog copy distinguishes met static floors from unverified
Existing-model capacity. Step changes reuse the shared heading-focus hook. Saving unlocks
Today only after explicit Doctor verification passes; saving alone never
claims the configured pipeline works. The wizard and Settings
share the compact server-side Doctor checklist. The gate uses backend
`configured`; stricter operational `ready` remains diagnostic.

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
step change is announced politely to assistive technology. Its clipped status text
shares the label's containing block, so initial Setup fits 320/390px viewports
without removing assistive state or clipping the whole page.
Optional browser preferences use guarded storage access, so privacy settings that
block Web Storage do not prevent setup, Today, Library, or Search views from rendering.
Search session pointers/drafts are best-effort in `sessionStorage`; blocked storage
simply disables reload restoration for that tab.

Offline sync pulls use the complete paper snapshot and revision cursor; the
protocol's legacy `changes` field is empty because the PWA does not consume history.

`utils/setupDoctorState.js` owns Doctor request generations per QueryClient.
Wizard/Settings consume only that query cache, never a completed mutation fallback.
Config/RSS writes pass their outcome to that same owner: success and ambiguous
transport/JSON/5xx outcomes invalidate health; recognized403/422 rejections preserve
the unchanged report and current owner. Other explicit invalidation callers retain
their existing behavior. Late checks cannot restore pre-edit Ready after a real
change. A single not-started reason stays visible; multiple pending checks fold
into one native disclosure with their IDs and supplied reasons intact. Passed and
optional-unavailable results share a separately labelled disclosure, preserving
both statuses. Structured UNKNOWN load receipts remain explicit, never Ready. The checklist polls running
checks, cancels by actual run ID, and links unknown local admission to AI Settings
separately from a scoped retry. Stop never claims remote termination. Missing
production ResourceEnvelope keeps local LLM verification unknown, not Ready;
hosted/no-LLM and nonloading listing remain available. The wizard accepts the same
127/8, mapped-IPv6, numeric-shorthand and reserved-localhost endpoints as backend
admission; choosing an existing local endpoint does not require a hosted key.
No hardware acceptance is claimed by synthetic component tests.

## Paper review

Review status retains review dates and recorded note errors, but never displays
native-delivery success from a historical cache boolean. Explicit Pending/native
acknowledgements have their own owner; reading a review does not rewrite history
or verify a native write. Component and built-browser controls retain cache bytes.

The card and full-page verdict show the saved reading plan without opening Details:
ordered read targets, optional skip targets and the original's added value. These
fields have one displayed home. Unknown decisions show neutral unavailable copy,
not an old action rationale, targets or time; evidence panels remain unchanged.
Missing targets/value/time remain explicit unknowns. Existing policy, scores, cache,
API and native HTML briefs are unchanged; this is only the React presentation.
PaperReview admits the projected action once for its verdict, time and reading plan;
the plan presenter has no separate action-validity policy. Whitespace-only plan
entries are omitted; other text and order stay unchanged. The time chip identifies
the saved estimate, which is not recalculated when policy caps the action.

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
signals display the server's shared policy projection, not a second client policy.
Writing-friction reasons have one home in that disclosure; the folded full digest
no longer repeats them. Source wording and the independent scientific assessment
remain unchanged across compact, standard and full review.
The server projects the scientific grade into `digest.grade`, shared with proposals
and saved notes; React does not repeat its fallback rule. Independent failure returns
explicit unassessed/grade-empty state, never a new self-grade fallback.
`model_digest` retains the original self-grade but cannot supply a displayed grade.
Deploy with the matching backend; unprojected raw digests are not review UI inputs. Checklist summaries use only supplied applicable criteria: a position
paper never acquires invented ablation gaps. Known thesis/evidence/scope identifiers
have matching compact labels in React and HTML; unknown identifiers retain their
literal fallback. Mixed miss/not-retrieved goals use a neutral remainder heading
while their separate evidence states and meters remain unchanged. Missing writing assessments stay unknown;
non-papers have no scientific grade. Compact/full/legacy layouts remain intact.
HTML briefs and
figures live beside source PDFs for Zotero compatibility.

Compact cards and the full paper page share the explicit verdict editor. Picking
a priority changes a draft; Save/Update is the write boundary, and Cancel restores
the saved priority and comment. Batch priority keys also require successful detail
for the exact selected paper; pending, failed or foreign detail cannot prompt/write
an empty rationale. Navigation keys remain available. The mobile bar jumps to this same editor instead
of issuing a second, comment-erasing write. Clearing a comment remains an explicit edit.

The paper reader's stale notice covers changes to the PDF source, parser setting
or renderer. Automatic rebuild attempts are scoped to item and retained publication,
including cached A→B→A navigation; failures do not automatically replay. A held build
invalidates only its original item query, with captured source/acquisition options.
Stale or incomplete outputs offer no figures/static links until rebuilt.
Blocking or unverified audits arrive as errors: the reader shows the failure and
rebuild action without exposing old figures or artifact links. Build notices
describe audited HTML/figures, not Markdown notes (which are no longer generated).

Unknown review-job status has one explicit **Retry status** action in the existing
full/compact presenters. It performs the current-paper GET only, works without an
available model, and cannot trigger auto-generation if the reply is idle. Generate
is unavailable while status is unknown/checking; retained review content stays.
Question capture is independent of PDF/render availability: the same composer stays
mounted, while raw-PDF modes still require a PDF and grounding/abstention is unchanged.

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
with the verdict and returns only the processed ID and state. Local success requires
that exact ID and the action-appropriate state; missing/mismatched receipts and
transport/JSON/server errors stay unconfirmed. Use the existing Refresh before
another action; no verdict request is automatically replayed. The endpoint's
explicit 404/422 refusals remain failures, not acknowledged saves.
Pending treats a partially failed HTTP-200 Apply batch as an error and points to
the Failed tab. Only a fully read HTTP 409 is shown as stale; a received 409 whose
body cannot be read remains unconfirmed. Reject resolves only when the API's
integer updated count matches the captured distinct IDs; zero/partial/contradictory/
malformed replies cannot announce success, and existing rollback retains the
selection for reconciliation.
Today, Library and Pending derive batch counts and write IDs from the same current
filtered selection. Hidden selections cannot enter Add/Trash/Triage/Apply/Reject;
an empty filtered selection is a no-op. Library/Pending also block retained rows
during loading or a failed read. A started batch keeps its captured IDs through retries.
Shared list shortcuts operate outside interactive controls only. Native inputs
(including checkboxes), selectors, links/buttons, rich-text editors and ARIA
widgets keep their own keys; composition and already-handled events are ignored.
Pending's collection selector cannot issue Apply/Reject, while the explicit batch
buttons and shortcuts on the non-interactive list surface remain available.
Note, tag and collection editor labels natively identify their own fields even
with repeated queued changes; editing, saving and applying remain separate.

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

The shared `/paper/:key` page includes `RelatedPapers`: explicitly load up to
three saved links, then optionally confirm **Try with comments — experimental**.
The confirmation names the configured review provider/model/endpoint and complete
private seed/candidate context. Metadata hybrid remains default. The panel retains
comment-source identity and failed/unknown/empty distinctions; failures and
budget/interruption/staleness do not masquerade as successful contextual ranking.
Acknowledged note/verdict saves immediately fence the panel, before the detail
refetch returns. Verdict fencing consumes the primary acknowledgement through
`usePaperReview.onVerdictAcknowledged`, not completion of the secondary reject-tag
request. Existing `onSaved` completion/collapse and secondary warnings stay intact. Changed saved detail also remounts it with a new React-owned
query identity. Closed, loading and failed reads do not display retained comment
bodies; an old cached or pending GET cannot publish into the replacement owner.
GETs consume React Query's cancellation signal, including unmount cancellation.
An explicit fresh load/refresh/retry is still available, without an automatic
semantic POST. No second client store is introduced.
[Saved-paper feature boundary](../docs/features.md#related-saved-papers).

### Saved-seed discovery

The same Related section independently reads discovery admission. Only a server-positive
saved seed enables **Find new relevant**. Reads never POST; eligibility errors, unavailable
context and failed requests have explicit recovery, not focus/reconnect retries.
The folded boundary disclosure optionally confirms private **seed** comments and
your reading verdict/rationale to the named model; otherwise the one Find action sends a null consent token and uses public
metadata only. Providers never receive those comments. ACK/remount/navigation abort and
fence obsolete discovery completions before they can navigate.

`/search?session=<id>` adopts the server DTO before tab storage, including when storage
is blocked. Discovery shows three server recommendations, the retained pool, typed
retrieval/assessment/freshness, metadata quotes and attributed seed comments. Submitted
IDs are not model-cited IDs; quote membership is not entailment or a full-text review.
Stale/unverified/failed-status reads hide private evidence and disable new effects.
Review and Add are explicit; Add is followed by a fresh server read. A retained plan
uses the same action as explicit **Reconcile retained Add**, with server-enforced
existing-only recovery when stale. Find V1 searches only OpenAlex semantic/lexical;
ordinary topic Search retains all sources. Discovery polling
stops on read failure and offers Retry; ordinary Search still auto-reviews and retries
transient polling failures. A retry link returns to fresh seed eligibility and unchecked
consent; URL retry intent alone never starts work.

Related regression oracles bind these current owners rather than a retired inline
component: saved links/attributed evidence, independent admission, ACK fences and
Search's retained typed outcomes. Three-result caps belong to the backend, not a
second client truncation. Shared channel identifiers are synthetic test inputs.

This source seam requires the matching discovery backend/provider changes in the same
feature; it is not a standalone UI release. Controlled DOM/API/polling tests, build,
browser and independent review are required; their scope is not live-provider,
private-user usefulness or scientific acceptance. See [feature boundary](../docs/features.md#saved-paper-discovery).

## Offline boundary

The PWA keeps one existing IndexedDB papers/meta/outbox authority and v1 typed
sync protocol. Full pulls validate unique identities/monotone cursor and atomically
publish snapshots, order, compact detail for unopened papers and cursor. The
unscoped cold Library hydrates that working set while a bounded network check is
pending; an old scope/poll cannot overwrite the current view. Uncached server
filters/rescoring refuse rather than showing an unfiltered set as a verified match.

`usePaperReview` admits local-capable detail/set/delete callbacks independently of
React Query network scheduling. Shared verdict/note editors preserve dirty text
across refetch and fence late completions by owner/request. Full-detail cache writes
reuse the same pending-field/order projection as pull, not a second form store.
Compact snapshots do not claim synchronized tag/collection/PDF state. A failed
pending-field read refuses the incoming detail rather than displaying stale server
text. Keep mine projects the resolution into both durable local views atomically;
its refresh also works disconnected. Detail reads reuse the existing known-transport
outage predicate, so a durable local deletion refreshes without another blocking
request even when the browser still reports online. Observed HTTP/protocol refusals
remain errors, not cache permission. A transport fallback revokes stale connected
status, and saves signalled during a sync round schedule a follow-up round. Pull
request ordering lives in the existing meta store across tabs, fencing older
responses even when membership changes without a field-cursor increment. Queue
rows project nested verdicts into their flat display contract, including clears.
Batch verdict/retraction callbacks also run offline; local proposal confirmations
skip the server-only reject-tag request. Batch feedback names device vs app saves.

Only fetch transport failures or an already reported transport outage admit local
capture. Observed HTTP refusals, malformed received ACKs and mismatched detail IDs
remain errors. `api/offlineTransport.js` shares the existing sync safety budget:
finite positive build-time `VITE_SYNC_TIMEOUT_MS`, default15000/max60000ms. It bounds
only offline-capable requests; ordinary long-running APIs keep their old policy.
A decode failure after receiving a response cannot mint another offline mutation.

The existing sync event owns unchecked/syncing/connected/unavailable/blocked state;
HTTP/protocol/storage blocks are not transport-outage evidence. Connected follows
valid pull and a completed device transaction, not initial navigator.onLine or an
assumed zero pending count. Conflict controls compare both versions/comments before
one explicit resolution UUID/sequence; invalid/foreign ACKs roll back atomically.
Canonical evidence uses read-side types, not outbound text-size caps. `keep_server`
references the compared server revision without echoing its body; local recovery
intent survives another conflict but is stripped from the wire. A no-op app receipt
never changes a field/transition/native mirror or acknowledges past native work.
The UI reuses those states to disable Mac/model-only controls, without granting
backend authorization. PDFs, AI, annotations, acquisition and native Zotero work
remain server-only. See [offline review](../docs/features.md#offline-review). Fake-indexeddb,
owned Chrome/TCP/SQLite, physical iOS and authenticated remote acceptance are distinct.

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
same-machine loopback PWA boundary by default. The separate explicit authenticated
HTTPS gateway is described in [remote access](../docs/features.md#remote-access);
physical device/offline/revocation acceptance remains separate. Never expose
`/api/sync` directly as unauthenticated LAN HTTP.

Sequence allocation, mutation insert, optimistic paper state, and cached-detail
update share one IndexedDB transaction; each full pull replaces the compact
paper snapshot while retaining local papers with unresolved mutations, and
refreshes cached review details. The configured sync deadline cancels outstanding
network work; it does not bound browser-storage operations. Protocol incompatibility
explicitly asks for an app refresh. The IndexedDB
test kills/reimports the client module and proves cached context
plus ordered concurrent mutations survive while the server is absent. Ask Paper sends a
bounded session history; the backend compacts older evidence to verified
extraction handles, and the UI labels verified quotes without inventing pages.

Sync sends ordered batches of at most 100 with acknowledged predecessor UUIDs.
Receipt storage and outbox retirement share an IndexedDB transaction; restart
or timeout preserves the unsent tail and its causal base without rewriting UUID
bodies. Input bounds are checked before optimistic saves. Invalid legacy drafts
are retained as rejected, with copyable text and paper links in the navigation
status, while valid rows continue. Unknown acknowledgement IDs/status/revisions
cannot retire pending rows; HTTP rejections are distinct from connection failure.
Each sync round has its own network deadline; future rounds get a new budget.
Device transaction completion remains a separate prerequisite.

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
change server review selection. RSS refresh distinguishes partial failures, offline skips, zero returned items and an unavailable saved-source list. Persisted per-feed errors reload even when refresh fails. Preset saves retain custom drafts; uncertain writes reread rows without automatically repeating the write. Preset adds are create-only, including stale lists and imported host/default-port variants; existing subscription settings stay unchanged. Source writes are single-flight in the section. Deploy frontend and backend together for the create-only contract.

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

The original OrthogonalSignals disclosure replaces the abbreviated signal line. It presents stored idea/evidence/writing assessments and reading-policy reasons without changing the current server decision or projected grade. No second scoring rule or new inference is added.

Retain original setup source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.

Retain original RSS source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.

Retain original search-evidence source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.

Retain original reading source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.

Original discovery regression oracles are retained without changing assertions. Source-only retention does not establish current-contract compatibility.

Original native-preview client source is retained: displayed approval hashes, explicit refresh/force and direct-editor outcomes use the existing backend; no new controls or protocols are designed. Original synthetic client tests remain unchanged.

The free-tag editor retains exact input on failed, unconfirmed or declined-force
writes. Same-item applied/no-op receipts clear only the unchanged submitted draft;
newer edits and successor paper/request owners are fenced. Writes are single-flight,
and ambiguous delivery pauses this mounted editor without automatic replay or a new
reconciliation API. The native history/backup and backend uncertainty blocker remain
authoritative; remounting is not reconciliation. Focused regressions live in
`components/paper/TagOfInterestEditor.test.jsx`, `components/AdminSection.test.jsx`
and the existing `pages/Settings.test.jsx` (all relative to `src/`).

Doctor/RSS component fixtures expose the actual imported cancellation operation
instead of hiding the real checklist. Original race/source-invalidation oracles
remain; explicit Stop binds the current run and rereads unresolved ownership,
not proof of backend callback or remote termination.

Native editors/readers retain their shared server-availability gates. Eligible
rail/compact question owners stay mounted across transient sync/offline changes,
reusing AskPaperBox's existing device capture, explicit same-UUID answer retry,
item/mount fences and immutable delivery binding. Reconnection alone does not
replay an uncertain request; source/PDF eligibility is unchanged. Parent-transition
unit controls are distinct from built-browser/native delivery acceptance.

Original question/note UI source is retained: immutable submission identity, separate manual notes and source question list, existing outbox replay and signature-bound delivery state. Shared paper/rail assemblies keep the original callbacks and one editable notes surface; unrelated Related/orthogonal reading changes are not mixed into this slice. Cached question detail reports local-storage failure explicitly without claiming a device save.

Retain original offline PDF/figure controls and exact reading-text selectors; no new offline protocol.
Retain original question-native source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.
Retain original infrastructure source regression oracles for review. No assertions/skips are changed; source retention is not behavioral acceptance of the current API variant.
