# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project does not
yet publish versioned releases, so everything currently lives under
`[Unreleased]`.

Deep technical detail (benchmark numbers, parameter sweeps, measurement rationale)
is in `docs/internal/changelog_deep_detail.md` (gitignored, local-only).

## [Unreleased]

### Changed

- Guardrail allowlists regenerated against live findings after the sweep: every stale grandfather removed (reconcile exit 0), every kept entry re-justified inline. Slop 5 entries→6 (3 fixed, 4 surfaced from newly tracked WIP), redundancy pairs re-frozen under rule-of-three.

### Changed

- faithbench: `PaperSubstrate` bundles the per-paper text/norm/index trio (`judge_claim` 9→7 params), `judge_run` split into per-track row helpers, `run_benchmark` takes a `RunOptions` dataclass (16→10 params) with trial closures extracted. Public entry points unchanged in behavior; `RunOptions` exported.

### Changed

- `run_daemon_tick` slimmed 222→193 LOC: app-RSS refresh, L1 abstract-less rescue, and auto quality-gate blocks extracted into `_maybe_*` helpers in `_tick.py`, following the module's existing thin-orchestrator pattern. No behavior change.

### Changed

- Deduped eight near-identical helper clones found by the semantic overlap scan: shared `_inline_file` (routes), `_validate_choice` (4 config validators), `_quality_toggle_enabled`, storage `_col`, `_flight.run_in_background(name=)`, `_h` escape, `corpus_bm25.tokenize` as the single tokenizer, and one canonical `_parse_pub_year`. Zero behavior change; two validator error strings now use the sorted-list format.

### Removed

- Dead code sweep: `fetch_resolved_outcomes` (orphan; the README claim about it was stale — the real consumer uses `fetch_resolved_outcomes_by_key`), test-only `get_user_library_id`, `get_run_summary`, `list_recent_decisions` + their tests; vulture allowlist shrunk 6→2.

### Removed

- `docs/benchmarking.md` untracked from the repo (now gitignored, kept local-only); `tools/README.md` links updated to note it.

### Fixed

- **Review UX pains.** (1) Quality grade now shows the deterministic checklist grade (`quality.grade`) instead of the LLM's `digest.grade`, which flipped A↔B between runs while the chip was labelled "reference-free". (2) Review recency now reads "reviewed 3d ago" (exact date on hover) + a chip in the review header, so it's clear WHEN the deep review ran. (3) Non-paper sources get a loud amber "Not peer-reviewed" chip. (4) Read-next rows now show the `why_reason` (top rank signal) inline, not just as a filter. (5) `j`/`k` review switching also works when opened from Today (the slate now writes `zs.reviewOrder`, like Read-next), not only from Read next.
- **Prestige was dark for every item** ("high 0 / low 0 / new 1825"). `PrestigeConfig.enabled` defaulted **off**, so OpenAlex citation enrichment never ran — no item had a citation percentile / h-index → all bucketed "new". Flipped the default **on** (keyless, cached, fails soft); `ZS_OFFLINE` forces it off, `ZS_PRESTIGE_ENABLED=0` opts out. Existing items need one full-library rescore to backfill.

### Added

- **Review page: Prev/Next through the ranked queue** (`/paper/:itemKey`). Read next stores the displayed order in `localStorage` (`zs.reviewOrder`); the review page derives `‹ Prev` / `Next ›` + a `3 / 40` position and wires `j`/`k` keys (auto-disabled while typing). Same tab, no backend. Key not in the stored order → buttons hide.
- **Review notes → app + Zotero.** A free-text "My notes" box on the review page, saved to the new `review_notes` table (`POST /api/golden/review-note`) and best-effort mirrored to the Zotero item under a `My notes` note (refuses while Zotero is open — soft status, exactly like the verdict note). Decoupled from the verdict.
- **Attach paper figures to Zotero.** Review page → Figures → "+ Attach to Zotero" (`POST /api/zotero/items/{key}/figures`) files the extracted figure PNGs as child image attachments on the item, so they render in Zotero next to the paper. Dedups by filename (re-click is safe); same connector force-handshake as the other writes. `add_attachment` generalized from PDF-only to any content type.

### Changed

- **Quality → must_read promotion ON by default** (`quality_promote`; floor 3.5→3.0). The compressed gate leaves must_read empty (0/69 recall); promotion lifts grade-A/B + on-goal + gate-confirmed papers in. New `tools/eval_quality_promote.py`: on 68 firewalled user-verdict rows, 3.0/0.55 → 3 must + 9 should at 1.00 precision, 0 flooding (3.5 promoted just 1). `ZS_QUALITY_PROMOTE=0` opts out.
- **Model card no longer shows the always-zero `Thresholds` row.** `keep/must/could` are legacy classifier fields the current regressor gate hardcodes to `0.0` (bands come from fixed `domain` constants), so the row read like a bug. Dropped from `ModelCard.jsx` (+ its `formatThresholds` helper); the API still returns them, just unrendered.

- **Code-repo detection now catches GitHub links hidden in PDF hyperlink annotations.** A URL that's only a clickable annotation over text like "our code" (never in the extracted visible text) was invisible to the code-link regex. `_paper_read_pdf.extract_link_uris` harvests annotation URIs (as `content['link_uris']`, kept out of `full_text`) and the deep-review code-link layer appends them before `find_code_link`, so the repo now shows (may render `unverified`/grey — no availability phrase — still found). Harvested URIs with embedded whitespace/newlines are rejected (a crafted annotation could otherwise smuggle a fake "code available at …" phrase that outranks the real repo — injection defense).

- **Deep-review speed: cheap sub-calls now ride the feed model.** The rubric yes/no checks, overstatement, self-verify and section one-liners are known-cheap by task identity, so they route to the already-built feed client (`llm_map`) instead of the expensive deep_review reasoning model; the digest and goal summaries stay on the strong model. SOTA static per-identity tiering (claude-code `model-tier-routing`), no new config — reuses the `feed` stage. Add `deep_review.light_model` later only if feed ≠ desired sub-call model.
- **Digest reasoning now follows the provider's `thinking_effort`** instead of a hard-coded `enable_thinking=True`. New `ProviderConfig.thinking_on` (True unless `thinking_effort: off`) drives the 4 digest sites (deep_review, verify CLI, setup profiles, calibration). Digest still reasons by default (measured NEEDED); set `thinking_effort: off` on the deep_review provider to disable it — no separate toggle.

### Fixed

- **Today card quality bars stuck at 0** while the grade showed. The bars read the digest's 1-5 self-scores, but the deep-review bridge passes the reference-free `QualityEval` (which deliberately drops those unvalidated self-scores). Replaced the card's 5 dimension bars with the coverage-based signal the brief already uses (grade + rigor band chip + `met/applicable` checklist items + red flags) — one consistent quality language across card and brief; deleted the dead `QualityBar`.
- **Tags silently not saved when Zotero is open.** `TagOfInterestEditor` ignored the `requires_force` handshake, so with Zotero running every tag write was a no-op that looked successful. Now mirrors `CollectionEditor`: confirm → retry with `force` (`updateItemTags` gained a `force` arg).

- **`_auto_review_slate` never ran** (`quality_review.auto_on_tick_k` always 0). `_load_config()` in `feeds/_common.py` omitted the `quality_review` key; `QualityReviewConfig` defaults now included so the daemon tick actually submits in-place reviews.
- **`run_deep_review` missed reader for feed keys.** `library.py` now passes `resolve_reader_for_key(key)` so a `stable_feed_key` dispatches to `AppLibraryReader` instead of the Zotero reader (which 404s feed keys).
- **Invalid key formats accepted silently on `POST /api/library/deep-review/run`.** Now validates: must be 8-alnum Zotero key or `is_stable_feed_key` — otherwise 422.
- **SPA catch-all returned misleading "PDF file not found" for wrong-method API paths.** `app.py` now raises `APIError(404)` for any path under `api/` that reaches the catch-all.
- **Double `add-to-library` created duplicate Zotero entries.** `daily_actions.add_to_library` now skips rows whose `materialized_zotero_key` is already set.
- **Review fleet accepted `feed:` keys silently.** `POST /api/library/deep-review/run` (fleet mode) now rejects `feed:` keys with 422 — fleet only supports library (Zotero) keys.
- **`build_feed_detail` dead function removed** (`review_detail.py`). Zero callers; deleted.

### Added

- **Heavy paper brief for the top Today feed papers.** After triage, the daemon builds
  the full render (notes.md / presentation.html / figures) for the top
  `quality_review.render_on_tick_k` (default 3) feed papers — reusing the cached in-place
  review + cached PDF (no extra LLM), keyed by `stable_feed_key`. A Today card's "Open
  full brief ↗" opens the SAME `/paper/:key` page the library uses (now resolving feed
  keys via `resolve_reader_for_key`); the feed detail folds in the in-place review (was
  hardcoded none) and sources its metadata from app-owned `rss_items` so the feed brief
  renders WITHOUT a live Zotero DB (the old path read Zotero's feedItems table and raised
  when Zotero was absent). On "Add to library" the render rebuilds under the new Zotero key.

### Fixed

- **Prestige filter no longer blanks a preprint library.** The Library Prestige
  filter now keys on prestige *evidence* (OpenAlex citation percentile OR author
  h-index), not citation-only, so uncited preprints by known authors are
  filterable instead of all vanishing on high/low; chips show live bucket counts.
  The conservative banding floor stays citation-only.

### Changed

- **"Calibrate to my setup" now shows live progress + explains itself.** The sweep
  runs on a background thread with a `GET /api/setup/calibrate/status` poll, so the
  card shows "reviewed N of M" instead of an opaque spinner; the copy states what it
  tunes (text budget + lean/full profile to YOUR endpoint), that it runs ~6 reviews,
  that it needs a built brief first, and what it changed afterward. The missing-brief
  precondition surfaces as a clear "open a paper's deep review first" message.

### Added

- **Today papers now carry a real quality grade.** After ML triage, the daemon tick
  deep-reviews the top Today slate IN-PLACE (no Zotero write) — PDFs fetched to the
  local cache, reviews cached by `stable_feed_key`, run in parallel on the shared pool.
  The Today→quality bridge joins on `stable_feed_key` (not just the materialized Zotero
  key), so cards show A–D instead of "not assessed". Adding a paper to the library copies
  its review onto the new library key (`deep_review.copy_review`), so the review persists.
  Gated by `quality_review.auto_on_tick_k`; re-ticks skip already-reviewed keys.

- **Web articles / non-papers get a relevance-only review.** A `NON_PAPER` type
  (detected from the web-article render or a non-research Zotero itemType) skips the
  scientific A–D grade / red-flags — it's read for relevance + summary only.
  `review_web_articles` now defaults ON so a blog/news page with no PDF is rendered
  and reviewed instead of skipped. The UI shows "relevance read (not a research paper)".

- **Library journal/venue filter.** Zotero `publicationTitle` surfaced as `venue`;
  a Journal select (Advanced filters) narrows the queue by journal.

- **Code-repo link atop the deep review.** Extracts the paper's own GitHub repo
  from full text (`_code_link.py`), validates live/dead via keyless
  `integrations/github.py` + relevance, caches `code_link`, renders a banner on
  the brief; "no repo" when absent.

- **Production-grade gate rollback (fixes the `--force` overwrite problem).**
  `train-classifier --force` now snapshots the live model into
  `history/{name}/{ts}__{sha8}__oof{ρ}/` BEFORE the overwrite. INVARIANT: a
  snapshot that can't be written RAISES → `save_trained` aborts → the live model is
  NEVER overwritten without a successful backup. Rolling cap (`DEFAULT_KEEP=10`).
  New CLI: `goldenset model-history` (list rollback targets) + `goldenset
  restore-model --snapshot <name>` (atomic restore; backs up the live model first →
  reversible). 7 regression tests (`tests/test_model_backup_rollback.py`).

- **Quality → must_read promotion.** New `rank_blend.promote_band`: lifts a paper
  to must_read when grade ∈ {A,B} ∧ goal_sim ≥ 0.55 ∧ gate relevance ≥ 3.5 (strict
  AND, precision-first). One band up only; absent signal → no promotion; user
  verdicts still win. OFF by default (`ZS_QUALITY_PROMOTE`) — flip after verdicting
  reviewed rows.

- **`ProviderConfig.keep_alive`.** Ollama warm-hold knob (seconds a local model
  stays loaded) forwarded as `extra_body.keep_alive`. Off by default; pinning a
  model resident grows steady-state RAM.

- **`GET /api/golden/verdicts?source=...` provenance filter.** Lists the auto
  quality-gate's hides for one-tap restore without paging all `dont_read` rows.

- **Auto quality gate (precision mode) — quality as a hard filter.**
  Papers that fail quality are hidden automatically (`dont_read`,
  `source=auto_quality`, one-tap reversible, never clobbers a `source=user` verdict).
  Two cascaded layers: L1 = feed-stage LLM `relevance_score` floor (abstract-based);
  L2 = deep-review grade D / band `flag` (full-text, top-K). On by default
  (`ZS_AUTO_QUALITY_GATE`). Config: `ZS_AUTO_QUALITY_LLM_FLOOR`,
  `ZS_AUTO_QUALITY_HIDE_GRADES`, `ZS_AUTO_QUALITY_HIDE_BANDS`. See
  `services/library/README.md`.

- **Structured parameter extraction in the deep-review digest.** New
  `PaperParameters` model (`dataset`/`baselines`/`sample_size`/`metrics`/
  `architecture`/`external_validation`) as an optional `parameters` field on
  `PaperDigest`, extracted from the full text the review already reads.
  `parameters=null` for non-empirical papers. Regression test
  `tests/test_paper_parameters.py`.

- **Pipeline hyperparam wiring: `chunk_strategy`, `num_ctx`, structured output.**
  (1) `chunk_strategy` was dead config — now wired via `_map_reduce.digest_for_strategy`
  (`rank`/`prefix`/`map_reduce`). (2) `num_ctx` auto-derived for local providers in
  `read_config` and forwarded to ollama — prevents silent truncation at the default
  3900-token context. (3) Decoder-level JSON schema output (`response_format
  json_schema strict`) now configurable; off by default (prompt-level wins for
  well-behaved models).

- **Map-reduce deep review.** New `services/library/_map_reduce.py`: MAP each paper
  chunk on the cheap/local model (parallel), REDUCE into the digest on the remote
  API model. Exposed via `quality_review.chunk_strategy` (`rank` default | `prefix`
  | `map_reduce`) + `ZS_QUALITY_REVIEW_CHUNK_STRATEGY`. `rank` stays default.

- **Deployment profiles — Fully local vs Hybrid.** New Settings "Deployment" card +
  `profile` CLI + `GET/POST /api/setup/profile[s]`: **Fully local** (every stage on
  your local model; deep reviews on the lean tier) or **Hybrid** (local feed triage;
  deep_review on the remote API). `local_depth` lets a user force a slow deep local
  review. `services/setup/profiles.py`.

- **"Calibrate to my setup" — Tier-2 opt-in LLM sweep.** A foreground,
  memory-safe orchestrator (`services/setup/calibration.run_full_calibration`,
  `calibrate` CLI, `POST /api/setup/calibrate`, Settings card) sweeps the
  deep-review text budget on your endpoint and picks the fastest budget at no
  completeness loss.

- **Per-user calibration layer + Tier-1 env probe.** A derived `data/calibration.json`
  artifact slots into the override precedence: **code default < goals.yaml <
  calibration.json < ZS_* env** (`config_overrides.apply_calibration`). Tier-1
  measures your endpoint's throughput and writes the lean/full text+consistency
  profile automatically. Idempotent; remote endpoint is memory-safe.

- **Remote reasoning model for deep review.** A remote OpenAI-compatible provider is
  wired into `llm_routing`; the `deep_review` stage now runs on the remote reasoning
  model, while high-volume feed triage keeps the cheap local model. Override per-run
  with `--provider remote --model <name>`.

- **Tier-3 continuous recalibration.** `services/setup/calibration.tier3_recalibrate`
  (`calibrate --tier3`) recalibrates the classifier on your own golden labels once
  the set is large enough; below the floor the Tier-0 defaults stand. Runnable
  harnesses for each provisional default: `tools/eval_triage_threshold.py`,
  `tools/eval_prestige_weight.py`, `tools/eval_self_consistency.py`,
  `tools/eval_prompt_variant.py`. See `docs/internal/validated_defaults.md`.

- **One-click "Review cool papers" — auto deep-review of every high-relevance pick.**
  The Library Read-next bar shows **Review cool papers (N)**, where N = undecided
  must/should-read picks. Loops the review fleet in chunks of 5 until drained, the
  user hits Stop, or a round proposes nothing new. Results stream in mid-run.

- **Annotate active-learning list orders by decision value.** Border mode (🎯) now
  surfaces model↔prediction conflicts, then picks closest to the decision boundary
  (`border_distance`). One-line caption explains the order.

- **Per-tab comfort pass:**
  **Today** — source/feed filter on the cull slate.
  **Triage** — "Needs feedback only" filter on completed results.
  **Pending** — title filter + per-row Retry on the Failed tab.
  **Settings** — Discard button to revert unsaved edits without a page reload.
  **Audit** — Goal-Gradient progress bar on the session summary.
  **Annotate** — live `m:ss` elapsed timer during rescore + `sortBorderByUncertainty`.

- **Acquire-before-score rescue for abstract-less prestige-journal papers.**
  Nature/Science/Cell/NEJM RSS ships a boilerplate notice, not a real abstract, so
  the gate scored those papers on no content and dropped high-goal ones. The daemon
  now fetches full text for gate-rejected abstract-less items whose goal_sim clears
  a threshold, then re-scores before the verdict stands. `max_per_tick` caps the
  browser fetch. (`feeds/_tick_phases.recover_abstractless_rescues`)

- **Deep-review quality lift in ranking.** Grade/band float high-quality papers up
  WITHIN their relevance band (never across). Band-primary mode via
  `ZS_QUALITY_BAND_PRIMARY`.

- **Quality reaches the Today slate** via a GUID↔item_key bridge. The lift is
  confined to the floored model role; discovery roles stay quality-free.

- **Per-card Quality chip** in Read-next: one word (Highlight/Flag or A–D grade)
  reusing shared review tones.

- **Agentic interaction log** (`data/interaction-events.jsonl`): append-only JSON
  line per reading decision + the model prediction + the 7-day outcome. Emitted by
  verdict routes, Today keep/trash, review queue, triage feedback, and the outcome
  daemon.

- **Gated picks surface as one-click sign-in links.** When the fleet can't fetch a
  paywalled paper, the Suggested-verdicts bar renders each as a link — open it, log
  in, then Predict again.

- **Review fleet now reviews web articles** (blogs/Substack/news), not just PDFs.
  New `_pdf_acquire` web-article rung renders such a page to a PDF via headless
  browser. Gated by `quality_review.review_web_articles` (off by default; needs
  `[browser]` extra).

- **Docs: flagship journals documented** (Nature, Nat Commun, Nat Biomed Eng,
  Science, NEJM, Lancet, Cell) with the principle + PubMed F1–F4 backstop. See
  `docs/usage.md`.

- **HackerNoon documented as a triage source.** Zero code: `hackernoon.com/tagged/llm/feed`
  flows through the Zotero-RSS pipeline. Triage-only (no PDF/deep-review).

- **PubMed as a first-class triage source + PMC full-text rung.** Zero-code ingestion
  (the pipeline reads Zotero feed items). New `integrations/pubmed.py` resolves
  PMID/DOI → PMC PDF URL for papers without a DOI that the Unpaywall/OpenAlex rungs
  miss.

- **University browser access for the review fleet's PDF fetch.** Non-arXiv /
  paywalled picks can be reviewed: `_pdf_acquire` resolves arXiv → Unpaywall OA →
  OpenAlex `oa_url` → a real browser (`integrations/browser_fetch.py`, optional
  `[browser]` extra) driving a persistent profile the user logs into once via
  Settings → University access.

- **Reuse an existing browser login instead of a second in-app sign-in.**
  `university_access.cookie_browser` (chrome/firefox/edge/brave) reads that
  browser's session cookies and injects them into the fetch context. NOTE: **Safari
  is unreadable on macOS 15+** (Apple hardened its cookie container); use
  Chrome/Firefox or the in-app login.

- **Shared UI foundation.** New reusable primitives: `components/ui/{Spinner,
  Skeleton,Async,Badge,HintBanner}.jsx`, `utils/humanizeError.js`,
  `components/paper/PaperDetailView/`, hooks `useKeyboardNav` /
  `useOptimisticAction` / `useFocusOnChange`.

- **Power-tool interaction parity.** Pending Changes: keyboard nav (j/k move · space
  select · a apply · r reject), optimistic apply/reject with rollback,
  focus-follows-action. Triage Monitor: sticky per-row Approve/Reject bar, keyboard
  nav, auto-refresh on job finish.

- **Config-UX simplification — backend.** New `services/setup/` + `/api/setup/*`
  endpoints: readiness probe, Zotero path detection, path writing, config validation.

- **`zotero-summarizer setup`** — interactive terminal onboarding reusing the same
  `services/setup` primitives.

- **Phase-0 bootstrap** — on `serve` startup, absent `goals.yaml` / `.env` are
  created from safe defaults. Idempotent; never overwrites existing files. Removes
  the manual `cp *.example` + `migrate` steps.

- **Config-UX simplification — frontend.** First-run wizard (`/setup`): Connect
  Zotero → Connect LLM → Describe research. `SetupGate` redirects a new user once
  (skippable/resumable). Settings re-chunked into Essentials + Advanced disclosure.

- **One-click "open brief" (ℹ) button on every Read-next row.** Opens the brief if
  built, else builds on demand (spinner) then opens. No backend change.

- **Legible model config: Active-Models summary + per-provider `temperature` &
  `thinking_effort`.** Settings opens with a read-only "Active models" card showing
  the resolved provider/model/temperature/thinking + a live reachability dot per
  stage. New `thinking_effort` (off/low/medium/high; maps per dialect — Anthropic
  budget, OpenAI `reasoning_effort`, or vLLM `enable_thinking`).

### Security

- **Injection-safe default triage prompts.** The triage prompts now default to the
  security-hardened versions (feed fields wrapped in `<untrusted_input>` + the
  prompt-injection SECURITY directive) as code constants
  (`services/triage/prompts.py`), not just text that happened to live in
  `goals.example.yaml`. `test_prompt_injection_defense` asserts on the shipped
  constants.

### Changed

- **Split `api/routes/golden.py`**: the `/api/golden/border-suggestions`
  active-learning endpoint moved to `api/routes/_golden_border.py` (mounted via
  `include_router`) after the verdicts `source`-filter pushed `golden.py` past the
  500-LOC hard cap. No route/behavior change.

- **Config is now intent-only.** `goals.yaml` holds ONLY user intent (research
  goals, triage criteria, rubric, summary structure, language) + the LLM connection
  + university access. Every technical knob is a validated code default;
  `write_user_config` persists only the user-owned keys. Settings UI drops the
  Classifier-Gate section + corpus control. `classifier_gate` defaults flipped to
  `enabled: true` / `model_name: lightgbm`.

- **Figure lightbox → native `<dialog>`.** Uses the platform `<dialog>` (top-layer,
  native Esc + backdrop dismiss, focus-trap, `::backdrop` scrim) — removed the
  hand-rolled fixed-overlay + manual Esc listener + z-index. Active TOC link gets
  `aria-current="location"`.

- **Single-scroll paper "story" page.** `/paper/:key` is now a dedicated 3-zone page
  (sticky TOC · reading column · action+chat rail): auto-generates the review on
  open, overlays deep-review findings onto paper sections via `§ Title·p.N` chips,
  figures inline + lightbox, grounded Ask side chat.

- **Story-page UX grounded in reading research.** Key findings surfaced above the
  digest; red flags framed as "model judgment", demoted to "low confidence" when
  self-consistency disagreed; a located chip degrades to a muted `≈ § Section` on
  coarse match; rail chat offers clinician starter questions.

- **Interactive full review + quick filing.** "Open full review ↗" opens a React
  page to set the verdict, file to a collection, add/remove tags. Row-card lifts
  the collection picker out + adds a one-tap verdict; tag input autocompletes from
  existing Zotero tags.

- **Library row → terse decision card.** One banner chip-row (verdict word + grade +
  quality band + ⚠ red-flag count, each hide-when-empty) + a single reason line +
  "Open full review ↗". Everything else folds behind "Details" or the new-tab
  brief. Collections, Tags, and smart filters fold into one "Browse & filter" drawer.

- **Frontend adopts the "Ease Health" design system.** One saturated Forest Ink on
  Linen-White, surface-tint elevation (no shadows, no bold), Fraunces + Inter.
  Remaps Tailwind's color ramps + radii/shadows so every existing utility and
  `tones.js` inherit it. CSS/tokens only; no data change.

- **"Read next" opens much faster.** `get_all_items` now runs one un-paged query.
  Zotero read budget rose 0.2s→2s, eliminating the WAL checkpoint lock fallback.

- **Goal-similarity no longer recomputed on every queue open.** The
  corpus-embedding matrix is cached process-wide (main+`-wal` fingerprint) and each
  item's `goal_sim` is persisted in the score cache at Rescore time.

- **Widened + made the standalone paper brief responsive.** `.content` max-width
  740px → `min(960px,92vw)`, goal board reflows via `auto-fit/minmax` (3→2→1 cols).

- **Code-health cleanup.** Removed dead `contracts.TriageJob`; lifted duplicated
  `_RateLimiter` into `integrations/_rate_limiter.py`; promoted
  `corpus_bm25.tokenize` to the single word-tokenizer.

- **"Review cool papers" extracted to `useReviewCoolLoop` hook.** Shrinks
  `LibraryReadNext.jsx` 832→671 lines. 7 hook tests cover pin / re-chew regression /
  drain / drain-bound / terminate / stop / count. Auto-review status line is now an
  ARIA live region.

- **`eval_slate_blend.py` firewalled + CI'd.** Positive class restricted to
  `user_approved`; adds the reviewed∩labeled join via `materialized_zotero_key` +
  a measurability floor, bootstrap 95% CIs, and an additive-vs-normalized
  counterfactual.

- **Per-paper deep review fetches the full text.** "Run deeper review" on a paper
  with no Zotero PDF now acquires one first (OA/PMC/library session/web-article
  render) and reviews from it. On a paywall with no session it reports an honest
  "no full text available".

- **Frontend banners deduped.** `StatusBanner` (5 copies) and `ErrorBanner` (2
  copies) collapsed to one each in `components/library/shared.jsx`, with a11y
  `role`/`aria-live` and `humanizeError`.

- **Prewarm reads the deep-review cache once, not per pick.** New
  `deep_review.cached_review_keys()` (one read) replaces per-row
  `get_cached_review()`.

- **Unchecked Today→library adds downgraded to weak `could_read` (3.0).** A
  provisional "Add" was a full-strength `should_read` training label
  indistinguishable from a verified one.

- **Daemon + active-learning retrains now apply the `hybrid_gt` verdict overlay**
  (threaded `triage_db_path` through `load_or_train`), matching `/admin/retrain`.

- **Honest quality calibration scaffold.** `GET /api/library/review-fleet/calibration`:
  agreement and Cohen's κ between fleet proposals and confirmed labels, flagged
  `insufficient` until enough matched pairs accumulate.

- **Quality in ranking + a "quality papers" filter.** Deep-review grade/band ride on
  each queue row; a bounded quality lift floats well-graded papers up; a Quality
  (A/B · C/D) filter chip appears once rows are graded.

- **Expanded review opens in a right-side panel** (~44%, stacks on mobile). Figure
  captions no longer double-render.

- **Read-next layout — three labelled regions.** Split into `Find` (search + smart
  filters) / `Review queue` (`PredictionsBar` + ranked list) / `Export to Zotero`.

- **Review surface — aggressive subtract.** Decision-only by default: proposed-verdict
  card drops duplicate grade chip / rationale / flag pills; expanded review folds
  TLDR, method clause, signals, claims, checklist, legend, digest into "Details".

- **Paper-review render — flatten.** The in-app brief `<iframe>` is gone — the
  review renders natively via `PaperReview` + shared primitives. Nested cards
  collapsed to one container + hairline dividers + reading-grade type (13–16px /
  66ch). Grade/decision/band colours consolidated into `tones.js`.

- **Settings simplification.** Removed legacy `llm.draft_model / refine_model /
  api_base / api_key_env` inputs. Classifier-gate sub-fields render only when the
  gate is enabled. LLM API secret is name-only in the UI.

- **`vite.config.js`** — dev `/api` proxy target is `VITE_API_TARGET`-overridable
  (defaults to `http://localhost:8000`).

- **Scan-hygiene dedup.** Lifted 3 copy-paste idioms to shared helpers: prewarm-k
  → `_flight.resolve_prewarm_k`, atomic JSON writes → `_common.write_json_atomic`,
  golden-CSV reads → `_common.load_golden_rows`.

- **Review fleet — "Predict next 5" advances.** Re-running skips picks that already
  have a proposed verdict or user label.

- **Auto-rescore after a big backlog drain.** When a drain adds ≥
  `ZS_AUTORESCORE_MIN_ITEMS` (default 10) new items, the whole library rescores in
  the background.

- **Deep-review a 2nd paper while a 1st is still running.** `deep_review` is now
  per-item jobs over one provider-aware pool; each panel polls `status(item_key)`
  for its own progress.

- **Review-fleet deep reviews run in parallel for a remote provider, serial for
  local.** The fleet now batches its picks into ONE `deep_review.start` call; fan-out
  width comes from `deep_review_fleet_concurrency`.

- **UI clarity pass, pt.2.** Settings: University-access folded into one config form.
  Library search is semantic-only. VerdictPanel fixes the dont_read-renders-green
  bug. Bundle 464→446 kB.

- **UI clarity pass (subtraction-first), pt.1.** One tone vocabulary. Today drops
  PipelineFunnel/Refresh/telemetry. `PaperCard` loses relevance+prestige bars.
  `ModelCard` 14→5 audit fields. Library band-filter = histogram bars only.

- **Review fleet reviews from a local cache, not a Zotero attachment.** Acquired
  PDF injected via `start(pdf_overrides=…)`; no Zotero write. Outcome taxonomy:
  `no_fetchable_source` vs `needs_library_login`.

### Removed

- **Dead over-engineering.** Unused `TriageRepository` OO facade (zero prod callers)
  and `TriageJobService` class (→ module function `new_job`). ~70 LOC.

### Fixed

- **Deep-review digest now retries once on a malformed reply.** A reasoning model
  occasionally emits a digest with out-of-range scores or non-JSON; `assess_digest`
  re-asks once strictly before propagating the error.

- **`recover_abstractless_rescues` crashed on an empty gate-reject set when
  `app_state` was unset.** The `if not gate_rejected: return` early-exit now runs
  first (`feeds/_tick_phases.py`).

- **Library "read hidden" count inflated; read papers not hidden.** New `READ_EMOJIS`
  constant (`score_delta != 0.0` only) replaces `ALL_EMOJIS` in the read-check.
  Papers tagged only with meta emojis (🤖 ⚪ 🔮 🗣) no longer falsely classified as
  "read". `triage-approved` tag also skipped.

- **Mobile (<640px) overflow + tap-targets.** `overflow-wrap:anywhere` on
  `.review-prose`; 44px min tap-height; 16px inputs (no iOS zoom-on-focus).

- **"Review cool papers" now actually drains the cool set** (band-axis mismatch).
  `fleet.start(item_keys=…)` pins the exact cool keys; `handleReviewCool` tracks an
  attempted ledger and terminates on "no new cool key".

- **"Reviewing paper N of 5" no longer overshoots** ("6 of 5"): progress index
  clamped to the batch total.

- **Stop now reads honestly.** Distinct "Stopping…" state held until the in-flight
  chunk actually settles.

- **A 0-proposal run explains itself** instead of reverting to neutral idle.

- **First click no longer wastes itself on a running prewarm.** Detects the foreign
  run, drains it without counting a round, then pins its own cool keys.

- **Browser PDF fetch now passes Cloudflare for declared PDFs.** On a
  `context.request.get` miss, `_drive_browser` navigates to the PDF as a real page
  (`page.goto`). The per-paper path retries with a headed browser for stubborn
  challenges. When still gated, surfaces a click-to-open sign-in link.

- **Follow the page's real "Download PDF" link**, not just `citation_pdf_url`.
  Collects BOTH the meta and the on-page Download-PDF anchors and tries each.

- **Browser fetch drives the real Chrome binary** (`UniversityAccessConfig.browser_channel`,
  default `chrome`). Bundled Chromium's fingerprint doesn't match a `cf_clearance`
  cookie the real Chrome earned; the real binary's fingerprint does.

- **"Needs library login" was misleading + over-fired.** Fires ONLY when a real
  `citation_pdf_url` PDF exists but is gated at a publisher the browser isn't signed
  into. Web content (e.g. Nature news/comment) gets the render rung instead.

- **Paywalled publisher PDFs fetch via the browser rung.** PDF size cap raised 20 MB
  → 50 MB (`quality_review`/`full_text_refine.max_pdf_bytes`).

- **Gate-only backlog drain crashed on title-only items + now derives their abstract.**
  `predict` backfills missing abstracts from OpenAlex (`abstract_inverted_index`,
  already cached for prestige); residual is a terminal `gate_rejected:gate_unscorable:no_abstract`.

- **Temporal-eval `days_since_added=-1` sentinel bug.** Undated rows now sort oldest
  (never held out); the holdout fraction is taken over the dated pool.

- **"Predict next 5" silently did nothing on a heavily-labeled library.** Now scans
  the whole ranked library. Also fetches the arXiv PDF for a PDF-less pick and
  recomputes stale digest-less cached reviews.

- **`serve` died with `Errno 48 address already in use`.** Now reclaims its port
  first (`lsof` → SIGTERM → SIGKILL). Opt out with `--no-kill`.

- **Deep review crashed with `'str' object has no attribute 'model_copy'`.**
  `assess_digest` now salvages a JSON blob with `extract_json_blob` and raises
  cleanly on a truly empty completion.

- **Deep-review failures were always blamed on an unreachable endpoint.**
  The unreachable-endpoint suffix is now gated on a connectivity-looking error.

- **Library Rescore / Sync reloaded the MiniLM embedder once per 50-item predict
  batch.** `_resolve_embedding_cache` now reuses the runtime singleton.

- **Ask-the-paper returned an unhandled 500 on empty LLM output.** Now catches the
  parse `ValueError` and abstains.

- **"Predict next 5" reported every paper `failed` when a deep-review job was in
  flight.** `deep_review.start()` now returns `accepted`; the fleet waits then
  re-claims the slot.

- **Retrain double-ran on a fast double-click.** `retrain()` now claims the lock
  synchronously.

- **Sort-ranks (Call Number) re-stamped every item every run** even when unchanged.
  Now skips items whose Call Number already equals the computed rank.

- **Trash 500'd the whole batch when Zotero held the DB lock.** `mark_feed_items_read`
  is now best-effort: reports `marked_read: 0` + `marked_read_error`.

- **Quality band: near-perfect-metric red flag false-fired on the Adam β2
  hyperparameter.** A near-1 value now counts as a headline metric only when a
  performance-metric word (accuracy/AUC/F1/…/"reached") sits within ~40 chars.

- **Quality checklist grounding was too strict.** `quote_is_grounded(..., fuzzy=True)` —
  token-SequenceMatcher grounds a paraphrase whose tokens cover ≥80% of the quote,
  while still rejecting hallucinated quotes.

- **Quality band over-conservatism: self-verify + overstatement red-flag over-fired.**
  `SELF_VERIFY_PROMPT` reframed from "skeptical reviewer" to confirm-by-default.
  `OVERSTATEMENT_PROMPT` now flags only clear/material over-claims. Red-flag gate
  raised 2→3.

- **Self-verification 2nd pass catches the LLM positivity bias.** After the rubric,
  one extra short LLM call re-checks critical items marked met. Configured via
  `quality_review.self_verification` (default on).

- **Optional Docling PDF parser** (`quality_review.use_docling`). Recovers structured
  tables + figure captions fitz misses. Gated; fitz stays the default.

- **Paper-type fallback no longer mis-routes consensus guidelines.** Now keys on
  strong we-built/ran-it signals (`propose`/`rct`) before falling back.

- **Deep review judged every paper by the same empirical-ML rubric.** Now detects
  paper type (`paper_type.detect`) and judges against the recognized standard
  (SANRA/PRISMA/TRIPOD+AI/CONSORT-AI/REFORMS etc.).

- **Quality scores were unvalidated 1-5 LLM self-reports.** Band + A–D grade now
  derived from transparent checklist COVERAGE via `coverage_grade`.

- **Red flags showed near-duplicates; gloss contradicted them.** Merged by
  token-Jaccard (`_dedupe_near`). QUALITY gloss now derived from the actual
  red-flag list (`_gloss` / `bandGloss`).

- **"Predict next 5" silently did nothing.** Now tallies `proposed` /
  `skipped_no_fulltext` / `failed`; surfaces `status:"done_empty"` and names the
  cause.

- **"Triage backlog" silently did nothing.** Declared `lightgbm`; added
  `services/readiness.py` (boot log + `setup/status.subsystems[]` + 503 guard on
  the drain route).

- **Pending Changes: no more React "setState during render".** Derives the displayed
  value via a pure read-through.

- **First-run wizard no longer traps the user on the LLM step.** Next gates on a
  structurally-valid provider, not a passing live connection test.

- **Wizard progress indicator** no longer shows a later step as "done" before it is
  reached.

- **First-run no longer shows raw errors behind the setup card.** `/library` and
  `/today` gate their Zotero-backed fetches on a connected reader.

- `GET /api/library/reading-queue` returns a clean **503 `zotero_unavailable`**
  when Zotero isn't configured.
