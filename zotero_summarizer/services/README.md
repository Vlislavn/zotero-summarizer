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
| `library/` | Stage-2 reading: reading queue, deep/quality review, paper-read artifacts, feed review |
| `zotero/` | write path: pending changes, note rendering, Zotero read helpers |
| `llm/` | per-stage provider/model resolution: `factory` (build a client from a `ProviderConfig`, dispatch on `type`) + `operational_check` (manual probe of each stage). See `llm/README.md`. |
| `faithbench/` | faithfulness mini-benchmark for the deep-review / paper-Q&A pipeline: span-verified QA + trap questions + review-claim grounding, hard-before-soft judging with a pinned remote judge. CLI-driven (`faithbench build/run/judge/report`); artifacts under `data/faithbench/`. See `faithbench/README.md`. |
| `setup/` | first-run setup + onboarding: readiness `status`, read-only Zotero-dir `detect`, allowlisted `.env` path `env_writer` (byte-preserving; only `PDF_ROOT`/`ZOTERO_DATA_DIR`), dry-run config `validate`, and the Phase-0 `bootstrap` (creates absent `goals.yaml`/`.env`, runs the DB migration). Backs BOTH `/api/setup/*` and `zotero-summarizer setup`. Secrets never read as a value. See `setup/README.md`. |

Shared files: `_common` (helpers: settings/logging/sqlite-ro/now_iso_z/html_to_text,
`atomic_write` for tmp+replace artifact writes, NaN-rejecting `clamp`; `emoji_signals`
bins via `domain` so label derivation == prediction; the LLM-concurrency gates
`effective_llm_concurrency` (per-item fan-out) and `deep_review_sub_concurrency`
(within-review rubric/goal sub-calls) — both local→serial / remote→capped, shared so
the daemon, deep-review job, and `verify-deep-review` CLI never drift),
`_adapters` (`build_llm`: OpenAI-compatible client via OnPrem; `build_pdf_extractor`.
All LLM clients are constructed through `services/llm/factory`, which calls
`build_llm` for `openai`-type providers), `lifecycle` (startup composition root — small `_init_*`
builders wire each singleton onto `RuntimeState`; LLM clients are NOT built here,
they resolve lazily per stage so startup never depends on a provider being reachable;
`_init_classifier_gate` schedules a background Today-slate rescore when it loads a
cached gate with an unchanged golden sha, so an offline-trained model reflects on the
next start without a manual `rescore-slate`; the tail of `startup` also calls
`library.deep_review_prewarm.schedule_on_startup`, which background-warms the top-K
not-yet-cached deep reviews when `quality_review.prewarm_on_startup_k` > 0 so the first
paper open is instant, then `library.review_fleet.prewarm.schedule_on_startup`, which
PRE-DECIDES a `proposed_verdict` for those same top-K picks — reusing the just-warmed
deep reviews, no extra model load — so the user Confirms/Overrides instead of deciding
from scratch),
`run_log`, `config` (GET/PUT `/api/config`; PUT persists + invalidates stage clients,
does not validate provider availability; an edit to `research_goals` schedules a
background Today-slate rescore so persisted per-item `goal_sims` — the slate's
rank-blend input — don't go stale against the new goals), `health`, `results`,
`corpus` (embeddings/affinity), `emoji_signals`.

**Boundaries:** may import `storage/`, `integrations/`, `models`, and
`api.errors`. Must NOT import `api.app` or `api.routes` (enforced). New modules
go in a domain subpackage, not at the top level.
