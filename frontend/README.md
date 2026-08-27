# Zotero Summarizer frontend

React 18 + Vite 5 + Tailwind 3, served by FastAPI at the app root.

## Setup

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api/*` to `http://localhost:8000`. `npm run build` produces
`dist/` with the `/annotate/` base path used by FastAPI.

## Product surfaces

- **Library** — ranked Read next queue, batch labels, deep reviews, figures,
  grounded paper Q&A, and full-text acquisition.
- **Search** — targeted query-driven paper discovery and review.
- **Today** — feed triage and Zotero filing.
- **Settings** — the four everyday concepts: AI, Research profile, Zotero, and
  Sources. Performance/library-access controls are collapsed under Advanced.
- **Ops** — feed review, triage jobs, pending Zotero writes, and System
  diagnostics/retraining.

Legacy `/annotate`, `/review`, `/triage`, and `/pending` links redirect to their
current Library/Ops homes without dropping query parameters.

## First-run and AI settings

`/setup` is a skippable, persisted three-step wizard: Zotero → AI → research →
real verification. The final screen and Settings' Local setup health card share
the server-side doctor checklist; neither infers health in React.
`useSetupStatus` is the single readiness query used by the wizard, Settings,
and empty states.

The basic AI editor is shared by onboarding and Settings:

1. Choose OpenRouter (recommended), OpenAI, Anthropic, Gemini, Groq, Together,
   Local model, or Custom.
2. Paste an API key. The backend stores it in the OS credential store; the UI
   receives only presence/source metadata and never the secret value.
3. For Local, choose a hardware-gated Light/Balanced/Existing profile. Fixed
   profiles show source/size and a copyable pull command; the app never starts a
   model download. Connect, load models, and verify the selected identity.

Custom transport controls and the existing multi-provider/per-stage routing,
temperature, token, and thinking controls remain under **Advanced AI
configuration**. Unsurfaced config round-trips through `utils/configForm.js`,
so legacy `llm_routing` and environment-backed keys need no migration.
Provider/model saves hot-swap immediately; only filesystem path changes require
a restart.

## Paper review

Library rows use the shared `PaperDetailView`. `/paper/:key` is the full story
page: located review findings, Paper map, figures, action rail, and grounded Q&A.
The recommendation is policy-capped from independent Idea/Evidence/Writing
signals; raw model output remains in provenance. HTML paper briefs and figures
are written next to the source PDF for Zotero compatibility; Markdown sidecars
are no longer generated automatically.

The review fleet proposes verdicts for the same visible Read-next picks.
Suggestions are never written until Confirm/Override; low-confidence or flagged
proposals offer Override only.

## Offline boundary

The app is an installable local-first PWA. Its service worker caches only the
shell, never API responses. IndexedDB stores compact paper/detail snapshots, a
sync cursor, and a UUID mutation outbox. Verdicts and notes queue only on network
failure; HTTP errors remain errors. Conflicts require explicit Keep mine / Use
server resolution. PDFs, acquisition, AI, annotation, and rescoring stay
server-only.

## Structure

```text
src/
  api/          thin fetch wrappers
  components/   shared UI, setup, settings, library, paper review
  hooks/        server-state and workflow hooks
  pages/        route-level orchestration
  utils/        config/form transforms and presentation helpers
  offlineStore.js / syncClient.js
```

Use React Query for server state and local component state for transient UI.
Reuse `Button`, form primitives, and the canonical `CHIP_TONE` vocabulary; avoid
ad-hoc status colors. API wrapper tests live beside wrappers. Run `npm run build`
for every UI change.
