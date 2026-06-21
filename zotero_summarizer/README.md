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
            │   faithbench (grounding eval, validates library.qa) │
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
| `cli/` | `zotero-summarizer` CLI: serve, mcp, migrate, feeds, goldenset/ML lifecycle, faithbench |
| `models/` | Pydantic request/response + config schemas (the API contract) |
| `domain.py` | Pure constants/helpers — the single source for priority thresholds, `score_to_priority`/`PRIORITY_TO_RELEVANCE` (derivation == prediction), `apply_prestige_floor` (demote-one-band quality floor on the top bands; unknown prestige → keep; raw score untouched), the `label:<priority>` ground-truth tag helpers (`LABEL_TAG_PREFIX`, `label_tag_for_priority`, `priority_from_label_tag` — shared by the golden read path and the zotero write path), `normalize_doi`/`normalize_arxiv_id` (bare, version-stripped, lower-cased ids — the single source of truth for DOI/arXiv dedup comparison), and the `label_verdicts` provenance tags `VERDICT_SOURCE_USER`/`VERDICT_SOURCE_MACHINE_ADD` (single source shared by the triage write path and the golden/storage read path — only `machine_add` PROVISIONAL verdicts may be superseded by an observed materialization outcome at training time; explicit user verdicts always win) |
| `contracts.py` | Small shared dataclasses (e.g. `PendingChange`, `TriageJob`) |
| `settings.py` | `Settings.load()` — every path (incl. `data_dir`) derives from here, e.g. `browser_profile_dir` (`data/browser_profile`) for the review fleet's university-access browser session |
| `runtime.py` | `AppContext` + typed `RuntimeState` — how services reach runtime deps without FastAPI globals. `RuntimeState.classifier_gate_error` mirrors `zotero_error` so the readiness probe can report WHY the gate is `None` (e.g. `ModuleNotFoundError: lightgbm`) instead of a silent `None` |
| `models/config.py` | `QualityReviewConfig` carries tier-aware deep-review cost knobs: `self_consistency_runs`/`lean_self_consistency_runs`/`lean_max_text_chars`/`batch_goal_summaries` — a provider flagged `lean_deep_review` (ollama) uses the cheaper caps + a batched goal-summary call; every other provider (incl. MLX) uses the full settings. Keyed on the provider flag, not `is_local` |

## More

Subpackages each have their own README. Start with
[docs/architecture.md](../docs/architecture.md) for the end-to-end mental model,
then the [README](../README.md) for setup + usage.
