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
  components/NavBar.jsx + OfflineStatus.jsx (conflicts name the cached paper title, or “this paper” when none; Keep mine/Use server re-read the open paper) + ConnectionGuide.jsx
                      app chrome: ≥44 px tabs (below 640 px one row `Today · Library · Search · Weekly · Conferences` plus `⋯` for Settings and Ops — Esc closes it and returns focus; from 640 px all seven links inline) and one status line (sync state · `/api/pending/count`
                      via hooks/usePendingCount.js · unsent device outbox); details/Sync now/install
                      guide collapse, and open by themselves on outage, error or conflict
  pages/              route orchestration: Library(+ReadNext/AnnotationVerdict batch mode), Search,
                      Today, Weekly, Conferences, Settings, Ops (Feed review / Triage jobs / Pending changes / System tabs),
                      PaperReviewPage (/paper/:itemKey), SetupFlow (/setup); *Helpers.js beside pages
  components/         shared UI and workflows by domain: paper/ (review, RelatedPapers), conferences/ (EditionPanel: Update + preflight, ImportBar file import, «title only» badge, Late-breaking, paged All abstracts, lazy abstract), library/,
                      today/, search/, settings/ (task pages), setup/, form/Fields, ui/ primitives
  hooks/              usePaperReview, useDeepReviewRunner, useConnectionTask, useTodayVisit,
                      useReviewCoolLoop, useServerAvailable, useKeyboardNav, useOptimisticAction, …
  api/                one thin wrapper per backend area on api/client.js `request` (quietly retries a 503 `database_busy` ≤2× for GETs and `idempotent: true` writes)
  utils/              pure helpers: configForm, settingsNavigation, todayVisit, relevanceBands,
                      setupDoctorState, safeStorage, humanizeError, searchSort, readNextOrder
  offlineStore.js     IndexedDB papers / mutations (outbox) / meta
  syncClient.js       typed sync protocol over /api/sync/push|pull
  searchPolling.js, questionSubmission.js
public/               sw.js (app-shell cache; never caches /api), manifest.webmanifest
```

Routes: `/` → `/today` (default), `/library` (`?mode=batch`), `/search`, `/weekly`, `/conferences`, `/settings`,
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
  (verdict/review finished elsewhere, Refresh) shows "Checking…"/disabled until the reply. A review already seen stays ready during that refresh (`reviewReady`); the "needs a review" hint needs `reviewKnown`.
  A verdict saved on the paper page opened from Today is written into the retained visit and the cached members reply at once (`finishTodayReviewAction`), so the card shows "Decision: X" on return; a skip of a feed-only paper (`feed:…`) does not call reject-tag.
- Today per-card decision (#37): `PaperCard` shows must/should/could/Not interested with undo and an
  optional one-tap reason chip; it posts to `POST /api/daily/verdict` (undo: `/verdict/undo`)
  and reflects the saved verdict from the member facts. The bulk Add / Not interested bar appears only
  once ≥1 card is ticked (no always-on 210 px toolbar). **Owner decision (supersedes "keep
  `require_review` current-only"):** must/should/could (and bulk Add) still require a full-text
  review, but a current **or** outdated-same-PDF review now counts — the `require_review`
  service gate guards a write to the owner's real Zotero, so it is KEPT; an outdated-but-valid
  review keeps the card recommended with a “review outdated” badge and enabled buttons, while a
  missing/invalid/`pdf_changed` review still shows the "needs a review first — Generate" note,
  and Not interested is always available.
  Reason chips are never pre-ticked and are captured per item, never in a batch.
- Card on Today + paper page (#98, redesigned #124, English + terse TL;DR #127): `components/card/PaperFactsCard.jsx`
  (+ `AiSummary.jsx`, `FactStrip.jsx`, pure `cardModel.js`, `api/cardApi.js`) renders `GET /api/card/{key}` under the
  title. Order: **AI TL;DR** → **fact icon row** → one quiet who-is-behind line. The TL;DR is ≤4 labelled one-line
  bullets (`New` / `Take` / `Caveat`; `card.ai.bullets`, never prose — a card without `bullets` is treated as
  not generated) in a label column + text; the `Caveat` label is ochre. The server's `Code` bullet is not shown (#308: it
  repeated the code mark). On the paper page, where a review exists, a last `Not found` line (`cardModel.notFoundLine`)
  names what is absent in the card facts AND answered "no" in the review checklist (`quality.rubric`); Today cards have no such line. Facts are 10 small icons + the code mark from the shared
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
- Today header line «N papers waiting for Zotero sync» (`components/today/ZoteroSyncLine.jsx`): reads `stats.unsynced_items` of `GET /api/zotero/status`; shown only when N>0, silent on error or a missing key, re-read after an Add finishes. Not the app's own pending Zotero exports.
- Today "Not reviewed yet (N)" shelf: `components/today/NotReviewedShelf.jsx` under the cards, fed by the
  `unreviewed` field of `/api/daily` kept in the retained visit (`todayVisit.js`). One tap row per paper
  (title, source, ≤2 why chips, cached TL;DR bullets) → `/paper/<key>`; no verdict/Add controls.
- Today page (#124): one ≤760 px reading column (the cards are a one-at-a-time cull queue, so no master/list);
  header = title + ONE Forest **New shortlist** + **More** (Refresh status, Triage backlog, Article selection,
  RSS sources, Telegram today; it opens by itself while triage runs or fails). Card order: pills + quiet *Source ↗* → headline (a link to the full review at
  `/paper/<key>`, feed or Zotero key) → AI TL;DR + icons + who → ONE why line → verdict buttons → “more” (authors, venue,
  abstract, icon legend, selection details). Verdict tints (`decisionVocab.js` `DECISION_TINT`): must Forest solid,
  should Sage, could Mint, Not interested hairline outline; the chosen one shows ✓ + Forest border. Buttons are compact
  content-sized pills grouped left (`DECISION_BTN`: 40 px on phones with a 44 px tap target via an invisible
  `::before`, 32 px on desktop) — the same look on the paper-page sticky bar and the rail's `VerdictPanel` edit row. Loading shows card skeletons.
  **New shortlist** shows a pending state, then one result line under the header («N new papers» /
  «Same papers — nothing new since <time>», client-side id compare; the visit stores `createdAt`); an outdated card's
  *Regenerating · step · elapsed · #N in queue* reads the runner's 3 s status poll and flips to the new review when
  the members refetch ends.
- **Language rule (#127, supersedes #108):** the whole UI is English — chrome, buttons (`must/should/could/Not interested`,
  `Undo`), reason prompt + chips (`components/decisionVocab.js`; `NEG_CHIPS` and the shared chip style `chipClass` live there), the one-line “why selected”,
  tooltips, errors, empty states and the AI TL;DR itself. **Only exception: Telegram post/discussion summaries and
  trend labels stay in the language of the source chats** (they summarise that text); Telegram chrome is English too.
  Stored paper content is shown as stored. New strings follow this split.
- Today card leftovers (#108): the selection explanation is ONE plain line (`Close to your goal “…” · …`) with
  the honesty facts (policy, keys, review evidence) behind “more”; the review chip is hidden when the
  evidence is unknown/unrecorded (the detail stays in “more”); once the card has an AI TL;DR the abstract
  collapses (`abstract`); `Random check` uses the solid badge style of the relevance badges.
- Paper page decision (#94): the verdict (`components/paper/review/PaperVerdict.jsx`, shared
  by the desktop rail and the mobile sticky bar) uses the same must/should/could/Not interested vocabulary
  and one-tap reason chips as Today; a resting verdict reads `Yours:`/`Auto:` by its `source`.
  Auto deep review on open is opt-in: `useDeepReviewRunner` only auto-fires when
  `GET /api/paper/prefs` reports `auto_review:true` (`ZS_PAPER_AUTO_REVIEW`, default OFF).
  `usePaperReview` fetches `review-detail` once per open. Related-in-library sits below the
  abstract, collapsed.
- Paper page layout (#125): the whole page is a centred `max-w-[76rem]` frame (TOC / reading column / rail),
  with one ≤68ch reading column inside it. Order: title/authors/links →
  ONE at-a-glance card (#308, `review/Recommendation.jsx`, the same on the paper page, the Library detail and
  Annotate: verdict chip, minutes → what the paper does (`digest.tldr`) + the one-line `digest.verdict` →
  `review/Glance.jsx`: Rigor tile (the A–D letter is shown as «Rigor», code names stay `quality`; grade letter, «n of m checks», one segment per check, the model's notes count; letter rule D below), Metrics tile
  («as reported»: the first `digest.parameters.metrics` string leads with its number large and the full string under it;
  a bar/pair/range is drawn by `review/metricModel.js` only when that number is the only one in the string), one
  strength and one weakness → the why → `ReadingPlan` «Read first / Skip / What the original adds» → policy reasons → code row
  `review/CodeLink.jsx`) → `PaperFactsCard` → `Fold` sections (`review/primitives.jsx`: serif title + one-line summary while
  closed). Order: Findings, **Assessment** (`review/StudyMap.jsx`: Data → Method → Compared with → External validation
  rail, then Impact / Industry / Academia / Unknowns, then the remaining digest rows) and Caveats and coverage (notes =
  slate left rule «Notes to verify (N)», clay «Red flags» only when the band is flag; quiet «verify» badge) start open; Evidence, Relevance, Methods and limitations, **Reference** (summary,
  checklist, source basis), Figures, Paper map, Abstract and Related are closed folds with their content in the DOM. The
  Library detail shows the same card and keeps everything else behind one «Details». The closed **Evidence** fold (`review/EvidenceFold.jsx`, #40) reads `GET /api/library/review-audit/{key}` and lists, per verified claim (Contribution, Findings), the verbatim quotes from the paper (the best-matching sentence ±1 of the verified passage + «Show full passage»; «…» marks an excerpt); `not_established_fields` show as «could not be grounded this time»; legacy / stale / PDF-changed states are one plain sentence; older-rules audits carry a note. An outdated review is one line +
  `Regenerate` (`onRegenerate` → `runner.run()`). The mobile verdict bar uses graded tints (must Forest
  solid, should Sage, could Mint, Not interested outline), no black fills.
  **Give feedback** (`review/FeedbackMode.jsx`, mounted in `PaperReviewPage` `ReviewZone` with `key={itemKey|reviewed_at}`): one
  button above the review turns on a mode in which every part of the review carrying `data-feedback-field="<id>"` (ids and
  labels: `review/reviewFeedbackFields.js`; `Fold` takes `feedbackField`) gets an outlined overlay button laid over it (not
  wrapped around it, so no control nests in another). The review is `inert` while the mode is on, closed folds are opened,
  the phone verdict bar is hidden (`hidden` wrapper, not unmounted) and a sticky bar offers **Done** and **Whole review**.
  A tap opens `FeedbackPanel.jsx` (non-modal `dialog`: bottom sheet on phones, a card beside the part from `lg`): the 8
  shared `chipClass` reasons (Good as is excludes the problems), a note ≤1000 and **Save** / **Withdraw** (`PUT`/`DELETE
  …/parts/{field}` in `libraryApi.js`; the server records the shown text). Esc closes the panel, then the mode; focus returns
  to the part. `ReviewFeedback.jsx` lists what was said below the review («Your feedback on this review (N)», Edit /
  Withdraw, and «Checked: …» from `check.json`); with nothing saved nothing renders.
- Today exposure (card shown/dwell, review and link opens) is reported best-effort to
  `/api/daily/events` by `hooks/useCardExposure.js` and `utils/todayEvents.js`, keyed by
  the retained visit id (= the logged `slate_id`); a failed send is dropped. Cards with
  `role: "exploration"` are marked "Random check".
- The client does not recompute server policy: verdict/action, reading plan and scientific
  grade are rendered from the server projection (`digest.grade`, projected decision).
  Every printed letter carries what it rests on (`tones.js` `qualityBasis`/`gradeBasis`): `A · 3 of 5 checks`
  from stored `coverage_met`/`coverage_applicable` (old reviews → letter only).
  Letter rule D (`tones.js` `letterMuted`/`gradeTone(grade, band)`/`mutedReason`, a pure function of stored fields; the letter's meaning is unchanged): an **A** is green only when `quality_band` is `highlight` («no critical gap found»), otherwise slate grey with «checklist only — <one reason>» (runs disagreed → critical gap(s) by plain label → automatic warning → not enough to judge when no band is stored); B/C/D keep their colour. The Quality tile has a real `?` button (`aria-expanded`, 44 px hit area) that opens the one-line legend; the Library row chip and the Today chip only carry the reason in `title`. Model notes read «Notes to verify (N)» (neutral) and turn into the red «N red flags to verify» only at band flag. `briefModel.js` `rubricLabel` has plain labels for all 28 critical checklist items (the brief keeps the raw key).
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
- Page headers: `components/ui/PageTitle.jsx` is the one title style (Library, Search, Weekly; routes unchanged); it takes an `actions` slot (Library puts the `Read next` / `My verdicts` tabs at the right of the title) and `phoneSubtitle={false}` hides the purpose line below 640 px; every routed page owns exactly one `<h1>` (`PageTitle` or the page's own heading) and holds the one-line purpose of Today / Library / Search / Weekly (`PAGE_PURPOSE`; Today's owner can adopt it). Read next's selection bar adds `BulkVerdictBar` — per-paper verdict (dont_read also queues ❌) and "Mark read" (the 👀 skimmed tag; the paper leaves the queue), stopping at the first failure with "N of M" — next to the existing Run triage / Add to collection. Empty Ops Feed review offers `FeedReviewEmpty` (fetch RSS, then score the backlog: the in-app `feeds run`).
- Weekly (Todo #128 rethink) renders `components/TelegramToday.jsx`: title row **Channels** (Fraunces light) + ONE Forest **Check channels** button (`TelegramRunButton.jsx` `RunButton`; until the collection consent is given it reads **Allow & check channels** and a one-paragraph consent line sits below it; `useTelegramRun.js` polls `GET /telegram/status` and starts `POST /telegram/runs`), then ONE quiet line «Updated 12 min ago · 23 channels · Sources ⓘ» — the ⓘ disclosure holds the scope/not-fact-checked note and the marker legend (no other caption lines). `RunStatus` shows the 3-segment `Step n of 3` progress and English error/partial panels from `telegramRunText.js` (the `zotero-summarizer research-feed telegram-auth` command only for a missing/unauthorized session). **Trends** (`TelegramTrends.jsx`, collapsible, no outer box): hairline-divided rows — topic label (chat language) · one-line gist · marker pill (**↗ more discussion** / **new** / **hot thread**, tooltip = meaning; «steady» has none); every row carries ONE always-visible provenance line under the gist («12 posts in 6 channels this week (11 the week before) · @a, @b, @c and 3 more», from `posts`/`baseline_posts`/`channels`/`channel_count`); a row with ideas/posts opens to the ideas, «K of N posts have a discussion card» (N = `source_ids`; «No discussion card for this trend yet. At each check, comments are collected only for posts with the most replies and the closest to your goals. Open the posts in Telegram to see if there is a discussion.» when K is 0) and the matching cards as headline links (`TelegramFeed` joins `topic.source_ids` with the digest's `posts`; a click reveals «n read» when the card is Done, scrolls to `#tg-post-<source_id>` and focuses it — the anchor `id`/`tabIndex=-1` is on both `TelegramPost` variants), then the other ≤3 `examples` as Telegram links, then **Load discussions** (`TelegramLoadDiscussions.jsx`, only on the live page: `topic.discussion` comes from `GET /telegram/today`, absent in saved digests): one secondary button, shown when ≥1 post of the trend had replies when last checked (else «No post in this trend had replies when last checked.»); each press asks `window.confirm` «Read posts and comments of N posts through your Telegram account? Nothing is sent.» and posts `POST /telegram/discussions {topic_id}` (≤12 posts, no model; `useTelegramRun().load`); it is disabled while any Telegram action runs or before the collection consent («Press “Allow & check channels” first.»), shows «Loading 3 of 9 posts…» in place (`telegramRunText.loadingText`; the top button reads «Loading discussions…» and `progressText` says the same), and tells «Telegram asked to wait until HH:MM» / the auth hint beside the button (`loadNote`; the top `RunStatus` stays quiet for this kind). Comments land in a collapsed **Discussions · n posts · m comments** block (posts without a card; `TelegramThread` loads each thread when the block opens, «Telegram ↗» per comment, none for private groups; open threads refetch when a run ends); digests saved before `source_ids` show the line only. **Discussions** (`TelegramPost.jsx`): Linen-white 14 px hairline cards — ONE headline (first line of the post, cut at a word boundary ≤110 chars), 2–3 terse bullets in the thread's language (citations `[c…]`/list markers stripped), a small count chevron after each bullet expands the comments it cites (`TelegramThread.jsx` fetches them when opened — `GET /telegram/thread/{source_id}`, cached per post; the digest carries only the points: full text in thread order, a reply shows its parent's first words), a «Continues @channel: …» line above a series continuation, «Also in: topic · n channels», meta line «@channel · 3 d ago · Open in Telegram» (the stored post permalink, http(s)-guarded, absent for digests saved without one; the 76-comments count reads once, as the **All comments** label), a «Telegram ↗» link under each comment in `TelegramThread` when its `permalink` is set (public discussion groups only), quiet **Highlights (n)** / **All comments (n)** / **Details** disclosures (rest of the text, one **Your feedback** group, see below). **Past digests** (`TelegramHistory.jsx`, `?past=list` / `?past=<run_id>`, linked from the status line): runs by date with a second line (first trend label or busiest channel) + chevron · discussions · unread, **Export feedback**, and one saved run (note «Marks and feedback apply to the discussion everywhere.») rendered by the shared `TelegramFeed.jsx` (trends + discussions, unread first, read ones behind a collapsed **n read**, **Mark all read (n)** → «Marked n read · Undo» for 10 s). `TelegramPost` has **Done** (collapses to one line with its feedback state, e.g. «marked useful» / «not a fit», Undo); under **Details** `PostFeedback.jsx` is ONE **Your feedback** group on one 13 px chip style: Useful / New to me / Want to try / Good discussion (`POST /telegram/feedback`, rolled back if saving fails) · **Not a fit** (`POST /telegram/reasons`, opens its optional reason chips; it and the positive chips exclude each other — picking one clears the other) · a note («Saved» clears after 3 s); Trends shows 5 with **Show all N trends**. **Saved digests** (options read «1 Nov 2026 – 9 Nov 2026 · saved 10 Nov, 22:33 · 8 cards», never the run slug) is a collapsed section at the bottom, hidden when there are none (open when `?run=` is set). A saved digest (`WeeklyDigest.jsx`, wording in `weeklyDigestText.js`) reads in plain words at display time, because stored digests keep the generator's text: date range «14–20 Oct 2026», «Worth a skim» (not `skim`), gate-only rationale «Picked by the relevance model alone…» (no raw scores/SHAP), snake_case evidence flags (`weak_evidence`, `uncertainty`, `no_leakage`…) as sentences, «Read in full text» instead of `Source basis: full_text · model: …` (the model name stays under **Technical details**), «Not checked» for unassessed lists, snake_case reproduction inputs and the reproducibility rationale spelled out («Dataset provenance»; Markdown export too), «1 card» singular, Not-a-fit reason chips capitalised like the positive ones, RSS rows «Last checked: 9 Nov, 11:07» (local time), and an identical project-use sentence said once for all its projects. A Telegram post whose first line is a bare link takes its first text line as the headline (or «Link post from @channel»); the number after a bullet reads «2 comments». The block is capped at `max-w-3xl`; the old page title/«Read-only snapshots…» text is gone. `components/settings/TelegramSources.jsx` (Weekly → Sources) is English editing only: connection line, **Channels** (two-column hairline list, one compact row per channel + ✕, add field, scope/link edits behind **Edit**), **Summaries** (provider/model; a non-local provider needs an explicit **Allow sending text to …** consent, `POST /telegram/processing`; bounded limits), **Access** (the collection-consent checkbox; the line below it names the button as it currently reads, «Allow & check channels» until consent is given). Saving sources without a model is allowed; limit fields clamp to sane ranges and surface human-readable `422`s via `humanizeError`. When unverified it shows one copyable `zotero-summarizer research-feed telegram-auth` command.
- Decision (My verdicts default): the filter stays **Must read** (the owner's labelling queue), but the chips now start with **All**, the active chip carries a ✓ and a ring (`aria-pressed`), and the header/progress name the filter ("Must read: 143 of 160"). Revisit only if opening on All proves wanted.
- Paper figures: "+ Attach to Zotero" (`PaperReviewPage`) is hidden unless the render is completed and current — never while figures are outdated or rebuilding.
- Library tabs: `Read next` and `My verdicts` (`?mode=batch`). My verdicts' filter pills use the shared decision vocabulary (`must · should · could · Not interested`); a verdict there (panel Save or keys 1–4) goes through `utils/verdictUndo` with `surface: library_verdicts` and shows an 8 s Undo toast (`z` undoes it); overwriting needs no confirm, and the ❌ Zotero tag is never queued from this tab (`verdictUndo.decide` takes `rejectTag = true`; this tab passes `rejectTag:false`). There is no Why filter and no `w` URL key (an old `w=` link is ignored). `All reviewed` is hidden when no top picks are pending. «Cool papers» are called top picks.
- One status row sits under the title (`LibraryStatusLine`, chosen by `libraryStatus()` in `components/library/libraryStatus.js`): offline → load error → scoring failed → connecting/slow → scoring runs → action receipt → summary (`Scores updated … · N not scored · N Zotero changes to apply ›`, `not scored` counted over the whole queue) → `Scores up to date`. It is one sentence and at most one action; errors are `role="alert"` and say «Error:», warnings say «Warning:». Empty lists (`emptyState()`) are filters / search / collection / not scored / all caught up / no papers, never shown while loading, failed, offline or scoring; while the device copy shows during the first request the line reads `Connecting to your Mac…`.
- `components/library/LibraryJobsPanel.jsx` is the closed «Library jobs» control: a button (labelled «Jobs» below 640 px, accessible name «Library jobs») at the right edge of the status row that opens the panel under that row: Scores (Rescore), Zotero (`Send to Zotero…`, `Fetch missing PDFs…`, `N Zotero changes to apply ›` → Ops → Pending), Top-pick reviews (hidden when nothing is to review; a running review opens it). A job's result is the status-line receipt; there is no bottom banner, and opening the page resumes a running full-text fetch (one GET of `fetch-fulltext/status`). Rescore is not in the toolbar.
- Zotero writes on Read next never use `window.confirm`: `Send to Zotero…` and `Fetch missing PDFs…` always open a bottom `Sheet` first (`ConfirmSheet`, via `hooks/useConfirmSheet`; wording in `components/library/zoteroCopy.js`: a paper count only for an unscoped list, one backup line); a running Zotero opens a second sheet «Zotero is running…» (also for add-to-collection and bulk mark-read); Select → Add to collection and Mark read open a confirmation sheet first (count + backup line, via `zoteroCopy.js`) and Cancel sends nothing; `BulkVerdictBar` asks the same way for Not interested and stops at the first failure with «N of M». The Send receipt shows `backup_path`, `failed_count` and `skipped_user_callnumber` and has the warning tone (amber, «Warning:») when `failed_count>0`.
- `hooks/useLibraryFilters.js` owns the Library filters, sort, search text and Show read and mirrors them into the URL (`c t text rd b pr g q vn s sd code`; the retired `w` and unknown keys are ignored). `components/library/AppliedFilters.jsx` shows applied filters as removable chips with one `Clear all`; a `Go` button sits next to the search.
- The `Filter N` button (beside Search) opens `LibraryFilterSheet` on `Sheet` (`full` below 1024 px, `side` from 1024 px): Collection and Tag (`FilterPicker`: search in the list, emoji tags first, a pick applies at once), Rigor / Goals / Code / `Model guess` chips with counts (empty bands hidden unless applied) and a `Not reviewed` bucket for Rigor and Code, Journal, `More` → Prestige, `Show read papers (N)`; the `Show papers` button reads `Show N papers` only after a client-side facet changed. URL key `code` = `found|none|nr`; Rigor `q` also takes `nr`.
- Library Read next, default order (no search, Best match): `ReadNextView` groups the list on the client from each row's own verdict (`groupRows` in `components/library/readNextCopy.js`, so the offline set groups too): `YOUR MUST READ · N` (3 rows, 2 below 640 px, + `Show all N`), `TO DECIDE · N`, then a collapsed `Decided by you · N · Show` block; numbers appear only for a live answer. Any other sort or a search is a flat list with a `You:` chip. A row has no ℹ or `Why here` button: `Why it's here` is a disclosure inside the paper detail, and a proposed verdict is a quiet chip `◇ Review suggests: X · flagged|NN%` (no Confirm/Override card). Meaning search shows `Top 50 by meaning for “q”` when the backend cap of 50 rows is reached, and `N of 50 match filters` while client-side filters are on.
- A Read next row is one memoised `ReadNextRow`: title → signal line (★ · `Goals strong` · Rigor · `Code` · `Skim/Read N min`) → `AI summary` (review TL;DR) or the abstract clip (3 lines <640 px, 2 ≥640) → footer (authors · venue · year · PDF) → one state label (`You: …` or `◇ Review suggests: …`). Decided rows are compact. Rigor reads `Rigor A` (highlight), `Rigor A · checklist only`, `Rigor X · flagged` or `Not reviewed`; `Goals strong` shows only on the full list (no collection, tag or search; not offline). The TL;DR, read decision, minutes and code flag come from the queue row's `review_digest{tldr}`, `read_decision`, `estimated_read_minutes`, `has_code` (null without a review; the offline snapshot carries the last three).
- Library detail: a tap on a Read next row opens the paper in `LibraryDetail`: a full-screen `Sheet` over the still-mounted list below 1024 px, the right half of a split view above it. The open paper is the URL key `?open=KEY`, owned by `LibraryReadNext` through `hooks/useOpenPaper` (first open adds a history entry, later papers replace it; Close goes back for an entry this page added, otherwise drops `open` in place). ‹ › and «N of M» walk the undecided rows of the displayed list; a key outside the list opens without ‹ ›. Any exit with an unsaved note asks inside the detail (Keep editing / Leave); the system Back button closes the detail without that question. Opening makes only `GET review-detail` and the status reads: no POST and no artifact build. A paper with no review digest shows its abstract and «No review yet». Entering with `?open=KEY` (a link, a reload, Back from the full review) renders the row — past the first 60, inside the Must/Decided groups — and scrolls it into the top third of the screen when it is off-screen; a key not in the loaded list does nothing. Closing the detail uses the same reveal and also focuses the row. `Open full review ↗` is a same-tab link to `/paper/KEY?autoRun=false` (state `{from}`); it never builds the artifact or POSTs. The paper page's `← Back` is `navigate(-1)` when `state.from` is present (Today excluded), otherwise `/library`.
- `components/library/VerdictBar.jsx`: the open paper's verdict pills (must / should / could / Not interested); sticky at the bottom of the sheet, first row of the split view; hidden while a text field in the detail has focus; ignores taps for 500 ms after the open paper changes. `UndoToast.jsx`: `Not interested: “Title” [Undo]`, a reason-chips row for Not interested, a Retry failure variant; 8 s. `hooks/useLibraryVerdict.js`: write via `utils/verdictUndo`, patch the row in place, advance to the next undecided paper (`All caught up in this list` at the end); the queue is re-read once on detail close / 30 s idle / window focus. `surface`: `library_sheet` below 1024 px, `library_split` above, `library_bulk` for `BulkVerdictBar` (one Undo per batch). The verdict comment is the `Verdict comment` disclosure in the detail.
- Keys in Read next: `j`/`k` move, `1`–`4` verdict (same path as the pills), `z` undo, `Enter` open, `Esc` close the detail (never clears the search text), `/` focus search; silent while typing in a field; auto-repeat is ignored except for `j`/`k`; a hint line shows at ≥1024 px. `hooks/useKeyboardNav` ignores all keys while a modal dialog (`[role=dialog][aria-modal=true]`) is open, except one that contains the `allowModal` selector (the Library detail sheet). It takes optional `allowFrom`, `onEnter`/`onEscape`/`onUndo`/`onSlash`.
- `components/ui/Sheet.jsx` is the modal-surface primitive (`variant` full | side | bottom): `role=dialog aria-modal` named by its title, focus to the title and back to the opener, Esc closes, Tab trapped, the rest of `<body>` `inert` and not scrollable, `100dvh` and `env(safe-area-inset-bottom)`; mounted only while open.
- `utils/verdictUndo.js` is the undoable verdict write path: per-paper serial writes; Undo restores the prior verdict or DELETEs; the ❌ tag request is sent 8 s after the verdict, cancelled by Undo, sent early on the next verdict or `pagehide`; reason chips go out as one extra POST when the window closes. `submitVerdict` and `usePaperReview` take an optional `surface` (sent to `POST /api/golden/verdict`; the server defaults it).
- The `dont_read` verdict is named `Not interested` on every surface (`utils/priorityLabels.js`, `components/decisionVocab.js`); the four verdict pills use `px-2` so they fit one row on a 390 px card.
- Library Read next **Order**: ONE `Order` list (`OrderControls.jsx` `OrderControl`): presets │ Zotero fields. Presets act when the list is search-free, online, and the response has `rank_coverage`; a field writes `s`/`sd` (direction arrow), hides the shares and the Custom sliders, and a preset returns to the ranked order. Disabled presets show their reason (`presetsOffReason` in `readNextCopy.js`); the ★ legend is in `ⓘ`; below 640 px the shares caption is inside `ⓘ` and Order · `ⓘ` · Select stay on one row. Show read lives only in the Filter sheet (URL `rd`). `utils/readNextOrder.js` holds the presets, the stored choice (`localStorage` `zs:readNextOrder`; unknown/broken value → Balanced), the key (Σ weight × part over the signals with data; an unknown part takes `rank_fill`) and `sortByPreset` (group ↑ → scored first → key ↓ → date added ↓; rows without `rank_group` stay last); Balanced returns the server order untouched. `OrderControls.jsx` (`OrderControl`, the Custom sliders and the shares caption) and `WhyHere.jsx` (the `Why it's here` disclosure in the paper detail: group, weight × part per signal, key, the model's main reason) render it; `LibraryReadNext` applies it before `zs.reviewOrder` is written, so Prev/Next follows the shown order.

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
  While a sync runs (`syncing`) the gate keeps the last settled status (the last phase other than `syncing`), so controls
  do not flip to "need the Mac" at every sync start; unavailable-control text (`ServerUnavailableNote`, search
  placeholder, PDF/figures notes, open-review titles) comes from `hooks/useUnavailableText.js`: "need the Mac" only when
  the Mac is unreachable, "The app is busy" when it answered, "Checking the connection…" before the first answer.
- A sync deadline with no answer, or a 502/503/504 (incl. `database_busy`), is phase `busy`, not
  `unavailable`: the header says "Server is busy — retrying…", `publishStatus` re-requests a sync
  (5 s, then 15 s) and recovery re-checks the AI model. "Server unreachable" is only a refused
  connection (fetch `TypeError`) or `navigator.onLine === false`, or 3 consecutive no-response SYNC timeouts
  with no HTTP response between (blackholed network: Mac asleep, VPN off — `publishNoResponse`; any HTTP
  response resets the count in `fetchTransport`; page-read timeouts only say `busy`; 502/503/504 stay `busy`). Today's "AI not connected" needs a successful
  model check with `reachable === false` (or disabled); an unknown state says nothing;
  the setup status check re-asks every 5 s after a failure and the gate renders children meanwhile.
- Header counts are two different queues: "N pending changes" = Zotero changes awaiting approval
  (server, same as Ops → Pending changes; the Pending page refreshes it after Apply/Reject via `notifyPendingChanged`); "N unsent on this device" = the offline outbox. The
  outage message lists what still works (synced papers, local verdicts/notes) and what does not
  (Today decisions, PDFs, AI, rescoring); install copy assumes the Tailscale HTTPS address.
- Ops lists stay compact (apply result reads «6 changes applied.» / «5 applied, 1 failed — see the Failed tab»; `appliedMessage`): Pending changes show one line per change (paper title — «Untitled paper» if unknown, never the Zotero key —, plain action label such as «Add or update note», target) with
  payload JSON/audit/editor under "Details"; Triage jobs hide finished jobs over 7 days behind a toggle;
  System → Retrain asks first (`components/RetrainConfirm.jsx`: what is replaced, that the previous model is snapshotted and restorable with `goldenset model-history`/`restore-model`, 1–5 min); a note generated for another Zotero library says so instead of «reselect»; the Gate-rejected pile explains once that only «Not interested» can be set without a review and shows the feed name and `Score`; Setup health names checks in words (`CHECK_LABELS`); `/ops?task=`
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
- Library, Setup and connection approval in plain words (#253): `cleanAbstract` turns block ends (`</p>`) into a space (feed text `…-5</a></p>An agentic…` was glued); the My verdicts chip says «Not interested» and the active-learning chip «Papers the model is unsure about»; the score-band formula sits behind «About the priority bins»; `/setup` on a configured install preselects the current mode, opens with «Already set up», and credits steps as done (Zotero step only when the DB is found). `ConnectionTask` says which saved connection «Load models» reuses (same address + authorization) and `useConnectionTask.publish` re-probes `llm-reachability` after every save while `StepModelsTable` shows «Checking provider…» (#281). `settings/ConnectionApproval.jsx` shows «Approve this connection» at the top of AI models when a stage reports `provider_approval_required`; it calls the same register flow as Change → Load models (`approved: true`, no key typed or shown), one provider per click.
- Remaining plain-words fixes (#270): `cleanAbstract` also drops a leading «Journal, Published online: …; doi:…» notice; one band vocabulary «Not interested» (score-band bar, My verdicts intro) and the bins line reads «Not interested below 2.0 · Could read 2.0–3.5 · …»; queued notes/verdicts say `QUEUED_FOR_ZOTERO` (`hooks/useNoteDelivery.js`) and a confirmed one «Saved to Zotero.»; the Evidence quote picker ranks licence/preprint boilerplate last; red-flag groups (`CritiqueViews`) are not formed when a remainder would start mid-sentence («for…», «a…»); the paper page suppresses the «Outdated review» line for the moment between a finished regeneration and the refreshed review; Ops → Pending folds identical older pending rows behind «N older versions» and shows «Queued/Applied 11 Nov, 05:59» in local time; the Gate-rejected pile shows «loading…» instead of «0 shown»; connections named `connection_<id>` are shown by address.
- Search clarity (#274): while reviewing, `Search.jsx` shows «Reviewed N of 10» from the session's `screened_count` (total = `min(10, candidates)`, = `LIGHT_REVIEW_N`) with the waiting sentence as sub-line, «Refining the search…» during refinement; `retrieval <source>` plan rows and discovery provider receipts render as plain lines (`retrievalLine`, `providerLine`) plus one «Some sources failed» line; `cleanAbstract` decodes entities (`decodeEntities`, also used for Search titles) and ends a heading with «: »; version and source are one chip.
