# services/triage — the RSS daemon pipeline

Turns raw RSS feed items into scored, ranked `processed_feed_items` rows that
the Today tab consumes. The cheap `model/` gate fast-rejects; survivors get an
LLM summary + composite score.

```
Zotero feedItems ─feeds.run_daemon_tick→ gate(model) ─reject──> dropped
                                            │              └─rescue──> scored row
                                            └─keep──> summarization (LLM) ─score→ row
   recover_abstractless   : acquire-before-score rescue (see below)
   select.plateau_select  : daemon's materialization cutoff (kneedle elbow)
   daily_select/          : Today's role-mixed slate (model+surprise+diversity)
   daily_actions          : Today "Add to library" / "Trash" (write labels)
   triage_jobs            : on-demand library triage jobs
   triage_backlog         : drain the un-triaged backlog — ML-only by default
```

**ML-first drain (default).** The "Triage backlog" button runs the gate ONLY
(`gate_only=True`, `bulk_drain_gate_only` config): the classifier scores every
survivor from embeddings + prestige with **no per-item LLM call** — fast,
memory-safe, GPU-accelerated. Rows are written `triaged_pending` + marked read
(`review_mode=False`) so the slate fills and the picker drains. The full-text
LLM quality digest is **on-demand per paper** (Library → Deep Review), never run
in bulk. **Fail-fast precondition:** the gate-only drain MANDATORILY needs a live
classifier gate, so the `POST /api/daily/triage-backlog` route calls
`services.readiness.require("classifier_gate")` first — a missing gate (e.g.
`lightgbm` uninstalled, or a retrain still in flight) returns a `503` with the real
reason instead of starting a doomed background spin that re-fetches the same batch
forever (the 2026-06-16 bug). `_drain_worker` repeats the check as defence-in-depth
and its exception boundary now `LOGGER.exception`s, so a drain failure is never
swallowed unlogged again. `triage_backlog.status()` exposes `gate_reject_rate` /
`gate_onward` so the Today banner can show "filtered X% by the ML gate". Concurrency for the
remaining LLM work (legacy drain / live daemon) is provider-aware: **1 for a
local model**, the configured `TRIAGE_JOB_CONCURRENCY` for a remote one
(`services._common.effective_llm_concurrency`).

**Acquire-before-score rescue (`recover_abstractless_rescues`, `feeds/_tick_phases`).**
Prestige-journal RSS (Nature/Science/Cell/NEJM/Annals) ships only a boilerplate
publication notice — not a real abstract — so the gate's abstract-derived features
(`abstract_log_len`, `semantic_match_specter2`) score the paper on no content and
drop it to `dont_read` (the 2026-06-23 "Conversational AI for Disease Management"
miss: gate 0.299, yet goal_sim 0.556). Right after the gate, for each *rejected*
item that (a) has **no usable abstract** (`_common._has_usable_abstract` strips the
notice + title; `< min_abstract_chars` of prose left) **and** (b) whose strongest
research-goal cosine — already computed by the gate, read from
`pred.aux_context["goal_sims"]` for free — clears `recover_abstract.goal_sim_threshold`,
it fetches the full text via the review-fleet chain (`library/_pdf_acquire`) and
re-scores on the PDF (`run_pipeline` + `_apply_prestige`), injecting a finished
`TriagedCandidate` into the triaged bucket. `max_per_tick` caps the browser/paywall
fetch so it never runs across a whole journal backlog (the cap is logged). Any
per-item fetch/score failure leaves the gate verdict standing (mirrors
`_daily._maybe_full_text_refine`). Disabled (`enabled=False`) → the gate verdict is
final, as before. Skipped in `gate_only` mode (no LLM).

| file | responsibility |
|---|---|
| `feeds/` | the daemon orchestrator package — facade re-exports the sub-modules below |
| `feeds/_common.py` | constants, the tick-report dataclass, low-level helpers (leaf) |
| `feeds/_triage.py` · `feeds/_gate.py` | abstract triage + concurrent scoring + prestige · classifier gate + audit + retrain |
| `feeds/_daily.py` · `feeds/_tick.py` | daily plateau selection + refine · one daemon tick (orchestration). After triage the tick fires TWO auto-reviews (gated by `quality_review.auto_on_tick_k`): `_maybe_auto_review` reviews the just-materialized daily picks (live reader, keyed by Zotero key) and `_auto_review_slate` reviews the broader Today slate IN-PLACE — no Zotero write — via `AppLibraryReader` keyed by `stable_feed_key`, so EVERY shown card can carry a real quality grade, not just the materialized few. Both are fire-and-forget on the shared `deep_review` pool; the in-place pass skips already-reviewed keys so re-ticks stay cheap. A third step `_auto_render_slate` (gated by `quality_review.render_on_tick_k`, default 3) then builds the full HEAVY brief (notes.md/presentation.html/figures) for the TOP-K feed papers — only keys ALREADY reviewed (so the brief folds the cached review in, one tick after the review, no race) and not yet rendered — via `paper_render.start_build` (its own pool + single-flight; PDF is a cache hit, digest cached → NO extra LLM). So a top feed paper opens with the same brief as a library item (`/paper/:stable_feed_key`). On "Add to library", `daily_actions._carry_renders_best_effort` rebuilds the render under the new Zotero key |
| `feeds/_tick_phases.py` | the discrete tick phases — incl. `recover_abstractless_rescues` (acquire-before-score) |
| `feeds/_outcomes.py` · `feeds/_loop.py` | outcome detection → feedback · the long-running asyncio loop. `_outcomes` also appends an `outcome_resolved` event to the agentic interaction log (`services.interaction_log.log_behavioural_outcome`) — the daemon-resolved 7-day engaged/moved/trashed signal, joined to the at-triage verdict by `feed_item_id`; this is the second event producer (daemon thread, shared UTC stamp) |
| `summarization.py` | the LLM summarize/refine pipeline (`run_pipeline`) — refine/triage prompts default to the hardened constants in `prompts.py` (a null `config.prompts.*` = use them) |
| `prompts.py` | `DEFAULT_REFINE_PROMPT` / `DEFAULT_TRIAGE_PROMPT` — the validated, injection-safe CODE defaults (feed fields wrapped in `<untrusted_input>` + SECURITY directive). Prompts are engineering artifacts, not user config; a bootstrap-created `goals.yaml` (null prompts) is injection-safe by default. `DEFAULT_MAP_PROMPT` is the per-chunk map step for the map-reduce deep review (`services/library/_map_reduce.py`) |
| `select.py` | plateau/elbow cutoff for daily materialization |
| `daily_actions.py` | Today keep/trash → Zotero + training labels. "Add" files into the user-chosen collection (`add_to_library(item_ids, target_collection_key=None)` resolves the picker's key → name via `ZoteroReader.collection_name_for_key`, an unknown key → 400; default None → "Inbox") and writes a PROVISIONAL verdict (`label_verdicts.source='machine_add'`, tier `feed_interest`) — captured as interest but UNCHECKED, so `golden/hybrid_gt` caps its effective training label at weak `could_read` (not the `should_read` shown for display intent) until you verify it or the 7-day materialization outcome resolves; trash stays a deliberate `user` verdict. A positive verdict (must/should/could) on a feed paper now auto-materializes it into the Inbox too — `materialize_feed_verdict(item_key, user_priority)` reuses the same `_materialize_one`/`_mark_pending` core as `add_to_library` but writes NO training label (the verdict is already saved), instead stamping `user_priority` as the new item's ground-truth `label:<priority>` tag in the same write so it reaches Zotero even while Zotero stays open (the standalone `zotero_set_label_tag` refuses to); `dont_read` never adds. On "Add", an IN-PLACE Today review (cached under `stable_feed_key`) is copied onto the new library `item_key` (`deep_review.copy_review`) so the deep review persists into the library. Both keep (`today_keep`) and trash (`today_trash`) also append a `human_feedback` event to the agentic interaction log via `_record_label` (`services.interaction_log.log_feed_decision`) — the gate's pre-mutation derived priority + the human's keep/trash. The daemon gate retrain (`feeds/_gate`) threads `triage_db_path` into `load_or_train`, so it applies this overlay too (not just `/admin/retrain`). **Trash marks the feed items read best-effort**: the `dont_read` labels are the source of truth and are committed per-row, so a Zotero-DB-lock on `mark_feed_items_read` reports `marked_read: 0` + `marked_read_error` instead of 500-ing the whole batch after the labels already saved (matches the per-row best-effort contract) |
| `triage_jobs.py` | background triage-job lifecycle (`/api/triage/run`); persists a snapshot copy so the DB-write thread never serialises a live-mutating job |
| `triage_backlog.py` | single-thread **ML-only** drain of un-triaged feed backlog (gate scores; no LLM); `allow_daily_selection=False` — the UI button never auto-materialises into the Inbox; `status()` surfaces `gate_reject_rate`. On completion it **auto-rescores the slate** (`rescored`/`rescore_error` in `status()`) so freshly-drained rows rank consistently with what was already there |
| `rescore_slate.py` | re-score the CURRENT Today slate in place with the live gate; rewrites only the gate-derived fields via `storage.feeds.update_scores` — never a card's decision/read-status, and skips already-handled rows so nothing is re-surfaced. It is now triggered **automatically** (not just by `POST /api/daily/rescore-slate`): after a backlog drain, after any gate retrain (daemon or UI `install_gate`), and at startup for a cached gate — so Today always reflects the current model |
| `daily_select/` | the role-allocated Today slate (see its README) |

**Source: the app-owned RSS pool.** Since the app-RSS migration the daemon's
default reader is `integrations/app_rss.py` (`AppRssReader`): subscriptions live
in the app's `rss_feeds` table (seed via Settings or `POST /api/rss/import-zotero`),
and each tick fetches upstream RSS/Atom directly into `rss_items` — rotating
least-recently-fetched-first so a bounded pass covers EVERY enabled feed. The
pipeline stays source-agnostic (any public RSS/Atom URL: arXiv, bioRxiv,
**PubMed**); `_tick_dedup` dedups cross-feed on normalized DOI/arXiv. **Zotero
stays in sync through a separate OPTIONAL `ZoteroReader`/`ZoteroWriter` pair**
the tick constructs (Zotero absent → logged skip, retried next tick): (1)
`_zotero_readsync.sync_zotero_read_state` sweeps Zotero's unread `feedItems` and
marks read every guid the app already read — this is what clears the Zotero
unread badge post-migration (`feeds.zotero_read_sync`, default on); (2)
`_tick_dedup.dedup_against_library` checks DOI/arXiv against the REAL Zotero
library (routing the app-RSS source reader there made the guard a silent no-op);
(3) `_outcomes` resolves engagement outcomes via `get_item_membership` on the
Zotero library (the app-RSS reader has no such method — every check died on a
swallowed AttributeError, starving the outcome→golden→retrain loop). The
`feeds.*` knobs are a validated `FeedsConfig` section (`ZS_FEEDS_*` overrides,
`docs/overrides.md`) — previously raw-dict passthrough that goals.yaml silently
dropped. *Known caveat:* PubMed RSS abstracts are often truncated to the
conclusion, so the gate/goal_sim see a partial abstract for PubMed picks.
Deferred upgrade if this hurts ranking: a PMID→efetch abstract backfill in
`integrations/` called before the gate. See
[docs/usage.md](../../../docs/usage.md) "Adding sources".

**Boundaries:** imports `model/` (gate), `zotero/` (pending), and shared
scoring; standard services rules.
