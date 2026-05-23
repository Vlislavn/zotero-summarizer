# 2026-05-15 — UI redesign: full Alpine to React migration

- **Status:** Accepted
- **Date:** 2026-05-15
- **Authors:** vlislavn

## Context

Phase 1.18 Step 1 shipped a React annotation tool at `/annotate/`, mounted inside the
existing Alpine UI as an iframe tab. The intent was to ship label-provenance auditing
first and let the rest of the UI follow incrementally. The user opened the tool on
2026-05-15 and pushed back hard on the result.

The verbatim feedback collected that day, in order:

- "I want to be able to annotate it here, not below"
- "library is useless from UI perspective"
- "user notes are missing"
- "on today I don't know what means 'worth it' and 'waste'"
- "majority of papers show Detail load failed: HTTP 404"
- "That UI needs deep reconsidering before I can validate the labels"
- "make sure we are moving away from plain HTML into react. and in the end — documentation."

Three structural problems sat behind these symptoms:

1. **The 404s.** `review_detail()` in `api/routes/golden.py` only handled 8-character
   Zotero library keys. The golden CSV contains 632 rows keyed `feed:*` (35.7 percent of
   the file) and 21 rows keyed `note:*` (1.2 percent). Every one of them returned 404.
2. **Invisible prestige inputs.** Per-author h-index and venue works-count were already
   fetched from OpenAlex, cached, and persisted into
   `processed_feed_items.shap_contribs_json` under `summary.prestige_score` and
   `aux_context.max_author_h_index`. They were never returned by any UI endpoint, so the
   user could not see why a paper scored the way it did.
3. **Alpine tab sprawl.** The Alpine `web/ui.html` accreted 3,808 lines of code across
   seven tabs (Today, Library, Triage, Feed Review, Pending, Labels, Settings) over
   multiple phases. No tab was ever retired. Hick's Law was violated every time the page
   opened.

The user explicitly rejected an incremental fix and asked for the full React migration in
one step, with documentation at the end.

## Decision

- `/api/golden/review-detail` was rewritten to dispatch by key prefix. `feed:*` resolves
  through a new `_feed_review_detail()` helper that reads `processed_feed_items` and
  merges feed metadata; `note:*` resolves through `_note_review_detail()` against the
  parent library item plus the note row; bare 8-character keys keep the existing library
  path. All three branches return the same payload shape, with a new `source` field and a
  new `scoring` block (composite, prestige, six-bar SHAP top, raw prestige inputs).
- The 3,808-line Alpine `web/ui.html` was deleted in this step. The React app at
  `frontend/` (Vite + React Router + React Query + Tailwind) is now the only UI. FastAPI
  mounts the bundle at `/` and serves `index.html` for every non-API deep link
  (`zotero_summarizer/api/app.py:24-61`).
- Three primary tabs (Today, Annotate, Settings) plus five power-tool routes (Library,
  Triage, Feed Review, Pending, Re-label Audit) all live in React. The primary three are
  always visible; the power tools sit behind a `<details>` disclosure
  (`frontend/src/components/NavBar.jsx:6-18`).
- Master-detail with a 3-zone sticky layout (`PaperDetailLayout.jsx`) is the canonical
  paper-inspection surface. Modal and accordion options were discarded.
- The visual for prestige reasoning is the full SHAP waterfall
  (`PrestigeWaterfall.jsx`), not a numeric breakdown.

## Consequences

### Positive

- The 404 rate on the annotation tool dropped from 37 percent of golden rows to 0.
- The h-index and venue prestige inputs are now visible end to end, from the OpenAlex
  cache through `scoring.prestige_inputs` into the waterfall.
- The tab count visible by default is three, not seven. The remaining surfaces are still
  one click away but no longer compete for attention on every page load.
- Net code-line delta is roughly minus 3,800 Alpine plus 2,600 React, so the repository
  is smaller and more uniform than it was the day before.
- All 502 backend tests pass. The pre-Step-2 baseline was 467.
- The Playwright UX audit reports zero friction points. The pre-Phase-1.18 baseline was
  three.

### Negative

- The migration happened in one step instead of per tab. If any power-tool route has a
  subtle regression it can go undetected until the user actually opens that tab. The
  power tools were ported at functional parity, not visual parity, and some Alpine polish
  was dropped.
- The OpenAlex per-author cache only stores `max_author_h_index` (the trio max), so the
  byline only shows the first author's h-index. Per-author h-index would require new
  OpenAlex calls on the request path and was deferred.
- The Settings tab does not surface the `feeds.daily_selection_at` field. The backend
  `GoalsConfig` Pydantic model would need to be extended before the UI can expose it.

### Neutral

- React Query staleness: pages do not auto-refresh on backend mutations from other tabs.
  The user reloads when needed. This is acceptable for a single-user local app.
- File-size discipline: `services/feeds.py` was split into three modules
  (`feeds_constants.py`, `feeds_schema.py`, `feeds.py`) to stay under the 500-LOC project
  rule. The split is mechanical and the public import surface is unchanged.

## Alternatives considered

1. **Keep Alpine; only fix the 404 and add h-index visibility.** Rejected. The user
   referred to the Alpine UI as "this shitty html" and identified the tab count itself
   as a problem. Patching the two symptoms would have left the structural complaint
   untouched and the user would have re-raised it on the next pass.

2. **Port tabs incrementally across multiple sessions.** Rejected. The user said "make
   sure we are moving away from plain HTML into react" and indicated they wanted the full
   move now. An incremental migration would have left the Alpine codebase as a long tail
   of "we will get to it" tabs, which is exactly the accretion pattern that produced the
   3,808-line file in the first place.

3. **Modal overlay or inline accordion for paper detail.** Rejected. The master-detail
   layout was already the user's mental model from Alpine, and the user verbally chose
   to keep it in the planning AskUserQuestion. A modal or an accordion would have asked
   the user to learn a new interaction pattern for no compensating gain in information
   density.

4. **Numeric-only prestige breakdown instead of full SHAP waterfall.** Rejected. The
   user chose the full waterfall in the planning AskUserQuestion. A numeric-only
   breakdown would have been cheaper to build but would have hidden the relative size
   of contributions, which is the part the user actually wanted to inspect when deciding
   whether to trust a score.
