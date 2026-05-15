# Zotero Summarizer — Frontend (`/annotate`)

React 18 + Vite 5 + Tailwind 3 single-page tool that lives at FastAPI URL
`/annotate`. This is the first React surface in a codebase whose main UI is
still the Alpine.js SPA at `zotero_summarizer/web/ui.html` — the React tree
will gradually expand from here.

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
    App.jsx                     Header + <AnnotationVerdict />
    index.css                   Tailwind directives + .glass / .mono helpers
    api/
      goldenApi.js              fetch wrappers for /api/golden/*
    pages/
      AnnotationVerdict.jsx     Two-column layout (list + detail)
    components/
      PaperListItem.jsx
      VerdictPanel.jsx
      ProvenanceBreakdown.jsx
      AnnotationsList.jsx
      NotesList.jsx
      TagsRow.jsx
```

## Conventions

- Plain `.jsx` (no TypeScript yet). `.tsx` migration can happen per-file
  later — Vite is already configured to allow it via `@vitejs/plugin-react`.
- Server state goes through `@tanstack/react-query`; transient UI state
  uses `useState`. No Redux / Zustand.
- Styling stays in Tailwind utility classes. The two custom helpers
  (`.glass` and `.mono`) match the Alpine UI in `web/ui.html`.

## Out of scope for this scaffold

- No tests in this iteration; React Testing Library setup is a follow-up.
- Data fetching is stubbed (`api/goldenApi.js` is wired, but pages render
  placeholder text until the backend endpoints are finalized).
