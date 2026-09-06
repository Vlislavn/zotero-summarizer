# services — business logic, grouped by domain

All the real work lives here. Modules are grouped into five domains plus a
small set of shared/infra files at the top level.

Startup RSS limits are read when scheduling, after the project `.env` loads;
refresh failures propagate to the event loop's error handler.

Startup reads the classifier from `Settings.model_dir`, the same project-local
path used by training, API reads and the daemon worker. ZIP archives take
precedence over legacy joblib files; loading errors propagate rather than silently
triggering a replacement model.
Startup reuses the gate-install quality label: unavailable Spearman is `n/a`,
while a measured zero remains `0.000`.

`lifecycle.startup(background=False)` initializes dry-run clients/caches but
does not resume persisted jobs, refresh RSS, train/rescore the gate, import the
corpus or launch prewarm workers. The feed dry-run CLI uses this mode; an absent
or incompatible required cached gate fails explicitly instead of training.

```
              ┌─────────── shared/infra (top level) ───────────┐
              │ _common _adapters lifecycle run_log             │
              │ interaction_log config health results            │
              │ corpus emoji_signals                             │
              └────────────────────────────────────────────────┘
   triage/ ──gate──> model/ ──trains on──> golden/ <──labels── library/
      │  (RSS daemon)        (relevance ML)   (dataset)  (Stage-2 reading)
      └────────────────────────────> zotero/ (queue + apply writes)
   mobile PWA ←typed mutations/cursor→ sync/ ──> golden label history
   research_feed/ ──projects existing RSS + deep reviews──> weekly digest
```

| domain | what it owns |
|---|---|
| `model/` | the relevance gate: classifier, scoring blend, eval, tuning, active-learning |
| `golden/` | labels & ground truth: golden dataset, provenance, hybrid GT, relabel audit |
| `triage/` | the RSS daemon pipeline: feeds, summarization, selection, daily slate |
| `library/` | Stage-2 reading: reading queue, deep/quality review, paper-read artifacts, feed review |
| `search/` | Targeted Search — the query-driven *pull* surface (vs triage/library's *push*): topic → per-source query plan → concurrent federation (arXiv/EuropePMC/OpenAlex) → version-family dedup → cross-encoder query score under a constrained re-rank contract → light-review tier (quality before deep-set selection) → query-lensed deep read. Composes `library`'s read-only review layers; the only Zotero write is the explicit *Add to library* action (`materialize.py`). See `search/README.md`. |
| `sync/` | same-machine local-first PWA boundary: compact paper snapshots + durable cursor pull; ordered UUID verdict/note mutations with per-field conflicts and explicit auditable resolution; applied writes share online training/materialization/Zotero effects. Remote mobile remains disabled-by-deployment until auth + HTTPS exist |
| `research_feed/` | bounded weekly Research Intelligence: profile/taxonomy, RSS adapter, existing deep-review reuse, engineering cards, JSON/Markdown, and opt-in idempotent tag queue |
| `zotero/` | write path: pending changes, note rendering, Zotero read helpers |
| `llm/` | provider presets, OS-keyring/env credential resolution, per-stage clients, model discovery and operational checks. See `llm/README.md`. |
| `faithbench/` | faithfulness mini-benchmark for the deep-review / paper-Q&A pipeline: span-verified QA + trap questions + review-claim grounding, hard-before-soft judging with a pinned remote judge. CLI-driven (`faithbench build/run/judge/report`); artifacts under `data/faithbench/`. See `faithbench/README.md`. |
| `setup/` | first-run setup + onboarding: readiness `status`, read-only Zotero-dir `detect`, allowlisted `.env` path `env_writer` (byte-preserving; only `PDF_ROOT`/`ZOTERO_DATA_DIR`), dry-run config `validate`, and the Phase-0 `bootstrap` (creates absent `goals.yaml`/`.env`, runs the DB migration). Backs BOTH `/api/setup/*` and `zotero-summarizer setup`. Secrets never read as a value. See `setup/README.md`. |

Shared files: `_common` (helpers: settings/logging/sqlite-ro/now_iso_z/html_to_text/
`load_golden_rows` (delegates to `golden.csv_store.read_snapshot`), `atomic_write` (callback) + `write_json_atomic`
(dict→JSON) for tmp+replace artifact/cache writes. JSON publication delegates to
the callback writer: both use a unique sibling staging file per invocation and
clean it up on failure. They do not serialize read–modify–write operations or
promise power-loss durability (`fsync`);
`read_json_or_empty` (their read
counterpart: missing state file → `{}`, an EXISTING-but-corrupt file raises),
`is_app_rss_source` (the one definition of "row came from the app-RSS reader"),
NaN-rejecting `clamp`; `emoji_signals`
bins via `domain` so label derivation == prediction; `read_config` applies the
strict-offline override after env config, disabling prestige, OpenReview, and
full-text refinement. `settings.offline_requested` is the one toggle used by
RSS, uncached PDF, library acquisition, and Search network boundaries; the LLM-concurrency gates
`effective_llm_concurrency` (triage per-item fan-out, remote→`TRIAGE_JOB_CONCURRENCY`),
`deep_review_fleet_concurrency` (the N-paper deep-review batch, remote→`max_sub_concurrency`
else all N — NOT the triage knob, so a remote batch isn't throttled by the local-RAM cap)
and `deep_review_sub_concurrency` (within-review rubric/goal sub-calls) — all local→serial,
shared so the daemon, deep-review job, and `verify-deep-review` CLI never drift),
`_adapters` (`build_llm`: OpenAI-compatible client via OnPrem — threads the
configured request timeout and per-provider `temperature` (default 0, deterministic);
`build_pdf_extractor`.
All LLM clients are constructed through `services/llm/factory`, which calls
`build_llm` for `openai`-type providers), `lifecycle` (startup composition root — small `_init_*`
builders wire each singleton onto `RuntimeState`; LLM clients are NOT built here,
they resolve lazily per stage so startup never depends on a provider being reachable;
`_init_metadata_clients` builds the THREE optional network clients — OpenAlex prestige,
Unpaywall full-text, and the authenticated OpenReview peer-review Search source
(`OpenReviewClient`, host-locked, creds read from env INSIDE the client so only
whether-they-resolved is logged) — all sharing the one `OpenAlexCache` TTL store;
`_init_classifier_gate` schedules a background Today-slate rescore when it loads a
cached gate with an unchanged golden sha, so an offline-trained model reflects on the
next start without a manual `rescore-slate`; `startup` then runs a loud **readiness
sweep** (`readiness.all_statuses`) so a missing critical dep — e.g. `lightgbm`, which
once silently left the gate `None` and made the backlog drain spin without progress —
is logged at once instead of discovered later; the tail of `startup` also calls
`library.deep_review_prewarm.schedule_on_startup`, which background-warms the top-K
not-yet-cached deep reviews when `quality_review.prewarm_on_startup_k` > 0 so the first
paper open is instant, then `library.review_fleet.prewarm.schedule_on_startup`, which
PRE-DECIDES a `proposed_verdict` for those same top-K picks — reusing the just-warmed
deep reviews, no extra model load — so the user Confirms/Overrides instead of deciding
from scratch),
`readiness` (subsystem fail-fast: stateless on-demand checkers — `check_dependency`
(importable?) + `check_classifier_gate` (live gate? else surfaces the retrain-failure
reason vs "training" vs missing dep) — feeding ONE signal to three surfaces: the boot
log, the additive `subsystems[]` on `GET /api/setup/status`, and `require(name)` →
`503` so an action that MANDATORILY needs a dead subsystem fails fast with the real
reason instead of degrading silently; new subsystem = one checker + one row),
`run_log` (run IDs combine a readable timestamp with a stdlib UUID, so same-second
and concurrent commands cannot share report paths; latest-per-classifier reads
append order, not lexical ID order),
`interaction_log` (append-only **agentic interaction log** → `data/interaction-events.jsonl`:
one immutable JSON line per human reading decision — including explicit `label_transition`
assignment/change/retraction events with separate prior-user/current-user/model values — plus
the daemon's 7-day behavioural outcome; reuses `run_log`'s NDJSON appender,
stamps `git_commit` + the live gate's full artifact `model_sha256` so drift is attributable to a
model version. The live verdict tables UPSERT/DELETE and lose the trajectory; this keeps it
for offline improvement. Identity lookup and append failures propagate.
Emitted by the shared `golden.label_verdicts` command used by every deliberate label writer,
the legacy verdict feedback paths, and the outcome daemon — `results` also calls it. The JSONL
append still follows the current-label commit, not a transactional outbox; failure
does not roll back that prior durable decision),
`config` (GET/PUT `/api/config`; PUT persists via `_common.write_user_config` — only
the `USER_OWNED_KEYS` (intent + LLM connection + university access), so `goals.yaml`
stays intent-only; re-applies `ZS_*` env before the hot-swap; invalidates stage
clients; does not validate provider availability; an edit to `research_goals` schedules a
background Today-slate rescore so persisted per-item `goal_sims` — the slate's
rank-blend input — don't go stale against the new goals),
`config_overrides` (the auto-derived `ZS_*` env override registry for every
system-owned knob — `apply_env_overrides` re-validates, `render_overrides_doc`
generates `docs/overrides.md`; precedence: code default < `goals.yaml` < `ZS_*` env;
`read_config` additionally forces `prestige.enabled` off under `ZS_OFFLINE` so the
now-default-on OpenAlex prestige never hits the network air-gapped),
`health` (reports `starting` until the runtime config exists), `results`,
`corpus` (embeddings/affinity), `emoji_signals`
(emoji→engagement taxonomy; `READ_EMOJIS` = non-meta engagement emojis only, used by the library read/hide partition).

**Boundaries:** may import `storage/`, `integrations/`, `models`, and
`api.errors`. Must NOT import `api.app` or `api.routes` (enforced). New modules
go in a domain subpackage, not at the top level.

`results.submit_triage_feedback` delegates approve/reject replacement to the
shared atomic feedback writer; it no longer deletes the opposite verdict first.
A failed feedback insert preserves the prior decision and emits neither a log
entry nor pending tags. Logging and tag queueing still follow the feedback
commit: their own failures propagate but do not undo the committed decision.

`results.calibration_metrics` owns observed feedback reporting alongside the
feedback command; the unrelated corpus service no longer forwards this work.
The 7-day, 30-day and all-time periods use one storage snapshot and the priority
saved with each item's latest explicit decision. Unknown legacy priorities count
as feedback but not matched predictions; undefined ratios remain `null`, never
filled from a later result. Storage failures propagate. These are descriptive
metrics on reviewed triage results, not unbiased classifier-gate recall: no
counterfactual cohort, model-version attribution or sampling weights are claimed.

An enabled corpus requires a working encoder: startup and config updates propagate
load/encode failures, without fallback vectors. A disabled corpus does not load
the encoder or rewrite goals. Background corpus import logs and rethrows failures
to the task owner/event loop instead of reporting a successful partial import.

Background corpus import reads the existing whole-library listing once, unions
its keys with all cached keys, then delegates batches to the same detail-based
refresh used after user edits. One converter derives annotation/note counts from
the reader's lists and preserves collection paths; the old zero-count listing
converter is removed. Resync observes both additions and removals of engagement
without re-embedding unchanged text. This uses one detailed local read per item;
batch engagement reads are the next step only if sync latency warrants them.
The same converter preserves DOI for corpus self-exclusion. Full corpus matching
passes the summary request's DOI, matching the classifier auxiliary path; goals
remain independent of the candidate's own engagement.

Each refresh checks details under the existing corpus-write lock. Only explicit
`None` (deleted, trashed or no longer a user-library paper) removes a mirrored
row; malformed details and read errors raise. A missing listing entry alone
does not authorize deletion. Current rows and confirmed-missing keys commit in
one corpus transaction, so a failed batch retains its previous rows. Vector and
BM25 caches observe the resulting DB fingerprint change. Restored items are
imported again normally; original Zotero data and historical feedback are never
deleted. The removed `import_corpus_items` wrapper had only this caller.
The listing is an in-memory snapshot and commits are per batch, not an atomic
whole-library/cross-database snapshot. A later failed batch leaves earlier
successful batches committed; inferred feedback still follows the corpus commit.
