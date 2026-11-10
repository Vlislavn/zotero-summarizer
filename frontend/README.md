# Zotero Summarizer frontend

React 18 + Vite 5 + Tailwind 3 single-page app, served by FastAPI at the app root.
This README is the contract for people changing `frontend/`; user-facing behavior
lives in [docs/features.md](../docs/features.md), system structure in
[docs/architecture.md](../docs/architecture.md), the visual system in
[DESIGN.md](DESIGN.md), test running in [tests/README.md](../tests/README.md).

## Run and build

```bash
make dev     # repo root: backend :8000 + Vite hot reload :5173
make run     # serves the built frontend/dist through FastAPI; rebuilds when sources change
make check   # lint, Python + frontend tests, build into a disposable dir, smoke test
cd frontend && npm run lint | test | build
```

- Vite proxies `/api` to `http://localhost:8000` (override: `VITE_API_TARGET`) with
  `changeOrigin: false`, so the browser Host/Origin reaches the backend's local-admission
  check. Never expose Vite as a remote proxy.
- The SPA calls relative `/api/...` URLs at whatever origin served it (loopback or the
  approved gateway, see [remote access](../docs/features.md#remote-access)); it holds no
  credentials and has no mode selector.
- Backend and frontend ship together: wrappers validate response shapes strictly.
- Publishing flow: [CONTRIBUTING](../CONTRIBUTING.md#check-and-publish).

## Source map

```text
src/
  main.jsx            QueryClient + createBrowserRouter (one data router) + service worker + startSync
  App.jsx             routes (`/` → Today), legacy redirects (RedirectTo), SetupGate, NavBar
  components/NavBar.jsx + OfflineStatus.jsx (conflicts name the cached paper title) + ConnectionGuide.jsx
                      app chrome: ≥44 px tabs and one status line (sync state · `/api/pending/count`
                      via hooks/usePendingCount.js · unsent device outbox); details/Sync now/install
                      guide collapse, and open by themselves on outage, error or conflict
  pages/              route orchestration: Library(+ReadNext/AnnotationVerdict batch mode), Search,
                      Today, Weekly, Settings, Ops (Feed review / Triage jobs / Pending changes / System tabs),
                      PaperReviewPage (/paper/:itemKey), SetupFlow (/setup); *Helpers.js beside pages
  components/         shared UI and workflows by domain: paper/ (review, RelatedPapers), library/,
                      today/, search/, settings/ (task pages), setup/, form/Fields, ui/ primitives
  hooks/              usePaperReview, useDeepReviewRunner, useConnectionTask, useTodayVisit,
                      useReviewCoolLoop, useServerAvailable, useKeyboardNav, useOptimisticAction, …
  api/                one thin wrapper per backend area on api/client.js `request`
  utils/              pure helpers: configForm, settingsNavigation, todayVisit, relevanceBands,
                      setupDoctorState, safeStorage, humanizeError, searchSort
  offlineStore.js     IndexedDB papers / mutations (outbox) / meta
  syncClient.js       typed sync protocol over /api/sync/push|pull
  searchPolling.js, questionSubmission.js
public/               sw.js (app-shell cache; never caches /api), manifest.webmanifest
```

Routes: `/` → `/today` (default), `/library` (`?mode=batch`), `/search`, `/weekly`, `/settings`,
`/ops` (`?tab=`), `/setup`, `/paper/:itemKey`. `/annotate`, `/review`, `/triage`, `/pending`
redirect through `RedirectTo` in `src/App.jsx`, carrying the query string. Settings and
source tasks open with `?task=`; the old Settings hashes resolve through `LEGACY` in
`utils/settingsNavigation.js`.

## Invariants

**State**
- React Query is the only client cache; component state is transient UI. No second store.
  Browser storage goes through `utils/safeStorage.js` so blocked storage degrades, not breaks.
- Today's shortlist lives under `zs.todayVisit.v1` (`utils/todayVisit.js`, QueryClient-backed).
  Reads never replay writes; clearing site data loses it. The checkbox SELECTION persists
  in the same visit (survives a round trip to a paper and reload; a new shortlist starts
  empty). Members do NOT refetch on window focus, so returning from another app never flips
  the cards to "Status not current". The members result is kept 5 min (`gcTime`), so a return to
  Today renders it at once and revalidates on mount; only a first load or an invalidated entry
  (verdict/review finished elsewhere, Refresh) shows "Checking…"/disabled until the reply.
  A verdict saved on the paper page opened from Today is written into the retained visit and the cached members reply at once (`finishTodayReviewAction`), so the card shows "Decision: X" on return; a skip of a feed-only paper (`feed:…`) does not call reject-tag.
- Today per-card decision (#37): `PaperCard` shows must/should/could/skip with undo and an
  optional one-tap reason chip; it posts to `POST /api/daily/verdict` (undo: `/verdict/undo`)
  and reflects the saved verdict from the member facts. The bulk Add/Trash bar appears only
  once ≥1 card is ticked (no always-on 210 px toolbar). **Owner decision (supersedes "keep
  `require_review` current-only"):** must/should/could (and bulk Add) still require a full-text
  review, but a current **or** outdated-same-PDF review now counts — the `require_review`
  service gate guards a write to the owner's real Zotero, so it is KEPT; an outdated-but-valid
  review keeps the card recommended with a “review outdated” badge and enabled buttons, while a
  missing/invalid/`pdf_changed` review still shows the "needs a review first — Generate" note,
  and skip is always available.
  Reason chips are never pre-ticked and are captured per item, never in a batch.
- Card on Today + paper page (#98, redesigned #124, English + terse TL;DR #127): `components/card/PaperFactsCard.jsx`
  (+ `AiSummary.jsx`, `FactStrip.jsx`, pure `cardModel.js`, `api/cardApi.js`) renders `GET /api/card/{key}` under the
  title. Order: **AI TL;DR** → **fact icon row** → one quiet who-is-behind line. The TL;DR is ≤4 labelled one-line
  bullets (`New` / `Take` / `Caveat` / `Code`; `card.ai.bullets`, never prose — a card without `bullets` is treated as
  not generated) in a label column + text; the `Caveat` label is ochre. Facts are 10 small icons + the code mark from the shared
  inline-SVG set `components/ui/Icon.jsx` (lucide-style, stroke 1.5): *Published* (prompts, protocol, judge-vs-human,
  contracts, data) | *Method* (baselines, ablation, uncertainty, external validation, reproducibility) | the GitHub mark
  with a ✓/✗ dot (a link when the repo exists). The row shows **only the facts that were found** (#231), in that
  order; with none it is one quiet line («Code, data, method: full text not read» / «… nothing found»), never empty
  circles. Present = Forest on Mint, absent = faint outline, unknown (no full text read) = dashed circle; each has an
  English `aria-label`+`title`, and the legend in the card's single “more” keeps all 11 with their state + words. With no TL;DR the row reads «AI TL;DR» + «Generate AI TL;DR»; the first use shows
  ONE consent line («The paper text will be sent to an external model (<provider>) to make the AI TL;DR. Allow?»,
  posts the GET's `boundary.token`), later taps generate at once (`POST /api/card/{key}/generate`). The fetch is
  additive and fail-soft; unavailability reasons (`cardModel.js` `AI_REASON_MESSAGE`) use the backend's keys
  (`call_cap`, `daily_token_cap`, `circuit_open`, …).
- Today "Not reviewed yet (N)" shelf: `components/today/NotReviewedShelf.jsx` under the cards, fed by the
  `unreviewed` field of `/api/daily` kept in the retained visit (`todayVisit.js`). One tap row per paper
  (title, source, ≤2 why chips, cached TL;DR bullets) → `/paper/<key>`; no verdict/Add controls.
- Today page (#124): one ≤760 px reading column (the cards are a one-at-a-time cull queue, so no master/list);
  header = title + ONE Forest **New shortlist** + **More** (Refresh status, Triage backlog, Article selection,
  RSS sources, Telegram today; it opens by itself while triage runs or fails). Card order: pills + *Open full
  review* → headline → AI TL;DR + icons + who → ONE why line → verdict buttons → “more” (authors, venue,
  abstract, icon legend, selection details). Verdict tints (`decisionVocab.js` `DECISION_TINT`): must Forest solid,
  should Sage, could Mint, skip hairline outline; the chosen one shows ✓ + Forest border. Buttons are compact
  content-sized pills grouped left (`DECISION_BTN`: 40 px on phones with a 44 px tap target via an invisible
  `::before`, 32 px on desktop) — the same look on the paper-page sticky bar and the rail's `VerdictPanel` edit row. Loading shows card skeletons.
- **Language rule (#127, supersedes #108):** the whole UI is English — chrome, buttons (`must/should/could/skip`,
  `Undo`), reason prompt + chips (`components/decisionVocab.js`; `NEG_CHIPS` and the shared chip style `chipClass` live there), the one-line “why selected”,
  tooltips, errors, empty states and the AI TL;DR itself. **Only exception: Telegram post/discussion summaries and
  trend labels stay in the language of the source chats** (they summarise that text); Telegram chrome is English too.
  Stored paper content is shown as stored. New strings follow this split.
- Today card leftovers (#108): the selection explanation is ONE plain line (`Close to your goal “…” · …`) with
  the honesty facts (policy, keys, review evidence) behind “more”; the review chip is hidden when the
  evidence is unknown/unrecorded (the detail stays in “more”); once the card has an AI TL;DR the abstract
  collapses (`abstract`); `Random check` uses the solid badge style of the relevance badges.
- Paper page decision (#94): the verdict (`components/paper/review/PaperVerdict.jsx`, shared
  by the desktop rail and the mobile sticky bar) uses the same must/should/could/skip vocabulary
  and one-tap reason chips as Today; a resting verdict reads `Yours:`/`Auto:` by its `source`.
  Auto deep review on open is opt-in: `useDeepReviewRunner` only auto-fires when
  `GET /api/paper/prefs` reports `auto_review:true` (`ZS_PAPER_AUTO_REVIEW`, default OFF).
  `usePaperReview` fetches `review-detail` once per open. Related-in-library sits below the
  abstract, collapsed.
- Paper page layout (#125): the whole page is a centred `max-w-[76rem]` frame (TOC / reading column / rail),
  with one ≤68ch reading column inside it. Order: title/authors/links → `PaperFactsCard` →
  ONE summary card (`review/Recommendation.jsx`: verdict chip, minutes, grade, the why with its first
  sentence leading, `ReadingPlan` «Read first / Skip / What the original adds», policy reasons, code row
  `review/CodeLink.jsx`) → `Fold` sections (`review/primitives.jsx`: serif title + one-line summary while
  closed). Only Contribution and Findings start open; Caveats and coverage (red flags = clay left rule +
  quiet «verify» badge; summary names the flag count), Relevance, Methods, Assessment, Figures, Paper map,
  Abstract and Related are closed folds with their content in the DOM. The closed **Evidence** fold (`review/EvidenceFold.jsx`, #40) reads `GET /api/library/review-audit/{key}` and lists, per verified claim (Contribution, Findings), the verbatim quotes from the paper (the best-matching sentence ±1 of the verified passage + «Show full passage»; «…» marks an excerpt); `not_established_fields` show as «could not be grounded this time»; legacy / stale / PDF-changed states are one plain sentence; older-rules audits carry a note. An outdated review is one line +
  `Regenerate` (`onRegenerate` → `runner.run()`). The mobile verdict bar uses graded tints (must Forest
  solid, should Sage, could Mint, skip outline), no black fills.
- Today exposure (card shown/dwell, review and link opens) is reported best-effort to
  `/api/daily/events` by `hooks/useCardExposure.js` and `utils/todayEvents.js`, keyed by
  the retained visit id (= the logged `slate_id`); a failed send is dropped. Cards with
  `role: "exploration"` are marked "Random check".
- The client does not recompute server policy: verdict/action, reading plan and scientific
  grade are rendered from the server projection (`digest.grade`, projected decision).
  Every printed letter carries what it rests on (`tones.js` `qualityBasis`/`gradeBasis`): `A · 3 of 5 checks`
  from stored `coverage_met`/`coverage_applicable` (old reviews → letter only).
  The one client mirror, the effective relevance band with prestige demotion
  (`utils/relevanceBands.js`), is shared by Library filters and the cool-review loop.

**Visual hierarchy**
- `tailwind.config.js` maps `font-semibold`/`font-bold` to 500 and `index.css` sets bare
  `<b>`/`<strong>` to 500 (DESIGN.md: no weight ≥600). Use `font-medium` in new code.
- Paper page order on iPhone: title → byline (first 3 + last author, `+N more`) → outdated
  line → verdict card → facts strip; secondary detail lives in folds, never deleted.
- One disclosure look: a plain `<summary>` gets the shared chevron from `index.css` (no native ▶; a summary
  with its own `flex`/`grid`/`list-none` layout draws its own, e.g. `Disclosure`). Rail headings (`Your verdict`,
  `My notes`, `Ask this paper`) use `SectionLabel` (`className` for spacing). `NavBar` status row is 36 px on
  iPhone; its Details tap area is 44 px via `after:`.
- Settings states a fact once: `StepModelsTable` rows carry model, `provider · Local/Hosted`, speed and a
  short status; each provider's address and the status footnote sit in the closed «Providers and status
  checks» disclosure; the failure reason is one line per provider on AI models / Setup and lives in
  `ReadinessStrip` on the overview (the strip lists only open issues, pill + reason). Overview rows show a
  short «Change» whose `aria-label` is the full action. Measurements go behind the shared `Disclosure`
  («What was measured»); the task footer speaks only when there is an unsaved draft.

**Navigation and drafts**
- The data router installed in `main.jsx` owns navigation admission. Dirty/pending drafts block
  through `useBlocker` + `useBeforeUnload`: `components/settings/TaskPage.jsx` (one blocker per
  task page) and `pages/PaperReviewPage.jsx` (unsaved notes). Do not add callback-only guards;
  tests needing a router use `src/testRouter.jsx`.
- `autoRun=false` paper links (Today, Weekly, Settings returns) never start a review;
  generation is an explicit action. `useDeepReviewRunner` defaults `autoRun` to false.
- `components/library/PaperFigures.jsx` draws images only from a current render; a stale artifact
  shows "Rebuilding figures…" / "Figures are outdated" + Rebuild (the server 404s stale files). Figures and Tables are separate groups; a caption's repeated leading label is dropped.
- Page headers: `components/ui/PageTitle.jsx` is the one title style (Library, Search, Weekly; routes unchanged) and holds the one-line purpose of Today / Library / Search / Weekly (`PAGE_PURPOSE`; Today's owner can adopt it). Read next's Browse lists use `ExpandableList` (fade at the cut edge + "Show all N"); its selection bar adds `BulkVerdictBar` — per-paper verdict (dont_read also queues ❌) and "Mark read" (the 👀 skimmed tag; the paper leaves the queue), stopping at the first failure with "N of M" — next to the existing Run triage / Add to collection. Empty Ops Feed review offers `FeedReviewEmpty` (fetch RSS, then score the backlog: the in-app `feeds run`).
- Weekly (Todo #128 rethink) renders `components/TelegramToday.jsx`: title row **Channels** (Fraunces light) + ONE Forest **Check channels** button (`TelegramRunButton.jsx` `RunButton`; until the collection consent is given it reads **Allow & check channels** and a one-paragraph consent line sits below it; `useTelegramRun.js` polls `GET /telegram/status` and starts `POST /telegram/runs`), then ONE quiet line «Updated 12 min ago · 23 channels · Sources ⓘ» — the ⓘ disclosure holds the scope/not-fact-checked note and the marker legend (no other caption lines). `RunStatus` shows the 3-segment `Step n of 3` progress and English error/partial panels from `telegramRunText.js` (the `zotero-summarizer research-feed telegram-auth` command only for a missing/unauthorized session). **Trends** (`TelegramTrends.jsx`, collapsible, no outer box): hairline-divided rows — topic label (chat language) · one-line gist · marker pill (**↗ more discussion** / **new** / **hot thread**, tooltip = meaning; «steady» has none); a row with ideas/examples opens to ideas + post counts + channel links. **Discussions** (`TelegramPost.jsx`): Linen-white 14 px hairline cards — ONE headline (first line of the post, cut at a word boundary ≤110 chars), 2–3 terse bullets in the thread's language (citations `[c…]`/list markers stripped), a small count chevron after each bullet expands the comments it cites (`TelegramThread.jsx` fetches them when opened — `GET /telegram/thread/{source_id}`, cached per post; the digest carries only the points: full text in thread order, a reply shows its parent's first words), a «Continues @channel: …» line above a series continuation, «Also in: topic · n channels», meta line «@channel · 3 d ago · 76 comments», quiet **Highlights (n)** / **All comments (n)** / **Details** disclosures (rest of the text, link, one **Your feedback** group, see below). **Past digests** (`TelegramHistory.jsx`, `?past=list` / `?past=<run_id>`, linked from the status line): runs by date with a second line (first trend label or busiest channel) + chevron · discussions · unread, **Export feedback**, and one saved run (note «Marks and feedback apply to the discussion everywhere.») rendered by the shared `TelegramFeed.jsx` (trends + discussions, unread first, read ones behind a collapsed **n read**, **Mark all read (n)** → «Marked n read · Undo» for 10 s). `TelegramPost` has **Done** (collapses to one line with its feedback state, e.g. «marked useful» / «not a fit», Undo); under **Details** `PostFeedback.jsx` is ONE **Your feedback** group on one 13 px chip style: Useful / New to me / Want to try / Good discussion (`POST /telegram/feedback`, rolled back if saving fails) · **Not a fit** (`POST /telegram/reasons`, opens its optional reason chips; it and the positive chips exclude each other — picking one clears the other) · a note («Saved» clears after 3 s); Trends shows 5 with **Show all N trends**. **Saved digests** (options read «1 Nov 2026 – 9 Nov 2026 · saved 10 Nov, 22:33 · 8 cards», never the run slug) is a collapsed section at the bottom, hidden when there are none (open when `?run=` is set). A saved digest (`WeeklyDigest.jsx`, wording in `weeklyDigestText.js`) reads in plain words at display time, because stored digests keep the generator's text: date range «14–20 Oct 2026», «Worth a skim» (not `skim`), gate-only rationale «Picked by the relevance model alone…» (no raw scores/SHAP), snake_case evidence flags (`weak_evidence`, `uncertainty`, `no_leakage`…) as sentences, «Read in full text» instead of `Source basis: full_text · model: …` (the model name stays under **Technical details**), «Not checked» for unassessed lists, and an identical project-use sentence said once for all its projects. A Telegram post whose first line is a bare link takes its first text line as the headline (or «Link post from @channel»); the number after a bullet reads «2 comments». The block is capped at `max-w-3xl`; the old page title/«Read-only snapshots…» text is gone. `components/settings/TelegramSources.jsx` (Weekly → Sources) is English editing only: connection line, **Channels** (two-column hairline list, one compact row per channel + ✕, add field, scope/link edits behind **Edit**), **Summaries** (provider/model; a non-local provider needs an explicit **Allow sending text to …** consent, `POST /telegram/processing`; bounded limits), **Access** (the collection-consent checkbox). Saving sources without a model is allowed; limit fields clamp to sane ranges and surface human-readable `422`s via `humanizeError`. When unverified it shows one copyable `zotero-summarizer research-feed telegram-auth` command.
- Decision (Select & label default): the filter stays **Must read** (the owner's labelling queue), but the chips now start with **All**, the active chip carries a ✓ and a ring (`aria-pressed`), and the header/progress name the filter ("Must read: 143 of 160"). Revisit only if opening on All proves wanted.
- Paper figures: "+ Attach to Zotero" (`PaperReviewPage`) is hidden unless the render is completed and current — never while figures are outdated or rebuilding.
- Library Read next: the server sorts papers with the owner's own verdict (`decided`) after the undecided ones; `ReadNextView` states one set of counts (`components/library/readNextCopy.js` — to decide · decided · read hidden), the score age/staleness next to Rescore, and a divider before the decided group (default order only). "Cool papers" is called "top picks".

**Configuration writes**
- `components/settings/ConfigTask.jsx` captures the saved snapshot and its `_revision`, PUTs
  only the task's owned fields (`scopedFormStateToConfig` in `utils/configForm.js`), and
  rejects partial/malformed snapshots or ACKs instead of defaulting them.
- Uncertain or conflicting writes are never replayed or rebased on a fresh revision; the
  user reads back explicitly and compares. Unowned config branches round-trip untouched.
- Settings shows one task at a time (`pages/Settings.jsx`); there is no global Save.
- `StepModelsTable` (Settings overview + AI models task) is the one "which model does each step" table: saved routing + `/api/llm/step-history` (median time, last success from run logs) + `/api/admin/llm-reachability` (provider check) + the Test result shown in the tested row (`utils/stepModelStatus.js` verdicts). Goal lines are tidied for display only (`cleanGoal`) and edited one per row (add/remove, one Save, same `research_goals` write); the Settings overview shows the resolved Zotero path (`status.paths.zotero_data_dir.value`, "default location" when the env var is unset); Setup shows only the "All steps" model row (`StepModelsTable summary`) with per-step models under "Advanced", hides the app nav (`App.jsx`), reads "Step N of M" on phones and has a real Skip button; Setup warns while no Test has answered.
- **Review depth** (`settings/ReviewDepthChoice.jsx`, task `?task=depth`, saved through `ConfigTask` as `review_depth`): one Thorough (default) / Fast choice on the Processing card with the measured cost in plain words; Fast turns off digest reasoning and runs the quality rubric once, so reviews made under the other depth show as outdated.
- **Background processing** (`settings/BackgroundProcessingChoice.jsx`, task `?task=processing`, `ConfigTask`): four number fields — papers reviewed at once / calls per paper (`quality_review.fleet_papers`/`calls_per_paper`) and feed catch-up days / pages per feed (`feeds.backfill_days`/`max_pages_per_feed`). Ranges live in `PROCESSING_KNOBS` (`utils/configForm.js`) and mirror the backend models; out-of-range blocks Save. The backend persists only these fields (`models.config.USER_KNOBS`) when non-default.
- Connection changes belong to `ConnectionTask` (`components/settings/`), `useConnectionTask`
  and `api/connectionsApi.js`; Setup reuses the same task. The hook's `publish` fences both
  `llm-connections` and `runtime-config` revisions, retires obsolete work and rereads; a
  mismatched revision invalidates instead of adopting. Malformed ACKs stay unconfirmed
  (`requireReceipt`) and offer readback, not a retry.
- Doctor state is owned per QueryClient by `utils/setupDoctorState.js`; config/RSS writes
  report outcomes there so a stale Ready cannot survive an edit.

**Offline / PWA**
- One IndexedDB database (`offlineStore.js`: `papers`, `mutations` = outbox, `meta`) and one
  typed sync protocol (`syncClient.js`, protocol 1). Pull replaces the snapshot atomically;
  push sends ordered batches with predecessor UUIDs; unknown ACKs never retire rows.
- Local capture happens only on fetch transport failure or an already-reported outage.
  HTTP refusals, malformed ACKs and decode failures after a response stay errors.
- `api/offlineTransport.js` bounds offline-capable requests with build-time
  `VITE_SYNC_TIMEOUT_MS` (default 15000, max 60000). Server-only features (PDF, AI,
  annotations, native Zotero) gate on `hooks/useServerAvailable.js`.
- Header counts are two different queues: "N pending changes" = Zotero changes awaiting approval
  (server, same as Ops → Pending changes; the Pending page refreshes it after Apply/Reject via `notifyPendingChanged`); "N unsent on this device" = the offline outbox. The
  outage message lists what still works (synced papers, local verdicts/notes) and what does not
  (Today decisions, PDFs, AI, rescoring); install copy assumes the Tailscale HTTPS address.
- Ops lists stay compact: Pending changes show one line per change (paper title — «Untitled paper» if unknown, never the Zotero key —, plain action label such as «Add or update note», target) with
  payload JSON/audit/editor under "Details"; Triage jobs hide finished jobs over 7 days behind a toggle;
  System → Retrain asks first (`components/RetrainConfirm.jsx`: what is replaced, that the previous model is snapshotted and restorable with `goldenset model-history`/`restore-model`, 1–5 min); a note generated for another Zotero library says so instead of «reselect»; the Gate-rejected pile explains once that only «Remove» can be set without a review and shows the feed name and `Score`; Setup health names checks in words (`CHECK_LABELS`); `/ops?task=`
  alone opens System. RSS rows (`settings/RssFeedRow.jsx`) show name · host (full URL on hover/Edit) · last check · item
  count and a plain-language reading of the last error (`plainFeedIssue`; raw text on hover), with Delete in a "⋯" menu (still confirms).
- Never expose `/api/sync` as unauthenticated LAN HTTP.

## Extension points

- New backend call: add a function to the matching `api/*Api.js` using `request`; validate
  the receipt for writes and keep tests beside the wrapper.
- Loading/error/empty panes: `components/ui/Async.jsx`; status colors: `Badge`/`CHIP_TONE`
  and the palette in [DESIGN.md](DESIGN.md); form controls: `components/form/Fields.jsx`.
- New Settings task: add it to `pages/Settings.jsx`, wrap in `TaskPage`, and, if it writes
  config, reuse `ConfigTask`'s `TASKS` spec rather than a separate PUT path.
- Optimistic writes: `hooks/useOptimisticAction.js`; list shortcuts: `hooks/useKeyboardNav.js`
  (ignores interactive controls).
- Features with browser evidence are documented with their code/test links in
  [docs/features.md](../docs/features.md); update the matching paragraph with the change.
- Search / Find similar (#250): result chips use plain labels (`Crossref`, `Published article`, `needs checking` + one-line legend), abstracts go through `cleanAbstract`, the header row wraps at 390 px, a saved session fills the query box, the discovery status line is plain words (`STATUS_LINE`), and Related shows its own «Related in my library» heading and «Comments would be sent to: <provider>» only as the consent target.
- Paper page and Today in plain words (#252): Today's first load says «Loading today's papers…» (not «Selecting…»); a paper opened from the Today shelf keeps «← Back to Today» (other entries say «← Back»); stale-status states read «Updating status…»; the unknown note-delivery state reads «Not yet saved to Zotero.»; red-flag lists drop near-identical variants at display (`briefModel.dedupeFlags`, negation must match); dataset checklist keys and standard names, the method line and the FLAG/NEUTRAL/HIGHLIGHT legend use plain sentences; the phone sticky bar names the verdict like the rail (`pretty()`: «Yours: Should read»); the phone page keeps `pb-44` so the sticky verdict bar never covers text.
- Library, Setup and connection approval in plain words (#253): `cleanAbstract` turns block ends (`</p>`) into a space (feed text `…-5</a></p>An agentic…` was glued); the Select & label chip says «Not interested» and the active-learning chip «Papers the model is unsure about»; the score-band formula sits behind «About the priority bins»; `/setup` on a configured install preselects the current mode, opens with «Already set up», and credits steps as done (Zotero step only when the DB is found). `settings/ConnectionApproval.jsx` shows «Approve this connection» at the top of AI models when a stage reports `provider_approval_required`; it calls the same register flow as Change → Load models (`approved: true`, no key typed or shown), one provider per click.
