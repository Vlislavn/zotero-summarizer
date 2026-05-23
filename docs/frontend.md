# Frontend (React SPA)

Phase 1.18 Step 2 deleted the 3,808-line Alpine `web/ui.html`. The React app
under `frontend/` is now the only UI. This document is the on-ramp for the
next developer who needs to add a route, edit a shared component, or debug
the SPA mount.

## Stack

- React 18 (plain JavaScript, no TypeScript)
- Vite 5.4 (`@vitejs/plugin-react` 4.3)
- Tailwind 3.4 (+ PostCSS, Autoprefixer)
- React Router 6.30 (`react-router-dom`)
- React Query 5.51 (`@tanstack/react-query`)

Build commands (from the repo root):

```bash
cd frontend && npm install
npm run dev      # Vite dev server on :5173, proxies /api/* to :8000
npm run build    # production build -> frontend/dist/
```

The FastAPI app mounts `frontend/dist/` at `/`. SPA deep-links (`/today`,
`/annotate`, etc.) fall through a catch-all in
`zotero_summarizer/api/app.py::_install_spa` that returns `index.html` for
any unknown path while preserving `api/` and `assets/` routes.

## Directory layout

```
frontend/
  src/
    main.jsx                 # BrowserRouter + QueryClientProvider
    App.jsx                  # <Routes> table
    index.css                # Tailwind + .glass / .slim-scroll utilities
    api/                     # one module per endpoint group
      goldenApi.js
      dailyApi.js
      settingsApi.js
      libraryApi.js
      triageApi.js
      reviewApi.js
      pendingApi.js
      auditApi.js
    components/              # shared, used by >=2 pages
      NavBar.jsx
      PaperDetailLayout.jsx
      AuthorByline.jsx
      PrestigeWaterfall.jsx
      VerdictPanel.jsx
      PaperListItem.jsx
      ProvenanceBreakdown.jsx
      AnnotationsList.jsx
      NotesList.jsx
      TagsRow.jsx
    pages/                   # one file per route
      Today.jsx
      AnnotationVerdict.jsx
      Settings.jsx
      Library.jsx
      Triage.jsx
      Review.jsx
      Pending.jsx
      Audit.jsx
```

## Routes

The complete routing table lives in `frontend/src/App.jsx`. `/` and `*`
both redirect to `/today`.

| Path         | Page component       | Purpose                                            |
| ------------ | -------------------- | -------------------------------------------------- |
| `/today`     | `Today`              | Daily must-read digest (landing page)              |
| `/annotate`  | `AnnotationVerdict`  | Batch-mode verdict entry over the provenance list  |
| `/settings`  | `Settings`           | OPML, API keys, model overrides, scoring weights   |
| `/library`   | `Library`            | Browse the full Zotero library with detail panes   |
| `/triage`    | `Triage`             | Score, filter, and gate incoming feed items        |
| `/review`    | `Review`             | Feed-level review (per-source quality)             |
| `/pending`   | `Pending`            | Items awaiting Zotero write or human action        |
| `/audit`     | `Audit`              | Re-label audit for previously-classified rows      |

## The 3-zone layout contract

`components/PaperDetailLayout.jsx` is the single source of truth for the
sticky-top / scrolling-middle / sticky-bottom layout shared by Annotate,
Library, Triage, and Pending. Use it whenever a page renders one paper's
full detail.

Props:

```jsx
<PaperDetailLayout
  topStrip={...}      // sticky top, z-10  - title, author byline, venue
  bottomStrip={...}   // sticky bottom, z-10 - verdict panel + flash banner
  emptyState={...}    // when set, replaces the entire pane (loading/empty)
  className=""
>
  {/* scrollable middle: abstract, tags, SHAP waterfall, annotations, notes */}
</PaperDetailLayout>
```

Why three zones (Fitts's Law): both the title strip and the verdict panel
stay within reach regardless of how long the paper's body is, so the user
never has to scroll to act on the paper they are reading.

## Shared-component contracts

### `<NavBar />`

```jsx
<NavBar />          // no props
```

Renders the primary tabs (`Today`, `Annotate`, `Settings`) plus a
`<details>`-based "Power tools" disclosure for `Library`, `Triage`,
`Feed Review`, `Pending`, `Re-label Audit`. Edit the `PRIMARY` and
`POWER_TOOLS` arrays in `NavBar.jsx` to add a new tab.

### `<AuthorByline />`

```jsx
<AuthorByline
  authors={[{ name: 'Smith J', h_index: 42 }, { name: 'Lee P', h_index: null }]}
  source="feed" | "note" | "library"
/>
```

Renders `Smith J (h=42), Lee P, Park K`. The `source` prop only changes
the empty-array fallback message so the user can tell whether authors are
missing in Zotero, in feed metadata, or on a note's parent paper.

### `<PrestigeWaterfall />`

```jsx
<PrestigeWaterfall
  scoring={{
    composite_score: 4.3,
    prestige_score: 3.1,
    shap_top: [{ feature: 'venue_works_count', value: 0.42 }, ...],
    prestige_inputs: { max_author_h_index: 42, venue_works_count: 1023, cited_by_count: 17 },
  }}
/>
```

Hand-rolled SVG waterfall (no chart library). When `scoring === null` it
renders a placeholder explaining the row predates triage. Bars are clipped
to the top six contributions; values are symmetric around 0.

### `<VerdictPanel />`

```jsx
<VerdictPanel
  itemKey={detail.item_key}
  derivedPriority={detail.provenance?.derived_priority}      // optional
  existingVerdict={detail.verdict}                            // {id, item_key, user_priority, comment, created_at} | null
  onSubmit={({ user_priority, comment }) => {...}}
  onDelete={() => {...}}
  submitting={mutation.isPending}
  submitError={mutation.error?.message || null}
  deleting={deleteMutation.isPending}
  deleteError={deleteMutation.error?.message || null}
/>
```

The four priority buttons are `must_read`, `should_read`, `could_read`,
`dont_read`. Confirms on delete with `window.confirm`.

### `<PaperDetailLayout>`

See the previous section.

## The uniform `review-detail` shape

The backend endpoint `GET /api/golden/review-detail?item_key=...` returns
the same shape regardless of whether the key resolves to a feed item
(`feed:*`), a Zotero note (`note:*`), or a library item:

```jsonc
{
  "item_key": "...",
  "source": "feed" | "note" | "library",
  "title": "...",
  "authors": [{ "name": "...", "h_index": 42 | null }],
  "venue": "...",
  "year": 2024,
  "doi": "...",
  "url": "...",
  "abstract": "...",
  "has_pdf": true,
  "pdf_path": "...",
  "tags": [...],
  "collections": [...],
  "annotations": [...],
  "notes": [...],
  "provenance": { "derived_priority": "must_read", ... },
  "verdict": { "id": 1, "user_priority": "...", "comment": "...", "created_at": "..." } | null,
  "scoring": {
    "composite_score": 4.3,
    "prestige_score": 3.1,
    "shap_top": [{ "feature": "...", "value": 0.42 }],
    "prestige_inputs": { "max_author_h_index": 42, ... }
  } | null
}
```

Branch on `source` in the UI, **never** on key syntax. `scoring === null`
means the row predates triage (no SHAP available); `<PrestigeWaterfall>`
already handles this.

## React Query conventions

Defaults are configured in `main.jsx`:

```js
{ staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 }
```

Query-key conventions:

- Scope-first, then params. Examples from `AnnotationVerdict.jsx`:
  - `['provenance-list', priorityFilter, flagFilter]`
  - `['review-detail', selectedKey]`
- Always prefix the key with the page or resource name; query keys are
  global and silent collisions across pages are easy to introduce.

Mutation pattern: optimistic + rollback. The canonical example is
`AnnotationVerdict.jsx::handleVerdictSubmit` - it advances the UI to the
next paper **before** the mutation lands, then on error snaps
`selectedKey` back to the failed row and shows the `flashStatus` banner
in the sticky bottom strip.

## How to add a new route

1. Create `frontend/src/pages/Foo.jsx`.
2. Create `frontend/src/api/fooApi.js` if the page hits new endpoints.
3. Add `<Route path="/foo" element={<Foo />} />` to `App.jsx`.
4. Add the entry to `PRIMARY` (always visible) or `POWER_TOOLS`
   (disclosure) in `NavBar.jsx`.
5. If the page renders a single-paper detail view, wrap it in
   `<PaperDetailLayout>` so it inherits the sticky-top / sticky-bottom
   chrome.

## Keyboard shortcuts pattern

`AnnotationVerdict.jsx::useEffect` is the reference implementation. Any
new shortcut handler **must** early-return when the focused element is a
text input - otherwise typing into the comment textarea fires the
shortcut. The guard looks like:

```js
const t = e.target;
const isTyping =
  t && (t.tagName === 'TEXTAREA' || (t.tagName === 'INPUT' && t.type !== 'checkbox'));
if (isTyping) return;
if (e.metaKey || e.ctrlKey || e.altKey) return;
```

Current shortcuts on `/annotate`: `j`/`k` navigate, `1`-`4` set priority
(`must_read`/`should_read`/`could_read`/`dont_read`).

## Build & test

```bash
cd frontend && npm run build           # emits frontend/dist/
node tests/e2e/audit.mjs               # Playwright UX audit
curl http://127.0.0.1:8000/api/health  # FastAPI backend smoke
curl http://127.0.0.1:8000/            # SPA index served by FastAPI
```

`tests/e2e/audit.mjs` exercises the sticky verdict panel, the optimistic
advance flow, and the keyboard shortcuts. Re-run it after any change to
`PaperDetailLayout`, `VerdictPanel`, or `AnnotationVerdict`.

## Gotchas

- **JSX file extensions.** Vite's React plugin only matches `.jsx` /
  `.tsx`. Adding JSX to a `.js` file silently breaks the build (no
  compiler error, just a runtime parse failure). All page and component
  files use `.jsx`; helper modules that contain no JSX (e.g.
  `pendingHelpers.js`, `reviewHelpers.js`) stay `.js`.
- **SPA catch-all guard.** `_install_spa` in
  `zotero_summarizer/api/app.py` rejects any `full_path` that starts with
  `api/` or `assets/` so static and API routes are never shadowed by
  `index.html`. If you add a new top-level static directory, extend the
  same guard.
- **Global React Query cache.** Query keys are shared across pages.
  Always namespace keys with the page or resource (`['provenance-list',
  ...]`, `['review-detail', ...]`) so cross-page invalidations behave
  predictably.
