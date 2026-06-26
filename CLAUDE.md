# CLAUDE.md — agent guide

Local-first Zotero paper-triage app (FastAPI + SQLite + React). **Read
[docs/architecture.md](docs/architecture.md) first** for the one-diagram
mental model and where things live; the [README](README.md) covers setup + usage.

## Mental model (30s)

```
RSS ─> [triage] ─gate─> [model] ─> SQLite ─> [api] ─> React UI ─> you label/cull
                          ▲                                          │
                  retrain └──────── [golden] dataset <──────────────┘
                                    approved changes ─> [zotero] ─> Zotero (backup first)
```

`services/` holds the logic in 6 domains: **model** (ML gate), **golden**
(labels), **triage** (feed daemon), **library** (reading + the paper brief &
ask-paper Q&A), **zotero** (writes), and **faithbench** (the faithfulness
benchmark that validates the abstention/grounding prompt + chunk index
`library.qa` reuses). Each package has a `README.md` with an ASCII sketch — read
it before editing. Note: `library.qa` imports `faithbench` and
`faithbench._build_claims` imports `library.quality_review` — a lazy-resolved
cycle; the clean long-term move is to lift the shared grounding primitives into a
neutral module both import (follow-up, not required now).

## Hard rules (pre-commit enforces these)

1. **≤500 LOC per `.py`.** Legacy files are grandfathered in
   `tools/precommit/loc_allowlist.txt` (frozen — may shrink, never grow). Split.
2. **Layering:** `api → services → storage/integrations → models`. `mcp/` is an
   HTTP client (imports none of those). `integrations/`/`storage/` never import
   `services/`/`api/`; `services/` may import only `api.errors`. New service
   modules live in a domain subpackage.
3. **Touch a package's code → update its `README.md` in the same commit.**
4. **All app state lives under `data/`** (gitignored). Paths come from
   `Settings` — never hardcode `project_root / "..."`. *Sanctioned exception:*
   the paper-read artifacts (`{name}_notes.md` / `_presentation.html` /
   `figures/`) are written next to the Zotero PDF for upstream compatibility;
   only their `paper_read.json` state lives under `data/paper_render/`. See
   `services/library/README.md`.

## Workflow

- Verify: `pre-commit run --all-files`, then `pytest -q --forked` (see
  docs/architecture.md re: the known macOS native-lib fork-crash — diff failures
  vs a baseline, don't expect zero), and `cd frontend && npm run build` for UI changes.
- Don't commit unless asked. Keep changes minimal and within the layering.
