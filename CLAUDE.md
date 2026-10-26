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

The canonical product/runtime diagram and service-domain ownership map live in
[docs/architecture.md](docs/architecture.md). Each package's `README.md` owns its
implementation contract; read it before editing.

## Hard rules (pre-commit enforces these)

1. **Use the `code-that-fits-in-your-head` skill for every code change.** Trace
   callers and search for existing code first; prefer deletion/reuse; finish by
   explicitly assessing whether the touched code can be simplified further.
2. **≤500 LOC per `.py`, with no exceptions or allowlist.** Touched production
   functions also block above 88 body lines, 6 required parameters, or 5
   control-flow levels. Split by responsibility, not metrics.
3. **Layering:** `api → services → storage/integrations → models`. `runtime.py`
   is foundational and must not import `services/` or `api/`, including
   `api.errors`. `mcp/` is an HTTP client (imports none of those).
   `integrations/`/`storage/` never import `services/`/`api/`; `services/` may
   import only `api.errors`. New service modules live in a domain subpackage.
4. **Touch a package's code → update its `README.md` in the same commit.**
5. **All app state lives under `data/`** (gitignored). Paths come from
   `Settings` — never hardcode `project_root / "..."`. *Sanctioned exception:*
   the paper-read presentation/audit (`_presentation.html` / `_audit.json`),
   `figures/`, and explicit-consent source are written next to the Zotero PDF;
   only their `paper_read.json` state lives under `data/paper_render/`. See
   `services/library/README.md`.

## Workflow

- Verify: `pre-commit run --all-files`, then `pytest -q --forked` (see
  docs/architecture.md re: the known macOS native-lib fork-crash — diff failures
  vs a baseline, don't expect zero), and `cd frontend && npm run build` for UI changes.
- Don't commit unless asked. Keep changes minimal and within the layering.
