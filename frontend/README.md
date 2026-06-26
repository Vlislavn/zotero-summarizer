# Zotero Summarizer — Frontend (`/annotate`)

React 18 + Vite 5 + Tailwind 3 single-page tool served by FastAPI. The Library
surface owns the paper-read controls that build generated notes, figures, HTML
presentations, and paper Q&A through `/api/library/*`.

## Setup

```bash
cd frontend
npm install
```

First install takes ~30s. Subsequent installs are cached.

## Development

```bash
npm run dev
```

- Dev server runs at <http://localhost:5173>.
- Vite proxies `/api/*` to <http://localhost:8000> (the FastAPI backend) so
  the React app can hit the real endpoints without CORS gymnastics.
- Start the backend separately (`uvicorn zotero_summarizer.api:app --reload`
  or your usual command) before opening the dev server.

## Production build

```bash
npm run build
```

Output lands in `frontend/dist/` with `base: '/annotate/'` baked in, so the
asset URLs resolve when FastAPI serves the bundle.

### FastAPI integration

The parallel backend session wires this up; for reference it should:

1. Mount `frontend/dist/assets` as static at `/annotate/assets`.
2. Serve `frontend/dist/index.html` at `GET /annotate` (and `/annotate/`).

`vite preview` (`npm run preview`) gives a local sanity check of the built
bundle, but the canonical path is through FastAPI.

## Project structure

```
frontend/
  index.html                    Vite HTML entry
  vite.config.js                React plugin + /api proxy + base '/annotate/'
  tailwind.config.js            Tailwind content globs
  postcss.config.js             Tailwind + autoprefixer
  package.json                  Dependencies + scripts
  src/
    main.jsx                    createRoot + QueryClientProvider
    App.jsx                     Routes for Library / Today / Settings / Ops (+ legacy redirects)
    index.css                   Tailwind directives + .glass / .mono helpers
    api/
      goldenApi.js              fetch wrappers for /api/golden/*
      libraryApi.js             reading queue, paper-read build/status, Q&A, deep-review + review-fleet runs
      settingsApi.js            /api/config + /api/admin/* (exports request())
      setupApi.js               /api/setup/* (status, detect-zotero, paths, validate)
    hooks/
      useSetupStatus.js         single seam → setup-status + isConfigured + pillars
    utils/
      configForm.js             shared config<->form transforms (Settings + wizard)
    pages/
      Library.jsx               tab wrapper: Read next (default) + Batch label (?mode=batch)
      LibraryReadNext.jsx       Read-next queue and inline paper actions (the "Read next" mode)
      AnnotationVerdict.jsx     Batch-label body — two-column list+detail, 1–4 / j-k keyboard flow
      Ops.jsx                   tab wrapper: Feed review / Triage jobs / Pending changes
      Review.jsx                Feed-gate review queue (Ops "Feed review" tab)
      Triage.jsx                Triage job monitor + calibration (Ops "Triage jobs" tab)
      Pending.jsx               Pending Zotero changes (Ops "Pending changes" tab)
      Audit.jsx                 Re-label audit — de-linked from nav, still routable at /audit-page
      Settings.jsx              thin orchestrator: Essentials + Advanced + save bar
      SetupFlow.jsx             3-step first-run wizard orchestrator
    components/
      PaperListItem.jsx
      VerdictPanel.jsx            full verdict editor (comment + edit/delete); exports PRIORITIES
      VerdictPicker.jsx           one-click 4-priority row reusing PRIORITIES (Review + Audit relabel)
      ProvenanceBreakdown.jsx
      AnnotationsList.jsx
      NotesList.jsx
      TagsRow.jsx
      form/Fields.jsx              SectionCard/Field/CheckboxField/Banner primitives
      settings/                    ReadinessStrip, EssentialsSection, AdvancedSection,
                                   DefaultProviderField, ClassifierGateFields
      setup/                       SetupGate, StepProgress, Step{ConnectZotero,
                                   ConnectLlm,DescribeResearch,Done}, NotConfiguredCard
      library/PaperReaderPane.jsx  paper-read build/status controls + figure grid (inline card)
      library/PaperFigures.jsx     always-visible figure grid + lightbox (story page)
      library/AskPaperBox.jsx      correctness-first paper Q&A (disclosure | rail variant)
      paper/DeepReviewSection.jsx  run deep review + live phase progress (inline card)
      paper/review/{StoryToc,SectionMap,ActionRail}.jsx  story-page rails + paper map
      pages/PaperReviewPage.jsx    /paper/:key — single-scroll "story" page (3-zone)
      hooks/{useDeepReviewRunner,useScrollSpy}.js  auto-run review + TOC scroll-spy
```

## First-run setup & simplified Settings

`/setup` is a 3-step wizard (Connect Zotero → Connect LLM → Describe research)
that is **skippable and resumable** — `SetupGate` only redirects an
unconfigured first-run user from the default landing (`/` or `/library`), never
traps a returning one, and "Skip for now" persists `zs:setupDismissed=1`.
`useSetupStatus` (key `['setup-status']`) is the single readiness seam: it
derives `isConfigured` and the `{zotero,llm,goals,model}` pillars consumed by the
Settings `ReadinessStrip` and the `NotConfiguredCard` empty states on Today /
Library.

Settings is re-chunked into always-visible **Essentials** + one collapsible
**Advanced** `<details>`. The legacy `llm.draft_model/refine_model/api_base/
api_key_env` text inputs were removed (they duplicated the `llm_routing` editor);
the backend round-trips the nested `llm` block untouched. The API secret is
**name-only** everywhere — the UI collects the env-var NAME, never the raw value
(the always-on "secret set?" indicator was removed: it never gated Next and
alarmed before the user acted). `university_access` is mapped by `configForm.js`
too, so the single sticky **Save changes** commits it — its panel keeps only the
one-time browser-login action.

## Navigation (Increment 3 — 3 daily surfaces + Ops)

The nav collapsed from 8 top-level routes to **Library / Today / Settings / Ops**
(Hick's/Miller's Law — flat, no "More" disclosure):

```
NavBar:  [Library]            [Today] [Settings] [Ops]
            │                                       │
   ┌────────┴─────────┐                  ┌──────────┴───────────┐
   Read next (default)                   Feed review  ← Review
   Batch label  ← Annotate               Triage jobs  ← Triage
                                         Pending      ← Pending
```

- **Library** (`pages/Library.jsx`) is a tab wrapper. *Read next* (default) is the
  ranked queue (`LibraryReadNext.jsx`); *Batch label* (`?mode=batch`) is the former
  Annotate page (`AnnotationVerdict.jsx`) with its VerdictPicker + 1–4 / j-k flow.
- **Ops** (`pages/Ops.jsx`) is a tab wrapper over the three former power-tool pages,
  rendered unmodified — each still owns its own data + deep-link params. The tab is
  picked from `?tab=` (or a `#hash`).
- **Legacy redirects** (App.jsx `RedirectTo`, query string preserved):
  `/annotate → /library?mode=batch`, `/review → /ops?tab=review`,
  `/triage → /ops?tab=triage`, `/pending → /ops?tab=pending`,
  `/audit → /library` (the audit page is de-linked but still routable at
  `/audit-page`). No old bookmark 404s.

## Deep review + paper brief (inline card vs the full story page)

`DeepReviewSection` (run + live progress), `PaperReaderPane` (build controls +
native figure thumbnails — **not** an iframe; the digest renders natively in
`PaperReview`), and `AskPaperBox` (grounded Q&A) render on Library's Read-next mode
(via `library/InlineAnnotate.jsx`, expand a row) and Batch-label mode
(`pages/AnnotationVerdict.jsx`), gated on `detail.has_pdf` — all through the shared
`PaperDetailView` (`compact`) assembly.

`/paper/:key` (`pages/PaperReviewPage.jsx`) is the **full single-scroll "story"
page** — a different surface, NOT the row-card un-compacted. A 3-zone layout
(sticky TOC · reading column · sticky action + chat rail) renders everything up
front: `<PaperReview flat sectionOverlay>` (verdict + relevance + quality + full
digest inline, with each red-flag / goal annotated by a located `§ title · p.N`
chip from `deep_review.section_overlay`), `<PaperFigures>` (auto-built figure grid
+ lightbox), `<SectionMap>` (the collapsed Paper map — section titles, pages, and
the Phase-C "what it covers" one-liners, the scroll target for the located-finding
chips), the abstract, and `<ActionRail>` (verdict / +Read Next / tags / the
grounded Ask chat in its `variant="rail"`). `useDeepReviewRunner` auto-generates
the review on open for a paper with a PDF (ref-guarded once-per-key; the user opted
into the Zotero-note side effect); below `lg` it collapses to one column + a sticky
bottom verdict bar.

Story-page refinements grounded in the deep-research on reading/triage UX (Scim,
CiteRead, Paper Plain, Traceable Text — see the cited report): the full **digest
folds** behind one disclosure with **Key findings surfaced** above it (decision-first;
the long reference tail is one click, not a wall); **red flags / overstatements are
framed as "model judgment"** and demoted to "low confidence, verify" when the
self-consistency runs disagreed (`critiqueIsTentative`, named floors) — the brittle
critique signal is never asserted as fact; a **located chip degrades to `≈ § Section`
(muted)** when only the coarse section fallback matched (overlay `match: "approx"`),
and the Paper-map `⚑` only marks a section on a confident (exact/fuzzy) location;
and the rail chat offers **standing clinician starter questions** (Paper Plain's
key-question pattern) that run the same grounded/abstaining QA and vanish after the
first question.

## Confirm/Override card (review fleet, Phase 2)

The "Pre-decide top picks" button in the Library header (`LibraryReadNext.jsx`) kicks
off the review fleet (`runReviewFleet` → `POST /api/library/review-fleet/run`),
polling `fetchReviewFleetStatus` every 3s (the deep-review status pattern) and
reloading the queue once it finishes. The fleet folds each top pick's CACHED
deep-review signals into a `proposed_verdict` — no new LLM call — which the queue
attaches to the row.

`library/ProposedVerdictCard.jsx` renders that proposal on the row (mounted by
`ReadNextView.jsx` when `it.proposed_verdict` exists) as a Von-Restorff-distinct
indigo chip (rose for a Remove proposal) with the rationale + flags and exactly TWO
primary actions: **Confirm** (one-tap `submitVerdict`; a `dont_read` also queues the
❌ tag, the same reject path as `InlineAnnotate`) and **Override** (expands the row so
the existing `InlineAnnotate` → `VerdictPanel` shows, with the proposal passed as
`derivedPriorityOverride`). The one-tap Confirm is WITHHELD (Override only) when the
proposal is low-confidence or carries any quality flag — ambiguity goes to the human.
Nothing is written until an explicit Confirm/Override click.

## Conventions

- Plain `.jsx` (no TypeScript yet). `.tsx` migration can happen per-file
  later — Vite is already configured to allow it via `@vitejs/plugin-react`.
- Server state goes through `@tanstack/react-query`; transient UI state
  uses `useState`. No Redux / Zustand.
- Styling stays in Tailwind utility classes. The two custom helpers
  (`.glass` and `.mono`) match the Alpine UI in `web/ui.html`.
- **One tone vocabulary.** The canonical pill palette is `CHIP_TONE`
  (`components/paper/review/tones.js`); `ui/Badge.jsx` re-imports it instead of
  keeping a second copy, so a `dont_read` / B-grade / error reads the same shade
  in a Badge as in a Chip (Law of Similarity). New status pills reuse
  `CHIP_TONE` / `Chip` / `ActionBadge` — don't hand-roll `bg-*-100` classes.

## Out of scope for this scaffold

- Component-level browser tests are still out of scope; API wrapper tests live
  next to the wrappers and run with Vitest.
