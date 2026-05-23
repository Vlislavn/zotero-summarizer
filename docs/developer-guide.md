# Developer Guide

The whole app in one mental model, plus the rules the pre-commit hooks enforce.

## The one diagram

```
  RSS feeds ─┐
             ▼
   [triage] daemon ──cheap gate──> [model] ──reject──> dropped
             │                        │
             │ survivors → LLM summary + score
             ▼
   processed_feed_items (SQLite) ──> [api] ──> React UI (Today / Library / Annotate)
             ▲                                        │
             │                                        │ you label / read / cull
   retrain [model] <── [golden] dataset <────────────┘
                                                      │ approve changes
                                          [zotero] ──> writes back to Zotero (backup first)
```

One sentence per box:

1. **triage** scores RSS papers (cheap gate first, LLM for survivors) into SQLite.
2. **model** is the relevance gate — trained on your labels, it ranks everything.
3. **api** serves the React UI and the JSON API; routes are thin.
4. **you** cull on *Today*, read on *Library*, and fine-label on *Annotate*.
5. **golden** is your label dataset; **model** retrains on it — the loop closes.
6. **zotero** is the only write path: changes are queued, reviewed, then applied.

## Where things live

| You want to… | Go to |
|---|---|
| add/change an HTTP endpoint | `api/routes/` (logic → `services/`) |
| change scoring / the ML gate | `services/model/` |
| change labels / training data | `services/golden/` |
| change the feed daemon / Today slate | `services/triage/` |
| change reading / review surfaces | `services/library/` |
| change what gets written to Zotero | `services/zotero/` |
| touch the DB / SQL | `storage/` |
| talk to Zotero / PDFs / LLM / OpenAlex | `integrations/` |
| change the agent (MCP) surface | `mcp/` (HTTP client only) |

Every package has a `README.md` with an ASCII sketch — read it before editing.

## Layering (lower never imports higher)

```
api → services → storage / integrations → models·contracts·domain
mcp → (HTTP only; imports none of the above)
```

- `integrations/`, `storage/` never import `services/` or `api/`.
- `mcp/` never imports `services/`, `api/`, or `storage/`.
- `services/` may import `api.errors` only (never `api.app`/`api.routes`).

## Data & config

- All app state lives under `data/` (gitignored): the two SQLite DBs, your
  golden dataset, logs, ML artifacts. Every path comes from `Settings` —
  never hardcode `project_root / "..."`.
- Config: `.env` (secrets/paths) + `goals.yaml` (research goals, models, prompts).

## Guardrails (enforced by `pre-commit`)

Install once: `pre-commit install`. Run anytime: `pre-commit run --all-files`.

1. **500-LOC limit** per `.py`. Legacy files are grandfathered in
   `tools/precommit/loc_allowlist.txt` with frozen ceilings — they may shrink,
   never grow. Split, don't extend.
2. **Import/structure policy** — the layering above; new service modules must
   live in a domain subpackage, not at `services/` top level.
3. **Module READMEs** — every package has one, and **editing a package's code
   requires staging its `README.md` in the same commit**. Keep the doc true.

## Verify your change

```bash
.venv/bin/python -m zotero_summarizer.cli smoke-test          # app constructs
KMP_DUPLICATE_LIB_OK=TRUE .venv/bin/python -m pytest -q --forked   # full suite*
cd frontend && npm run build                                   # UI builds
```

\* This repo's macOS test env hits a known LightGBM/torch native fork crash;
`--forked` isolates it. A handful of those tests fail for environment reasons,
not code — diff against a clean baseline rather than expecting 0 failures.
