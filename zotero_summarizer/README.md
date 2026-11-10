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
| `runtime.py` | `AppContext` + typed `RuntimeState` — how services reach runtime deps without FastAPI globals. Client resolution and disabled-AI 409 errors belong to `services.llm.factory`; ML-only services remain available. `RuntimeState.classifier_gate_error` mirrors `zotero_error` so readiness reports why the gate is unavailable. Optional metadata clients: `openalex_client` / `unpaywall_client` / `openreview_client`; `request_memo()`/`memoized(key, compute)` scope per-request computations (never a cross-request cache) |
| `models/config.py` | `QualityReviewConfig` carries tier-aware deep-review cost knobs: `self_consistency_runs`/`lean_self_consistency_runs`/`lean_max_text_chars`/`batch_goal_summaries` — a provider flagged `lean_deep_review` (ollama) uses the cheaper caps + a batched goal-summary call; every other provider (incl. MLX) uses the full settings. Keyed on the provider flag, not `is_local` |

`RuntimeState.config_disk_revision` holds only the hash of loaded/published config
bytes. Lifecycle/config owners stamp it; connection/full-config edits refuse when
another process changed disk behind the runtime. It is not a secret revision,
external-writer lock or permission to reload model resources automatically.

## Retaining a classifier during deployment

Set `ZS_AUTOMATIC_GATE_UPDATES=0` for a server/daemon process to retain its cached
classifier without startup/per-tick retraining or startup slate rescoring. The
setting is read after the selected project's `.env` loads; accepted values are
`0`/`false` and `1`/`true` (default: enabled, preserving ordinary launches).
An enabled gate requires a compatible cached artifact in this mode; missing or
incompatible artifacts fail instead of silently starting training. Loading the
cached artifact does not require a golden CSV when automatic updates are off.
Explicit training, rescore and goal-change commands retain their existing effects.
This is not an inference-disable switch or proof of model/input quality.

## Model-client cache ownership

`services.llm.factory.resolve_stage_client(runtime_state, stage, ...)` owns lazy
stage-client resolution; `RuntimeState` holds only the cache, lock, generation and
invalidation, plus models-only provider metadata resolution. Runtime imports no
services/API code and exposes no client-resolution method. The factory keys the
cache by a digest of an owned effective provider/model snapshot plus the stage
and thinking variant. Resolving metadata
alone does not build a client or read credentials. Construction happens outside
the cache lock; publication is guarded by the invalidation generation. Config
or credential invalidation therefore cannot be undone by an old in-flight build,
even when the replacement has identical routing metadata. An old client may
return to its original caller, never as a different routing identity's cache hit.
The keys contain no raw credentials. `ResolvedStage.fingerprint()` supplies the
shared identity; callers may require `expected_identity` before cache access or
construction. Related binds its consent token here, so an away-and-back metadata
change cannot authorize a differently resolved client. Unchanged effective
routing reuses its client; provider settings and stage/default overrides retain
their normal roles.

## More

`Settings.model_dir`, `tuned_params_path`, and `pdf_cache_dir` select
`data/models/`, `data/optuna-best-params.json`, and `data/pdfs/` below the active
project. Model/library caches never resolve an application cache from HOME.
Existing home-global artifacts are left untouched and are not auto-imported;
retrain/recompute in the selected project or explicitly copy trusted artifacts.

Per-call verification overrides disable declared chat thinking or lower active
three-level reasoning to `low`; low is not off. Explicit user off is preserved,
and unsupported provider parameters are never invented.

Subpackages each have their own README. Start with
[docs/architecture.md](../docs/architecture.md) for the end-to-end mental model,
then the [README](../README.md) for setup + usage.

Feedback scheduling is opt-in through Settings. Activation rejects a cycle wall
cap that cannot reserve its fixed operation cap; state remains project-local.

Optional Setup load approval uses Settings paths under data_dir and an explicit file-byte SHA256; nothing auto-approves. Policy cannot establish an executing-runtime envelope. See [Setup](services/setup/README.md#optional-load-approval-files).
