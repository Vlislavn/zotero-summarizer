# Architecture

The whole app in one mental model, plus the rules the pre-commit hooks enforce.
Read this before editing; every package also has its own `README.md`.

## The loop

```
  RSS feeds (in Zotero) ─┐
                         ▼
   [triage]  cheap ML gate ──reject──> dropped
                         │
                         │ survivors → LLM summary + relevance score
                         ▼
   SQLite (data/) ──> [api] ──> React UI  (Today / Library / Annotate / Settings)
        ▲                                   │
        │                                   │ you cull / read / label
   retrain [model] <── [golden] dataset <───┘
                                            │ approve changes
                                 [zotero] ──> writes back to Zotero (backup first)
```

1. **triage** scores RSS papers (gate first, LLM for survivors) into SQLite.
2. **model** is the relevance gate — trained on your labels, it ranks everything.
3. **api** serves the React UI and the JSON API; routes are thin.
4. **you** cull on *Today*, read on *Library*, fine-label on *Annotate*.
5. **golden** is your label dataset; **model** retrains on it — the loop closes.
6. **zotero** is the only write path: changes are queued, reviewed, then applied.

## Triage trigger: daemon vs UI

Triage (the pipeline above) runs identically whether it is triggered by:

- the **UI** — the *Today* tab's "Triage backlog" button (`POST /api/daily/triage-backlog`),
  on demand; or
- the **daemon** — `zotero-summarizer feeds serve`, a separate long-running process
  that ticks on a timer and auto-materializes a daily pick; or
- the **CLI** — `feeds run` / `feeds tick` one-shots.

The daemon is optional automation, not a separate engine. The `feeds.*` block in
`goals.yaml` only applies when the daemon runs.

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
| wire process-wide singletons at startup | `services/lifecycle.py` → `runtime.RuntimeState` |

The live JSON API is self-documenting: run `serve` and open `/docs` (OpenAPI).

## Layering (lower never imports higher)

```
api → services → storage / integrations → models · contracts · domain · settings · runtime
mcp → (HTTP only; imports none of the above)
```

- `integrations/`, `storage/` never import `services/` or `api/`.
- `mcp/` never imports `services/`, `api/`, or `storage/`.
- `services/` may import `api.errors` only (never `api.app` / `api.routes`).

## Data & config

- All app state lives under `data/` (gitignored): the two SQLite DBs
  (`triage_history.db`, `corpus_cache.db`), your golden dataset, logs, the
  append-only **agentic interaction log** (`interaction-events.jsonl` — the
  immutable human-decision + model-prediction trajectory for offline improvement;
  see `services/interaction_log.py`), and ML artifacts. Every path comes from
  `Settings` — never hardcode `project_root / "..."`.
- Config: `.env` (secrets/paths) + `goals.yaml` (research goals, models, prompts).
  Both are gitignored; copy the `*.example` templates to bootstrap. See the README.
- Schema changes are version-gated migrations (`storage/migrations.py`): add a new
  numbered `Migration` step, never an inline `ALTER`.

## Guardrails (enforced by `pre-commit` and CI)

Install once: `pre-commit install`. CI runs the same checks plus the test suites.

1. **≤500 LOC per `.py`** (`tools/precommit/check_file_loc.py`). Split, don't extend.
2. **Layering / structure policy** (`check_import_policy.py`) — the rules above; new
   service modules must live in a domain subpackage, not at `services/` top level.
3. **Module READMEs** (`check_module_readme.py`) — every package has one, and editing
   a package's code requires staging its `README.md` in the same commit.
4. **Redundancy** (`check_redundancy.py`) — new *provably* redundant transforms
   (idempotent `f(f(x))`, faithful round-trips, identity comprehensions, involutions)
   BLOCK; conditionally-redundant transforms and near-duplicate functions are advisory.
   Existing findings frozen in `redundancy_allowlist.txt`.
5. **AI-slop** (`check_slop.py`) — adopts [aislop](https://github.com/scanaislop/aislop)'s
   deterministic slop/dead-code detectors (swallowed exceptions, debug leftovers, mutable
   defaults, untracked TODOs, narrative/trivial comments, generic names, Long-Method
   complexity). Only a committed `breakpoint()`/`pdb.set_trace()` BLOCKs; the rest are
   advisory. Existing findings frozen in `slop_allowlist.txt`.

**Seeing findings (advisory, not enforced):** two commands, differing only in scope —
`make scan` (every detector across the whole tree) and `make scan-diff` (the same, scoped to
the `.py` changed vs the base branch). Both always exit 0 and never block; `EMBED=1` adds the
semantic code-model overlap pass, `BASE=<branch>` retargets the diff. The all-pairs **function-
overlap audit** (`tools/precommit/check_overlaps.py`) runs inside them — every function against
every function, ranked by a hybrid of a local code-embedding cosine + graded structural
similarity + API-Jaccard — surfacing consolidation candidates whose intent overlaps even across
different shapes; it degrades to deterministic-only when no embedding model is available.

## Verify a change

```bash
zotero-summarizer smoke-test                       # app constructs
pre-commit run --all-files                         # guardrails
KMP_DUPLICATE_LIB_OK=TRUE pytest -q --forked       # backend suite *
cd frontend && npm run lint && npm test && npm run build
```

\* On macOS this repo hits a known LightGBM/torch native fork crash; `--forked`
isolates it so one segfaulting test can't sink the run. A handful of those tests
fail for environment reasons, not code — diff against a clean baseline rather than
expecting zero failures.
