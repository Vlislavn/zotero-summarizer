# services — business logic, grouped by domain

All the real work lives here. Modules are grouped into five domains plus a
small set of shared/infra files at the top level.

```
              ┌─────────── shared/infra (top level) ───────────┐
              │ _common _adapters lifecycle run_log             │
              │ config health results corpus emoji_signals      │
              └────────────────────────────────────────────────┘
   triage/ ──gate──> model/ ──trains on──> golden/ <──labels── library/
      │  (RSS daemon)        (relevance ML)   (dataset)  (Stage-2 reading)
      └────────────────────────────> zotero/ (queue + apply writes)
```

| domain | what it owns |
|---|---|
| `model/` | the relevance gate: classifier, scoring blend, eval, tuning, active-learning |
| `golden/` | labels & ground truth: golden dataset, provenance, hybrid GT, relabel audit |
| `triage/` | the RSS daemon pipeline: feeds, summarization, selection, daily slate |
| `library/` | Stage-2 reading: reading queue, deep/quality review, feed review |
| `zotero/` | write path: pending changes, note rendering, Zotero read helpers |

Shared files: `_common` (helpers: settings/logging/sqlite-ro/now_iso_z/html_to_text),
`_adapters` (build LLM/PDF), `lifecycle` (startup wiring), `run_log`, `config`,
`health`, `results`, `corpus` (embeddings/affinity), `emoji_signals`.

**Boundaries:** may import `storage/`, `integrations/`, `models`, and
`api.errors`. Must NOT import `api.app` or `api.routes` (enforced). New modules
go in a domain subpackage, not at the top level.
