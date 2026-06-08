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
| `domain.py` | Pure constants/helpers — the single source for priority thresholds, `score_to_priority`/`PRIORITY_TO_RELEVANCE` (derivation == prediction), `apply_prestige_floor` (demote-one-band quality floor on the top bands; unknown prestige → keep; raw score untouched), the `label:<priority>` ground-truth tag helpers (`LABEL_TAG_PREFIX`, `label_tag_for_priority`, `priority_from_label_tag` — shared by the golden read path and the zotero write path), and `normalize_doi`/`normalize_arxiv_id` (bare, version-stripped, lower-cased ids — the single source of truth for DOI/arXiv dedup comparison) |
| `contracts.py` | Small shared dataclasses (e.g. `PendingChange`, `TriageJob`) |
| `settings.py` | `Settings.load()` — every path (incl. `data_dir`) derives from here |
| `runtime.py` | `AppContext` + typed `RuntimeState` — how services reach runtime deps without FastAPI globals |

## More

Subpackages each have their own README. Start with
[docs/architecture.md](../docs/architecture.md) for the end-to-end mental model,
then the [README](../README.md) for setup + usage.
