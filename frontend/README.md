# Zotero Summarizer frontend

React 18 + Vite 5 + Tailwind 3, served by FastAPI at the app root.

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api/*` to `http://localhost:8000`; `npm run build` writes
`dist/` for FastAPI.

## Product surfaces

- **Library** — Read next, labels, reviews, figures, paper Q&A, and acquisition.
- **Search** — query-driven discovery and review.
- **Today** — feed triage and Zotero filing.
- **Settings** — AI, research profile, Zotero, sources, and advanced controls.
- **Ops** — feeds, jobs, pending writes, diagnostics, and retraining.

Legacy `/annotate`, `/review`, `/triage`, and `/pending` links redirect without
dropping query parameters.

## Setup and AI

`/setup` is a skippable Zotero → AI → research wizard. Saving immediately
unlocks Today; verification is optional and explicit. The wizard and Settings
share the compact server-side Doctor checklist. The gate uses backend
`configured`; stricter operational `ready` remains diagnostic.

The shared AI editor supports hosted presets, Local, and Custom. Secrets go to
the OS credential store; the UI receives only presence/source metadata. Local
profiles are hardware-gated and show an explicit pull command—the app never
starts a download. Advanced routing and unsurfaced config round-trip through
`utils/configForm.js`. Provider/model saves hot-swap; path changes need restart.

## Paper review

Library rows share `PaperDetailView`; `/paper/:key` adds located findings,
Paper map, figures, actions, and grounded Q&A. Independent Idea/Evidence/Writing
signals cap recommendations while provenance retains raw output. HTML briefs and
figures live beside source PDFs for Zotero compatibility.

Missing-full-text recovery is one shared notice on every review surface. A missing
optional browser package links to Settings; only an attempted authenticated fetch
offers publisher sign-in, so installing support and refreshing a session are never
presented as the same action.

Fleet suggestions are proposals only: Confirm/Override writes them, and
low-confidence or flagged proposals offer Override only.

## Offline boundary

The installable local-first PWA caches only its shell. IndexedDB holds compact
snapshots, a sync cursor, and a UUID mutation outbox. Verdicts/notes queue only
on network failure; conflicts require Keep mine / Use server. PDFs, AI,
annotation, acquisition, and rescoring remain server-only.

The IndexedDB test kills/reimports the client module and proves cached context
plus a persisted sequence for rapid same-millisecond verdicts survive while the server is absent. Ask Paper sends a
bounded session history; the backend compacts older evidence to verified
extraction handles, and the UI labels verified quotes without inventing pages.

## Structure and conventions

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
tests beside wrappers and run `npm run build` for every UI change.
