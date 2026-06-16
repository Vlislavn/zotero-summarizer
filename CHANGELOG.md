# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project does not
yet publish versioned releases, so everything currently lives under
`[Unreleased]`.

## [Unreleased]

### Added

- **Shared UI foundation (whole-app UX pass).** New reusable primitives that
  replace per-page ad-hoc copies: `components/ui/{Spinner,Skeleton,Async,Badge,
  HintBanner}.jsx` (Badge = `StatusPill`/`PriorityBadge`/`ActionBadge`),
  `utils/humanizeError.js` (strips `HTTP NNN:` prefixes, maps statuses, never
  renders `[object Object]`), `components/paper/PaperDetailView/` (one configurable
  paper-detail assembly replacing the duplicated bodies in Annotate + Library
  InlineAnnotate), and hooks `useKeyboardNav` / `useOptimisticAction` /
  `useFocusOnChange` (generalized from Annotate). Behavior-preserving consolidation.
- **Power-tool interaction parity.** Pending Changes gains keyboard nav
  (j/k move · space select · a apply · r reject), optimistic apply/reject with
  rollback, focus-follows-action, and an "HTML allowed" hint on the note editor.
  Triage Monitor surfaces the previously buried Approve/Reject in a sticky per-row
  action bar, adds keyboard nav over the jobs list, and auto-refreshes calibration
  when a job finishes.
- **Config-UX simplification — backend.** New `services/setup/` domain + the
  `/api/setup/*` endpoints (frozen contract) backing a first-run onboarding flow:
  - `GET /api/setup/status` — one readiness probe across config / LLM (default
    provider, key-PRESENCE bool, advisory reachability) / filesystem paths /
    Zotero / trained classifier, with a `ready` gate.
  - `GET /api/setup/detect-zotero` — read-only per-OS probe for likely Zotero
    data dirs (db_exists first).
  - `PUT /api/setup/paths` — write the allowlisted `PDF_ROOT` / `ZOTERO_DATA_DIR`
    keys into `.env` (byte-for-byte preserving all other lines; 422 on a
    non-existent path or a non-allowlisted key).
  - `POST /api/setup/validate-config` — dry-run GoalsConfig validation
    (`field_errors`) + an optional default-provider connection probe; persists
    nothing.
- **`zotero-summarizer setup`** — an interactive terminal onboarding flow that
  reuses the same `services/setup` primitives (no duplicated logic with the HTTP
  layer).
- **Phase-0 bootstrap** — on `serve` startup, absent `goals.yaml` / `.env` are
  created from safe defaults (the `.env` secret placeholder is commented, never a
  real key) and the triage DB is migrated when absent. Idempotent; never
  overwrites existing files. Removes the manual `cp *.example` + `migrate` steps.
- `models/setup.py` — the Pydantic contract for `/api/setup/*`. `api_key_env` is
  only ever an env-var NAME; key presence is a BOOL — no secret value is ever in
  a response.
- **Config-UX simplification — frontend.** A first-run wizard (`/setup`: Connect
  Zotero → Connect LLM → Describe research) with Zotero path auto-detect, a live
  LLM connection test, and inline validation; a `SetupGate` that redirects a
  brand-new user once (skippable/resumable). The Settings page is re-chunked into
  Essentials (research goals, triage criteria, the default LLM provider, Zotero
  paths) + a single collapsible Advanced disclosure (full stage routing,
  classifier gate, corpus), with a readiness strip (Zotero · LLM · Goals · Model).
  Empty-state "finish setup" cards on `/today` and `/library`.

### Changed

- **Settings simplification (Laws-of-UX).** Removed the legacy
  `llm.draft_model / refine_model / api_base / api_key_env` inputs (duplicated the
  `llm_routing` editor — Occam's Razor; the backend still auto-migrates the legacy
  block). Classifier-gate sub-fields now render only when the gate is enabled
  (Hick's Law). The LLM API secret is name-only in the UI — never a raw-secret
  field.
- `vite.config.js` — the dev `/api` proxy target is now `VITE_API_TARGET`-overridable
  (defaults to `http://localhost:8000`), so a sandbox backend on another port can
  be previewed without editing the config.

- `services/llm/operational_check.py` — extracted a public
  `probe_provider(provider, model)` as the single shared probe mechanism behind
  both `check_stages()` and the setup config-draft validator (one probe, not two).
- **Layering fix:** the Settings ModelCard handler (`model_card` +
  `_model_dir` / `_load_latest_runlog_entry`) moved from `api/routes/admin.py`
  to `services/model/model_card.py` (no api→api import); `admin.py` re-exports it,
  so the `/api/admin/model` route and behavior are unchanged.

### Fixed

- **Pending Changes: no more React "setState during render".** The inline
  `ChangeEditor` lazily seeded its draft *during render* (a setState-in-render
  anti-pattern that logged a console warning every render); it now derives the
  displayed value via a pure read-through, with the save path's existing
  `buildDraft` fallback covering an unedited change. Caught by live preview, not
  by build/test/lint.
- **First-run wizard no longer traps the user on the LLM step.** Next now gates on
  a structurally-valid provider (type + base URL + key env-var name + model), not
  on a passing live connection test — a new user whose secret/endpoint isn't ready
  yet (the name-only posture sets it outside the app) can still finish setup. The
  connection test is advisory; its error drops the raw `HTTP <code>:` prefix.
- **Wizard progress indicator** no longer shows a later step as "done" (green ✓)
  before it is reached — a step is credited only once it is both reached and valid.
- **First-run no longer shows raw errors behind the setup card.** `/library` and
  `/today` gate their Zotero-backed fetches on a connected reader, so an
  unconfigured user sees only the "finish setup" card (was: "Failed to load
  sidebar" + "Failed to load queue: Unexpected server error").
- `GET /api/library/reading-queue` returns a clean **503 `zotero_unavailable`**
  when Zotero isn't configured (matching the `/api/zotero/*` routes) instead of a
  500 from an unhandled reader-unavailable error.
