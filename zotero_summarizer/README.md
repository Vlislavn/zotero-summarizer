# zotero_summarizer — application package

Local-first Zotero paper-triage app. Reads your local Zotero library + RSS
feeds, scores papers with a cheap ML gate (and an LLM for survivors), and
queues suggested Zotero changes for you to approve.

PDF triage keeps a frozen selected-text/raw-SDK-link/full-document identity for
literal method-artifact admission. Shared low-level traversal remains in
`integrations/pdf.py`; library presentation has a separate normalized policy.
Reader/identity and live receipt-write failures stay loud; source membership is
not scientific support or usefulness. See `services/triage/README.md`.

## Layers (lower layers never import higher ones)

```
            ┌────────────────────────────────────────────────┐
  cli.py ──>│ api/      FastAPI app + thin routes (HTTP)      │<── frontend/ (React)
            ├────────────────────────────────────────────────┤
            │ services/ business logic, grouped by domain:    │
            │   model · golden · triage · library · zotero · setup │
            │   sync · faithbench (grounding eval for library.qa) │
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
| `contracts.py` | Small shared dataclasses (e.g. `PendingChange`) |
| `settings.py` | `Settings.load()` — every path (incl. `data_dir`) derives from here; blank `ZOTERO_DATA_DIR` falls back to `~/Zotero` rather than resolving to the checkout, e.g. `browser_profile_dir` (`data/browser_profile`) for the review fleet's university-access browser session, `interaction_log_path` (`data/interaction-events.jsonl`) for the append-only agentic interaction log (`services.interaction_log`), and `search_dir` (`data/search`) for Targeted Search research sessions (`services.search`) |
| `runtime.py` | `AppContext` + typed `RuntimeState` — how services reach runtime deps without FastAPI globals. AI actions fail with a typed 409 when `llm_enabled=false`; ML-only services remain available. `RuntimeState.classifier_gate_error` mirrors `zotero_error` so readiness reports why the gate is unavailable. Optional metadata clients: `openalex_client` / `unpaywall_client` / `openreview_client` |
| `models/config.py` | `QualityReviewConfig` carries tier-aware deep-review cost knobs: `self_consistency_runs`/`lean_self_consistency_runs`/`lean_max_text_chars`/`batch_goal_summaries` — a provider flagged `lean_deep_review` (ollama) uses the cheaper caps + a batched goal-summary call; every other provider (incl. MLX) uses the full settings. Keyed on the provider flag, not `is_local` |

## More

`Settings.model_dir`, `tuned_params_path`, and `pdf_cache_dir` select
`data/models/`, `data/optuna-best-params.json`, and `data/pdfs/` below the active
project. Model/library caches never resolve an application cache from HOME.
Existing home-global artifacts are left untouched and are not auto-imported;
retrain/recompute in the selected project or explicitly copy trusted artifacts.

`Settings.feedback_analysis_dir` selects `data/feedback-analysis/` for retained
feedback snapshots, deterministic analysis and private JSON/Markdown reports.
`goldenset analyze-feedback` uses a bounded clean child; default-off weekly
`feeds serve` automation stores its cursor/status under `feedback_schedule_dir`
and requires positive owned finalization before advancing. Input capture is read-only;
retained evidence preserves source identity, ambiguity and coverage gaps rather
than inferring historical model identities or treating descriptive counts as gain.
Strict schedule environment/budget settings are validated by selected Settings;
manual operation and daemon activation remain separate.
See [the feedback-analysis contract](services/golden/feedback_analysis/INTERFACES.md).

Settings optionally selects `ZS_SETUP_LOAD_POLICY_PATH` and an independent
`ZS_SETUP_LOAD_POLICY_SHA256`; both default absent. SHA binds operator-selected
bytes, not authenticated approval or measured sufficiency. No policy file,
numeric defaults or effective runtime envelope is generated.

Subpackages each have their own README. Start with
[docs/architecture.md](../docs/architecture.md) for the end-to-end mental model,
then the [README](../README.md) for setup + usage.
