# services/triage/daily_select — the Today slate

Assembles the daily "Today" slate: a small, role-mixed set of feed papers
(`GET /api/daily`). Reads scored rows, normalizes them to candidates, then
greedily allocates K slots across roles (model / surprise / diversity) so the
slate isn't all one flavor. The slate never reads `gate_rejected` rows: the
former `audit` role (random gate-rejected spot-check, day-stable RNG) was
removed entirely — it degenerated into an endless one-at-a-time stream when
the primary pool emptied, and the spot-check now lives in the Review page +
Today's SpotCheck section (`services/library/review.list_by_state`).

```
processed_feed_items ─_querying.open_ro→ rows ─drop handled ─drop trashed-GUID ─drop content dupes─┐
                                                  │ (trashed-GUID = never-show-again; content by DOI/arXiv vs decided/library)
                                       ─_candidate→ normalized candidates        │
                                                  │ (+ _relevance.build_why chips)│
                       _allocation (greedy): model picks + surprise + diversity ◀┘
                                                  ▼
                                   _dataclasses.DailySlate  ──> /api/daily
```

**Duplicate protection (slate guard).** Identity dedup (`dedup_keep_newest`, by
GUID) only catches the *same* RSS item. A paper re-arriving under a different
GUID — or one the user already trashed/added, or already in the library — would
otherwise reappear. `_fetch_primary_unhandled` (the single source consumed by
both the slate and the `awaiting` counter) drops, by **DOI/arXiv only**, any
awaiting card that matches a paper in a "blocking" decision
(`fetch_decided_content_keys`: selected / black_swan / user_approved /
user_rejected / dedup_library / dedup_processed, or a `materialized_zotero_key`)
and collapses same-paper awaiting copies (keep newest). DOI/arXiv-only ⇒ a card
with no identifier is never dropped (no false positives). The triage daemon does
the matching root-cause dedup at ingest (`feeds.dedup_against_processed`).

**Trash → never show again (GUID guard).** Content dedup only helps papers that
carry a DOI/arXiv, and a trashed paper re-arrives under a *fresh* `feed_item_id`
(so `_drop_handled` misses it). `_drop_trashed_guids` drops any row whose stable
GUID matches a paper the user explicitly threw away — `user_rejected` (trashed
from Today) or a `trashed`/`deleted_all` `final_outcome` (trashed inside Zotero)
— the one identifier that survives re-ingestion, so an id-less thrown-away paper
stays gone. Mirrors the daemon's trashed-GUID suppression in `feeds._tick_dedup`.

The `model` quota scales with `K` (`assemble_daily_slate`: `model = max(3, K-2)`,
surprise/diversity stay at 1), so a larger `K` actually returns more cards — the
old fixed 3/1/1 default capped every slate at 5 regardless of `K`.

**Ordering: the shared rank blend.** Candidates are ordered by `rank_score` —
the relevance × goal-text × prestige blend from `services/model/rank_blend`
(the SAME primitive the Library queue uses, so the two surfaces can't drift):
relevance = `composite_score`, goal = `goal_sim` (max cosine to the research
goals, read from `aux_context.goal_sims`; `None` folds the goal weight back
into relevance), prestige = KNOWN `citation_percentile` only (the display
ladder's composite fallback is circular and never feeds the blend). The WHOLE
deduped pool goes to the allocator — `backlog_cap` only bounds the never-empty
fallback *fetch*, never the picker pool (a pre-pick cap starved the
surprise/diversity roles of exactly the off-mainstream papers they exist to
find). **Deep-review QUALITY** rides on top of this: `_candidate.attach_quality_from_reviews` bridges the deep_reviews cache onto the slate by each row's `stable_feed_key` (the IN-PLACE Today review key — no Zotero write) OR `materialized_zotero_key` (post-materialize; the feed GUID can't key the library-keyed cache), and the capped `rank_blend.quality_bonus` is applied ONLY inside the FLOORED model role (`_allocation._model_score`) — so a high-quality paper floats up among the recommendations while the un-floored surprise/diversity discovery roles stay quality-free and a below-bar paper can never be lifted in. Cards are therefore intentionally NOT in displayed-composite order.
`goal_sim` staleness: rescore-on-retrain/startup/drain plus a goals-save hook
(`services/config.update_runtime_config` → `schedule_slate_rescore_async`);
abstract-less rows are never rescored and keep `goal_sim=None` (honest absence,
ranked by composite alone). The Library lane is immune (it reads goal affinity
live at queue build).

**Relevance floor (model role only).** The `model` role and its `model_fallback`
never surface a `dont_read`-band paper (composite < `PRIORITY_COULD_READ_THRESHOLD`
= 2.0, the canonical `domain` band edge) — see `_allocation.MODEL_RELEVANCE_FLOOR`
— so a weak feed week doesn't pad Today with below-the-bar picks. `surprise` and
`diversity` are deliberately NOT floored (they exist to surface off-pattern /
off-library papers and own their predicates). `DailySlate` carries two honest
banner signals: `low_relevance_hidden` (count of dont-band candidates no role
surfaced) and `weak_slate` (no candidate reached the `should_read` band) — the
Today UI shows a "light week — trigger a fresh triage" note.

**Quality-first mode (`ZS_RANK_QUALITY_FIRST` / `quality_review.rank_quality_first`,
default OFF).** The user's directive — *"a high-quality paper a little bit
off-topic beats a poor-quality paper on-topic"* — as a whole-slate re-rank. When
on, `assemble_daily_slate` resolves `_common.rank_quality_first_enabled()` once and
threads `quality_first=True` through `attach_rank_scores` and `allocate`:
`rank_score` becomes the quality-LED key from `services/model/rank_blend_quality`
(quality leads, topicality soft-gates) INSTEAD of the relevance×goal×prestige blend,
the additive `quality_bonus` is dropped (grade now leads the key — re-adding it would
double-count), and the model-role floor swaps from `composite ≥ 2.0` to
`relevance_score > 2` (`_allocation.QF_RELEVANCE_FLOOR`; `None` passes) — the
composite floor would hide exactly the grade-A / low-corpus-affinity papers the
directive wants surfaced (composite is ~50% the inverted 0.268 affinity axis).
`low_relevance_hidden` counts via the active floor (`_passes_model_floor`), so the
banner stays honest in both modes. Surprise/diversity are untouched (diversity sorts
by `rank_score`, so it inherits the quality-led order for free). Default OFF until
the Track-C frontier eval clears the decision rule; `rank_blend` stays the control arm.

**Interleave mode (`ZS_RANK_INTERLEAVE` / `quality_review.rank_interleave`, default
OFF; takes precedence over quality-first — it contains both arms).** The P3 online
decision instrument (ADR-A9/GAP-G11): offline eval is exhausted (A2 sits at the
oracle ceiling), so the flip is decided by the user's OWN verdicts. When on,
`assemble_daily_slate` dispatches to `_assemble_interleaved`: it builds BOTH arms'
full slates over the same pool — A0 (control, explicit `quality_first=False`) and
A2 (`quality_first=True`) — and team-draft-merges their top-K
(`services/model/team_draft`, LOCAL-date-seeded coin → deterministic within a day)
into the ONE slate the user sees. Per-item arm attribution (`a0`/`a2`/`both` + the
competitive `pair_id`) is persisted to the `interleave_log` sidecar
(`storage/interleave`, DAY-level write-once — the first slate of a day is the only
one recorded, so a later drifted-pool re-assembly can't mix two merges' pair_ids)
for the SPRT scorer `tools/eval_interleave.py`. BLIND by design: `SlatePaper`
carries no team field. Slate metadata (pool_size / hidden / weak banners) comes
from the A0 control arm. Only the user-facing dispatch interleaves: daemon-internal
assemblies (`feeds/_tick._auto_review_slate` / `_auto_render_slate`) pass an
explicit `quality_first`, so a background K=10 ghost merge can never claim the
day's log nor widen the auto-review candidate set beyond the shipped arm.

| file | responsibility |
|---|---|
| `__init__.py` | public surface: `assemble_daily_slate` (K-scaled model quota, blend-ordered full pool; resolves `_common.rank_quality_first_enabled()` once — or takes an explicit `quality_first` arg pinning the arm — and threads it through `attach_rank_scores`/`allocate` + the `low_relevance_hidden` count; dispatches to `_assemble_interleaved` when `rank_interleave_enabled()`), `_assemble_interleaved` (P3: both arms built, team-draft-merged, attribution logged), `count_awaiting_unhandled`; `_drop_handled` + `_drop_content_dupes` (DOI/arXiv guard, `_BLOCKING_DECISIONS`) + `_drop_trashed_guids` (stable-GUID "never show again" guard, `_TRASH_DECISIONS`/`_TRASH_OUTCOMES`) |
| `_querying.py` | read-only SQLite access (delegates to `_common.connect_sqlite_ro`); `fetch_decided_content_keys` returns normalized DOI/arXiv sets for decided / in-library papers (now also blocks on a trashing `final_outcome`, not just `decision`) + `fetch_trashed_guids` returns the stable GUIDs of explicitly-trashed papers for the never-show-again guard |
| `_candidate.py` | raw DB row → normalized candidate dict (scores, provenance, `goal_sim` via `row_goal_sim` = max over `aux_context.goal_sims`, KNOWN `citation_percentile`, `rank_score`); also extracts the quality-first inputs from `summary` (`row_relevance_score` = LLM 1-5 `relevance_score`, `row_triage_dim` for `goal_alignment`/`methodological_rigor`/`evidence_strength` — sparse, `None` = signal absent); `attach_rank_scores(quality_first=)` adapts the cohort to `rank_blend.blend_scores` (default) OR `rank_blend_quality.quality_first_key` (quality-first); `attach_quality_from_reviews` is the bridge that joins the deep_reviews cache onto each candidate's `quality` via its `stable_feed_key` (in-place Today review) OR `materialized_zotero_key` (post-materialize) (one cache read; LOUD-warns on a 0-match join); `stable_feed_key` is surfaced on the candidate + `SlatePaper` for the in-place review trigger; `row_*` string accessors share `_row_str`/`_obj_field`; `row_prestige` (display only) prefers OpenAlex field-normalized `citation_percentile` (already [0,1]) → LLM prestige → h-index → composite |
| `_relevance.py` | heuristic, no-LLM reason chips. `attach_why` labels a whole cohort: goal chips key on the REAL `goal_sim` with pool-relative tercile bands (self-calibrating — mirrors the frontend `goalHighKeys` idiom; no absolute cosine constant). `goal_bands` requires ≥ 3 present positive goal_sims (a tercile needs 3 points) — with fewer the bands degenerate and a lone weak value (e.g. 0.2) would read "Strong goal match" right under the `weak_slate` "Light week" banner; below 3 it returns `(None, None)`, which suppresses goal chips entirely. Library chips key on `corpus_affinity` (engagement, labeled honestly: "Like papers you've saved" / "Off your usual track"); other thresholds reuse `domain`/`surprise` constants |
| `_allocation.py` | greedy role allocator (model / surprise / diversity slots); model + diversity picks order by `rank_score`; the model role + its fallback apply `MODEL_RELEVANCE_FLOOR` (no dont_read-band picks), surprise/diversity are un-floored. The capped deep-review QUALITY lift (`rank_blend.quality_bonus`) is added ONLY in the floored model role's sort key (`_model_score`), never in the un-floored discovery roles — so quality floats a recommendation up but can't win an un-floored slot or lift a below-bar paper. `quality_first=True` mode (`allocate`/`_pick_model` param): `_model_score` returns the bare quality-led `rank_score` (drops the double-counting `quality_bonus`) and `_passes_model_floor` swaps the composite floor for `relevance_score > QF_RELEVANCE_FLOOR` (2.0; `None` passes) |
| `_dataclasses.py` | frozen result types (`DailySlate` — incl. `low_relevance_hidden`/`weak_slate` weak-week banner signals; `SlatePaper` — includes `abstract`, `pub_year`, `goal_sim`, and `why` chips for the Today card) |
