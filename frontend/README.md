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
- **Today** — feed triage and Zotero filing. Author h-index badges render only for positive OpenAlex evidence; unresolved author metadata is not shown as `h=0`.
- **Settings** — AI, research profile, Zotero, sources, and advanced controls.
- **Ops** — feeds, jobs, pending writes, diagnostics, and retraining.

Legacy `/annotate`, `/review`, `/triage`, and `/pending` links redirect without
dropping query parameters.

Library and Today share `todayHelpers.fulltextMessage` for unavailable-PDF
accounting from per-item outcomes. The full-text API no longer exposes partial
per-reason counters; deploy frontend and backend together. Library reports a
missing/failed result explicitly. A failed status request stops local polling
and reports that the server job may still be running; it never implies success
or silently retries forever.

Ops' Triage Monitor lists every `active_items` entry from the job API, rather
than displaying the last completed paper as current. Polling continues through
`cancelling` until workers drain; only the terminal transition refreshes
calibration. The singular current-item fields are removed from the shared
API/MCP/UI contract, so server and client must be updated together.

The monitor's **Triage feedback** panel reports observed agreement/recall and
false-negative counts against saved at-decision triage predictions. It shows
matched-prediction / feedback coverage and explicitly limits the claim to
reviewed items; the former “Gate recall” / counterfactual-audit labels are removed.
Unknown ratios render `n/a`, distinct from measured `0%`. This does not turn
ordinary approve/reject feedback into an unbiased ML-gate audit.

Settings' Current model card describes the loaded gate, not the newest artifact
or evaluation run. Its API exposes only the four displayed fields; no gate loaded
is an explicit empty state even if trained artifacts exist on disk.

Search coverage hits reuse their existing Zotero key: the card says In library
and the server command cannot create a duplicate even if called directly.
Search keys cards and Add requests by the server's persisted candidate ID, never
by title. The query panel consumes `plan.display` so tight/broad variants and
OpenReview are not omitted. Backend and frontend must be deployed together.
Today/Search's shared collection picker consumes the API's `items` tree, including
nested collections; it does not mistake the response envelope for the tree.

## Setup and AI

`/setup` is a skippable Zotero → AI-assisted/ML-only → research wizard. Saving immediately
unlocks Today; verification is optional and explicit. The wizard and Settings
share the compact server-side Doctor checklist. The gate uses backend
`configured`; stricter operational `ready` remains diagnostic.

The shared AI editor supports hosted presets, Local, and Custom. Secrets go to
the OS credential store; the UI receives only presence/source metadata. Local
profiles are hardware-gated and show an explicit pull command—the app never
starts a download. Advanced routing and unsurfaced config round-trip through
`utils/configForm.js`. Provider/model saves hot-swap; path changes need restart.
ML-only mode is a first-class completed setup state: readiness and paper review
show AI off rather than reporting an unreachable model, and Settings can re-enable it.

## Paper review

Library keeps Rescore available after a queue-read error, including when the
display retains a previously scored list. Rescore can rebuild a corrupt score
cache; the existing status poll reloads the new snapshot after completion.
The progress notice promises a complete result, not streaming partial scores.

Library band filters and the automatic review work list share one effective-band
calculation, matching the server's demote-one-band prestige policy. Only known
citation prestige below the supplied floor demotes top bands; missing evidence
does not. The displayed count uses the current queue's floor, and each fleet
round uses its freshly fetched snapshot's floor. A cross-language matrix checks
the client filters/work list against the server rule without new API fields.

Provenance shows the explicit `label:*` source when it determines the exported
priority, replacing the inapplicable additive table. A separately edited final
priority remains visible as a manual override.

Library rows share `PaperDetailView`; `/paper/:key` adds located findings,
Paper map, figures, actions, and grounded Q&A. Independent Idea/Evidence/Writing
signals cap recommendations while provenance retains raw output. HTML briefs and
figures live beside source PDFs for Zotero compatibility.

Compact cards and the full paper page share the explicit verdict editor. Picking
a priority changes a draft; Save/Update is the write boundary, and Cancel restores
the saved priority and comment. The mobile bar jumps to this same editor instead
of issuing a second, comment-erasing write. Clearing a comment remains an explicit edit.

The paper reader's stale notice covers changes to the PDF source, parser setting
or renderer; the existing one-shot rebuild uses the backend's extraction identity.
Blocking or unverified audits arrive as errors: the reader shows the failure and
rebuild action without exposing old figures or artifact links. Build notices
describe audited HTML/figures, not Markdown notes (which are no longer generated).

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
Individual Review actions acknowledge the saved decision without claiming a CSV
append or pending-change enqueue; the server stores training metadata atomically
with the verdict and returns only the processed ID and state.
Pending treats a partially failed HTTP-200 batch as an error and points to the Failed tab.
Today, Library and Pending derive batch counts and write IDs from the same current
filtered selection. Hidden selections cannot enter Add/Trash/Triage/Apply/Reject;
an empty filtered selection is a no-op. Library/Pending also block retained rows
during loading or a failed read. A started batch keeps its captured IDs through retries.
Shared list shortcuts operate outside interactive controls only. Native inputs
(including checkboxes), selectors, links/buttons, rich-text editors and ARIA
widgets keep their own keys; composition and already-handled events are ignored.
Pending's collection selector cannot issue Apply/Reject, while the explicit batch
buttons and shortcuts on the non-interactive list surface remain available.

## Offline boundary

The installable local-first PWA caches only its shell. IndexedDB holds compact
snapshots, a sync cursor, and a UUID mutation outbox. Verdicts/notes queue only
on network failure; conflicts require Keep mine / Use server. PDFs, AI,
annotation, acquisition, and rescoring remain server-only.

After reconnect, the server gives queued verdicts and notes the same training,
feed-materialization, and Zotero-mirror effects as online saves. This is a
same-machine loopback PWA boundary; remote mobile needs a future authenticated
HTTPS deployment rather than exposing `/api/sync` directly.

Sequence allocation, mutation insert, optimistic paper state, and cached-detail
update share one IndexedDB transaction; pulls refresh both the compact paper and
any cached review detail. A 15-second sync deadline releases a stuck focus sync,
and protocol incompatibility explicitly asks for an app refresh. The IndexedDB
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
The 15-second deadline still bounds one sync attempt, not all future attempts.
Deploy this build with the matching backend; earlier protocol-v1 servers do not
understand predecessor receipts.

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
