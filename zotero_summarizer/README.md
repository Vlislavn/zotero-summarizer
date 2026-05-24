# zotero_summarizer — application package

Local-first Zotero paper-triage app. Reads your local Zotero library + RSS
feeds, scores papers with a cheap ML gate (and an LLM for survivors), and
queues suggested Zotero changes for you to approve.

## Layers (lower layers never import higher ones)

```
            ┌────────────────────────────────────────────────┐
  cli.py ──>│ api/      FastAPI app + thin routes (HTTP)      │<── frontend/ (React)
            ├────────────────────────────────────────────────┤
            │ services/ business logic, grouped by domain:    │
            │   model · golden · triage · library · zotero    │
            ├────────────────────────────────────────────────┤
            │ storage/        integrations/                   │
            │ (SQLite)        (Zotero, PDF, LLM, OpenAlex)     │
            ├────────────────────────────────────────────────┤
            │ models · contracts · domain · settings · runtime│  (leaf types)
            └────────────────────────────────────────────────┘

  mcp/  is a separate process: an HTTP client of api/ (imports none of the above).
```

## Top-level modules

| file | responsibility |
|---|---|
| `cli/` | `zotero-summarizer` CLI: serve, mcp, migrate, feeds, goldenset/ML lifecycle |
| `models/` | Pydantic request/response + config schemas (the API contract) |
| `domain.py` | Pure constants/helpers: priorities, tags, score↔priority mapping |
| `contracts.py` | Small shared dataclasses (e.g. `Paper`, `PendingChange`) |
| `settings.py` | `Settings.load()` — every path (incl. `data_dir`) derives from here |
| `runtime.py` | `AppContext` + typed `RuntimeState` — how services reach runtime deps without FastAPI globals |

## More

Subpackages each have their own README. Start with
[docs/developer-guide.md](../docs/developer-guide.md) for the end-to-end mental
model, then [docs/architecture.md](../docs/architecture.md) for detail.
