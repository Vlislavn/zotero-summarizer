# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project does not
yet publish versioned releases, so everything currently lives under
`[Unreleased]`.

## [Unreleased]

### Added

- **Legible model config: Active-Models summary + per-provider temperature & graded
  thinking-effort.** Settings now opens with a read-only "Active models" card showing the
  RESOLVED provider·model·temperature·thinking + a live reachability dot per stage
  (feed/backlog/deep_review) — answering "what's running now" without a probe. New
  per-provider `temperature` (default 0, openai-path only) and `thinking_effort`
  (off/low/medium/high; `services/llm/thinking.py` maps it per dialect — Anthropic budget,
  OpenAI `reasoning_effort`, or vLLM `enable_thinking` on/off). `None` effort is fully
  back-compat. `/settings` regrouped into AI Models / Triage / Classifier; the duplicate
  slim default-provider editor is gone (one editor); the readiness "Model" pill renamed to
  "Classifier" (it's the trained classifier, not the LLM).

### Changed

- **Code-health cleanup (advisory `make scan`):** removed dead `contracts.TriageJob`;
  lifted the duplicated `_RateLimiter` (openalex/pubmed) into shared
  `integrations/_rate_limiter.py`; promoted `corpus_bm25.tokenize` to the single
  word-tokenizer (faithbench `_build_qa` + library `_paper_goal_summaries` reuse it);
  split `build_reading_queue` and `run_daemon_tick` into small helpers to clear the
  function-length signal. Behavior-preserving; allowlisted the test-only `with_db_path`.

- **Extracted the "Review cool papers" loop into a unit-tested `useReviewCoolLoop`
  hook** (`frontend/src/hooks/`), shrinking `LibraryReadNext.jsx` 832→671 lines. The
  orchestration (pin cool keys, attempted-ledger dedup, foreign-prewarm drain, honest
  Stop-settle, mount-resume) is now isolated and covered by 7 hook tests (pin / re-chew
  regression / drain / drain-bound / terminate / stop / count). Adds dev-only test infra
  (`jsdom`, `@testing-library/react`) scoped per-file so the existing pure-logic tests
  stay in the node env. Behavior-identical — re-verified live (cool 1→0, re-proposed).
  Also: the auto-review status line is now an ARIA live region (`role=status`/`aria-live`)
  so screen readers hear the minutes-long progress.

### Fixed

- **"Review cool papers" now actually drains the cool set (band-axis mismatch).** The
  client counted cool (must/should-read) picks, but the fleet's `_select_keys` was
  band-agnostic — it reviewed the top *undecided* rows in blended-rank order, which are
  often higher-blended `could_read` papers, leaving band-cool stragglers (buried deep in
  the queue) never selected; because those could rows proposed, the loop's
  `!settled.proposed` guard never fired and it re-chewed up to 12 rounds (117 papers
  reviewed, cool set barely moving). Fix: `fleet.start(item_keys=…)` (+ route) lets the
  client **pin its exact cool keys** so the fleet reviews the SAME rows the UI counts;
  `handleReviewCool` tracks an **attempted ledger** and terminates on "no new cool key",
  not on proposed==0. Receipts confirmed the stragglers were merely *slow* (a clean
  single review of one finished in 147 s), never a deep-review failure.
- **"Reviewing paper N of 5" no longer overshoots** ("6 of 5"): the bar clamps the
  progress index to the batch total (PASS-2 re-reviews had inflated `deep_review.completed`).
- **Stop now reads honestly.** It shows a distinct **"Stopping…"** state and holds it
  until the in-flight chunk actually settles, instead of leaving a stale "Reviewing N of
  5" next to a re-enabled button; the tooltip no longer over-promises a mid-review cancel.
- **A 0-proposal run explains itself** instead of reverting to the neutral idle prompt:
  the bar's branch order now lets a real terminal status (`done_empty`/`ready`/`error`)
  beat the idle copy ("Reviewed N but proposed 0 — none yielded a fetchable digest").
- **First click no longer wastes itself on a running prewarm.** If a startup-prewarm
  fleet holds the single-flight latch, the loop detects the foreign run (its `started_at`
  predates the click), drains it without counting a round, then pins its own cool keys.

### Added

- **One-click "Review cool papers" — auto deep-review of every high-relevance pick.**
  The Library Read-next bar replaces "Predict next 5" with **Review cool papers (N)**,
  where N = the undecided must/should-read picks (`isCoolUndecided`). It loops the
  review fleet (deep-review → propose-verdict) over that whole set in chunks of 5 until
  it's drained, the user hits **Stop**, or a round proposes nothing new (the rest are
  unfetchable). Results **stream in mid-run**: the queue reloads each time a paper
  settles, so reviews, Confirm/Override cards and quality chips appear live instead of
  only at the end. Pure frontend — reuses the existing fleet/deep-review engine; the
  quality lift it surfaces is already applied on every queue rebuild (no separate
  rescore). No more clicking "Run deeper review" per paper.
- **Annotate active-learning list now orders by decision value** (`sortBorderByUncertainty`).
  Border mode (🎯 active learning) returned its uncertain picks in raw backend order;
  it now surfaces the most-worth-labeling papers first — model⇄prediction *conflicts*,
  then the picks closest to the decision boundary (smallest `border_distance`) — with a
  one-line caption explaining the order. Labeling the genuinely-uncertain papers first
  is what makes active learning pay off per click. Pure, stable sort; unit-tested.
- **Per-tab comfort pass** — one focused improvement on each remaining surface:
  **Today** — a source/feed filter on the cull slate (focus on arXiv vs PubMed vs HN;
  shown only when the slate mixes feeds). **Triage** — an opt-in "Needs feedback only"
  filter on completed results so a long job's still-to-review items aren't buried.
  **Pending** — a title filter to find one paper's queued change without scrolling
  (filter-aware empty state) **plus a per-row Retry** on the Failed tab — re-applies a
  failed Zotero write through the same path (`/api/pending/apply {retry:true}` re-applies
  FAILED rows) without re-queuing. **Settings** — a **Discard** button (shown while dirty)
  to revert unsaved edits to the last-saved config without a page reload. **Audit** —
  a Goal-Gradient answered/target progress bar on the session summary. **Annotate** —
  the active-learning border mode now shows a live **`m:ss elapsed`** timer during its
  minutes-long "Scoring your library…" rescore (honest progress + an exit hint), and
  orders the uncertain picks most-worth-labeling first (`sortBorderByUncertainty`).

### Fixed

- **Browser PDF fetch now passes Cloudflare for declared PDFs.** A paper's
  `citation_pdf_url` (Nature/Springer/Elsevier/Wiley) was fetched via
  `context.request.get` — an HTTP API client with none of patchright's page-level
  stealth — so Cloudflare bot-walled it even with valid cookies (the AgentClinic / npj
  Digital Medicine "browser yielded nothing" case). On that miss `_drive_browser` now
  **navigates to the PDF as a real page** (`page.goto` + the response interceptor), which
  solves the managed challenge and carries `cf_clearance`. The interactive per-paper
  path also retries the landing once with a **visible (headed) browser**
  (`allow_headed_fallback`) for stubborn challenges; the background fleet stays headless.
  When a paper is still gated, the per-paper pane now surfaces a **click-to-open sign-in
  link** (`needs_login` + `login_url` threaded onto the deep-review entry) instead of a
  misleading "paywalled" message.
- **Follow the page's real "Download PDF" link, not just `citation_pdf_url`.** The npj
  Digital Medicine / AgentClinic "browser yielded nothing" case was **not** Cloudflare
  (server is Nature's *Oscar Platform*): the `citation_pdf_url` meta (`<article>.pdf`) is a
  **redirect trap** that 30x's back to the HTML landing, while the on-page **Download-PDF**
  button points to the real open-access file (`_reference.pdf`, 13 MB). `_drive_browser`
  now collects BOTH the meta and the on-page Download-PDF anchors (`_pdf_candidates`) and
  tries each, so the actual PDF is fetched instead of giving up at the trap. Generalizes
  to any publisher whose declared meta and real download link diverge.
- **Browser fetch drives the REAL Chrome binary** (`UniversityAccessConfig.browser_channel`,
  default `chrome`) with `no_viewport`, for both the fetch and the one-time login. Bundled
  chromium's fingerprint/UA don't match the `cf_clearance` cookie a cookie-source Chrome
  earned, so aggressive Cloudflare publishers (Nature/npj) re-challenged it; the real
  Chrome binary's fingerprint matches, so the injected clearance is accepted. `""` falls
  back to bundled chromium for setups without Chrome installed.

### Added

- **Acquire-before-score rescue for abstract-less prestige-journal papers**
  (`feeds/_tick_phases.recover_abstractless_rescues`, `RecoverAbstractConfig`,
  default ON). Nature/Science/Cell/NEJM RSS ships a boilerplate publication notice,
  not a real abstract, so the gate scored those papers on no content and dropped
  high-goal ones to `dont_read` (the "Conversational AI for Disease Management"
  Nature miss: gate 0.299, goal_sim 0.556). The daemon now re-checks each
  gate-rejected, abstract-less item whose strongest goal_sim clears a threshold,
  fetches its full text (review-fleet `_pdf_acquire`) and re-scores on the PDF
  before the verdict stands; `max_per_tick` caps the browser fetch.
- **Deep-review quality lift in ranking** (`rank_blend.quality_bonus`, shared pure
  helper): a capped, order-only bonus that floats high-quality papers up WITHIN
  their relevance band (never across — banding derives from the raw score, not the
  sort key). Grade-only by default; band-primary (highlight↑/flag↓; neutral &
  uncertain→exactly 0.0) is a measured arm via `quality_review.quality_band_primary`
  / `ZS_QUALITY_BAND_PRIMARY`.
- **Quality reaches the Today slate** via a GUID↔item_key bridge
  (`daily_select/_candidate.attach_quality_from_reviews`): joins the deep_reviews
  cache by `materialized_zotero_key` (the feed GUID can't key it). The lift is
  confined to the FLOORED model role (`_allocation._pick_model`); discovery roles
  stay quality-free and a below-bar paper can't be lifted in.
- **Per-card Quality chip** in Read-next (`ReadNextView`): one word (Highlight/Flag
  or A–D grade) reusing the shared review tones, so a moved card shows its cause.
- **`tools/eval_rigor_vs_band.py`**: validates the incumbent abstract
  `methodological_rigor` vs the deep-review band (weighted κ + Spearman +
  false-strong-on-flag cell) + a kept/trashed lift; display-only until both pass.

- **Agentic interaction log** (`services/interaction_log.py` →
  `data/interaction-events.jsonl`): append-only, immutable JSON line per human
  reading decision + the model prediction it reacted to, plus the 7-day outcome;
  stamped with `git_commit` + the gate `golden_csv_sha256`. Emitted by the verdict
  routes (incl. DELETE retraction), Today keep/trash, review queue, triage feedback,
  and the outcome daemon. Reuses `run_log`; best-effort (warns, never blocks the
  durable write). Keeps the trajectory the UPSERT/DELETE verdict tables destroy.

### Changed

- **`tools/eval_slate_blend.py` firewalled + CI'd**: positive class restricted to
  `user_approved` (`selected`/`black_swan` were the allocator's own outputs =
  leakage, now a separate diagnostic arm); adds the reviewed∩labeled join via
  `materialized_zotero_key` + a measurability floor, bootstrap 95% CIs, a
  within-reviewed NDCG, and an additive-vs-normalized reorder-reach counterfactual.

- **Per-paper deep review fetches the full text.** "Run deeper review" on a paper
  with no Zotero PDF now acquires one first (`deep_review.start(acquire_missing=True)`
  → `_pdf_acquire.acquire_for_item`: OA/PMC/library session/web-article render) and
  reviews from it, instead of telling the user to "Find Available PDF in Zotero". On a
  paywall with no session it still reports an honest "no full text available". Scoped
  to the single-key route path (acquisition is one stateful browser session).

- **Frontend banners deduped.** `StatusBanner` (5 copies) and `ErrorBanner` (2
  copies) collapsed to one each in `components/library/shared.jsx`, now with a11y
  `role`/`aria-live` and `humanizeError`. `formatPercent` in Audit reused from
  `triageHelpers`.

- **Prewarm reads the deep-review cache once, not per pick.** New
  `deep_review.cached_review_keys()` (one read) replaces per-row
  `get_cached_review()`, which re-parsed all of `deep_reviews.json` for every
  top-K item.

- **Smaller triage/faithbench code.** Fast-reject/abstract-only summarize
  responses lean on `SummarizeResponse` defaults (drop ~20 restated fields);
  `faithbench.load_jsonl` is a guarded comprehension.

### Removed

- **Dead over-engineering (repo audit).** The unused `TriageRepository` OO facade
  (zero prod callers; tests now use `with_db_path`) and the `TriageJobService`
  class (→ module function `new_job`; its dead `public_job`/`TriageJob` path,
  used only by a test, dropped). ~70 LOC.

### Fixed

- **"Needs library login" was misleading + over-fired.** A scholarly item whose
  landing page declares NO real PDF (e.g. a Nature news/comment `d41586` piece — web
  content with a DOI) was routed to the paywall rung and reported `needs_library_login`,
  even though the user uses a browser-cookie session (no in-app login exists). Now the
  browser rung renders such a page (`render_fallback`, gated by `review_web_articles`)
  so it gets a verdict; `needs_library_login` fires ONLY when a real `citation_pdf_url`
  PDF exists but is gated at a publisher the cookie-source browser isn't signed into —
  and the message says so ("open it in your browser / sign into that publisher"), not
  "open Settings → University access". Verified live: a Nature news piece and an Ovid
  NEJM-AI case study both rendered to full text via the user's Chrome session (17.8K /
  36.4K chars) instead of failing.

- **Paywalled publisher PDFs (Nature/Springer/Elsevier…) now fetch via the browser
  rung — and the 20 MB size cap no longer drops them.** Two fixes: (1) `_drive_browser`
  follows a landing page's `citation_pdf_url` meta (the Highwire tag publishers expose)
  and fetches it through the cookie'd context, so the orchestrator gets the real PDF
  from a paywalled landing URL; (2) the PDF size cap was raised 20 MB → **50 MB**
  (`quality_review`/`full_text_refine.max_pdf_bytes`, `pdf_fetch._DEFAULT_MAX_BYTES`) —
  a figure-heavy clinical PDF runs >20 MB and was being fetched then rejected. Verified
  live: a 20.5 MB / 24-page Nature Medicine paper fetched end-to-end via the user's
  Chrome session (`cookie_browser=chrome`).

### Added

- **Gated picks surface as one-click sign-in links.** When the fleet can't fetch a
  paywalled paper (session stale/absent at that publisher), `status()` now returns
  `needs_login_items: [{item_key, title, url}]` and the Suggested-verdicts bar renders
  each as a link — open it, log in (refreshing the session the fetch reuses), then
  Predict again. Replaces the prior in-app-login prompt the user never set up.
- **Review fleet now reviews web articles (blogs/Substack/news), not just PDFs.** The
  top reading-queue picks were often web articles whose full text is HTML, so the
  PDF-only fleet skipped them ("no fetchable PDF"). New `_pdf_acquire` web-article rung
  renders such a page to a PDF (`browser_fetch.render_article_pdf`, headless `page.pdf`)
  so the existing review pipeline digests it. Gated by `quality_review.review_web_articles`
  (off by default; needs the `browser` extra). A `scholarly = arxiv_id or doi` split keeps
  academic papers on the paywall/browser rung and pure web pages on the renderer.
  Verified live: eugeneyan blog → 361 KB PDF, 12.3K chars extracted.

- **Docs: cover flagship journals, not just sub-journals (coverage-gap fix).** A
  flagship-venue gap let *"Towards autonomous medical AI agents"* (Nature, 2026) slip
  past triage — the user tracked Nature sub-journals but not flagship Nature, the paper
  had no preprint, and PubMed hadn't indexed it. docs/usage.md now lists verified
  flagship RSS (Nature, Nat Commun, Nat Biomed Eng, Science, NEJM, Lancet, Cell) with
  the principle + that PubMed F1–F4 backstop the indexing lag. No code.
- **HackerNoon documented as a triage source (practitioner/engineering angle).**
  Zero code: a tag-filtered RSS feed (`hackernoon.com/tagged/llm/feed` — validated
  on-point for LLM/agent engineering; the narrower `ai-agents`/`agentic-ai` tags
  don't exist) flows through the same Zotero-RSS pipeline. docs/usage.md notes the
  caveats: triage is title-driven (the tag feed carries no full abstract) and it's
  triage-only (blogs have no PDF/DOI/prestige, so deep-review/ask-paper don't apply
  — read on the web).
- **PubMed as a first-class triage source + PMC full-text rung.** Ingestion is
  zero-code (the pipeline reads Zotero feed items, never parses RSS), so a PubMed
  saved-search RSS feed flows through gate → goal_sim → slate like arXiv/bioRxiv.
  docs/usage.md ships four validated medical-agentic-AI / oncology query feeds
  (live-checked against NCBI: "agent" anchored to AI to dodge the pharmacological
  flood; `[tiab]` over MeSH because MeSH indexing lags ~60% on fresh papers). New
  `integrations/pubmed.py` resolves PMID/DOI → PMC PDF URL (keyless ID-Converter)
  so `_pdf_acquire` can fetch papers in PMC with **no DOI** (e.g. AMIA proceedings)
  that the DOI-keyed Unpaywall/OpenAlex rungs miss — via the browser rung, since
  fresh PMC is bot-walled headless.
- **University browser access for the review fleet's PDF fetch.** Non-arXiv /
  paywalled picks (bioRxiv, Nature, journal DOIs) can now be reviewed: `_pdf_acquire`
  resolves arXiv → Unpaywall OA → OpenAlex `oa_url` (headless) → a real browser
  (`integrations/browser_fetch.py`, optional `[browser]` extra = patchright) driving a
  persistent profile the user logs into once via Settings → University access
  (`POST /api/library/university-login`). New `university_access` config (optional
  EZproxy prefix; blank = SSO/OpenAthens).
- **Reuse an existing browser login instead of a second in-app sign-in.**
  `university_access.cookie_browser` (Settings picker: chrome/firefox/edge/brave/…)
  reads that browser's session cookies (`browser-cookie3`, optional `[browser]` extra)
  and injects them into the fetch context — so a paywalled paper you can already open
  there downloads without logging in again. Degrades to the in-app login when the
  store is unreadable or the session expired. NOTE: **Safari is unreadable on macOS
  15+/26** (Apple hardened its cookie container — Full Disk Access can't reach it);
  use Chrome/Firefox or the in-app login.

### Changed

- **Deep-review a 2nd paper while a 1st is still running.** `deep_review` was global
  single-flight, so a per-paper "Run deeper review" on paper B while A ran was silently
  rejected AND B's panel showed A's progress. It's now per-item jobs over one
  provider-aware pool (mirrors `paper_render`): a remote/API provider reviews papers
  concurrently, a local one queues the 2nd (RAM-safe); each panel polls
  `status(item_key)` for its OWN progress; re-running the same paper is a no-op.
- **Review-fleet deep reviews run in parallel for a remote/API provider, serial for a
  local one.** Two fixes: the fleet now batches its picks into ONE `deep_review.start`
  call (was: one paper at a time through the single-flight latch); and the N-paper
  fan-out width comes from `deep_review_fleet_concurrency` — a remote batch fans out
  capped by the provider's `max_sub_concurrency` (else all N), NOT the global
  `TRIAGE_JOB_CONCURRENCY` (a local-RAM triage knob a user may pin to 1, which used to
  silently serialise a remote batch). PDF acquisition between passes stays sequential.
- **UI clarity pass, pt.2.** Settings: University-access folded into the one
  config form (3 saves→1, 6 save-states→1); Refresh-labels card, retrain classifier
  dropdown, corpus-similarity + ML-tuning knobs removed (server defaults kept).
  Library search is semantic-only (Meaning/Exact toggle + Search button gone);
  filter model drops minScore/scored; Zotero menu 4→2. VerdictPanel fixes the
  dont_read-renders-green bug + read-state guard (no model preselect). AnnotationVerdict,
  Ops (Pending/Review/Triage), PaperReview, and the Setup wizard trimmed; one shared
  `<Button>`. Bundle 464→446 kB.
- **UI clarity pass (subtraction-first), pt.1.** One tone vocabulary
  (`ui/Badge`→canonical `CHIP_TONE`); Today drops PipelineFunnel/Refresh/telemetry;
  cull `PaperCard` loses relevance+prestige bars+bucket badge (one relevance scale);
  `ModelCard` 14→5 audit fields; Library band-filter = histogram bars only; Review =
  one verdict row (no dup Approve/Reject). Laws: Occam/Miller/Hick/Working-Memory.
- **Review fleet reviews from a local cache, not a Zotero attachment.** An acquired
  PDF is injected into `deep_review` via new `start(pdf_overrides=…)` and reviewed
  from that path with NO Zotero write, so verdicts work while Zotero is open. Outcome
  taxonomy is now honest per-pick: `no_fetchable_source` (web/no source) vs
  `needs_library_login` (proxied source, not logged in) — PredictionsBar names the
  real reason instead of guessing "no arXiv link, or Zotero was open". `_extra_layers`
  split into `_deep_review_layers.py` to keep `deep_review.py` under the 500-LOC cap.

### Fixed

- **Gate-only backlog drain crashed on title-only items + now derives their abstract.**
  One RSS item with a title but no abstract raised `RuntimeError: gate_only triage
  requires a gate prediction`, killing the whole drain (the backlog stayed stuck).
  Fix: `predict` backfills missing abstracts from OpenAlex (`abstract_inverted_index`,
  already cached for prestige — by DOI then title) so real papers become scorable and
  show their abstract; any residual the gate still can't score is a terminal
  `gate_rejected:gate_unscorable:no_abstract` instead of a crash.

- **Temporal-eval `days_since_added=-1` sentinel bug.** `_row_days` parsed the
  feed-row "no date" sentinel `-1` as the *newest* age, so the forward-looking
  holdout was ~94% undated feed rows (88% mass auto-rejects) — `temporal_spearman`
  measured junk separation, not recent reading decisions. Undated rows now sort
  oldest (never held out), and the holdout fraction is taken over the *dated* pool.

### Changed

- **Unchecked Today→library adds downgraded to weak `could_read` (3.0).** A
  provisional "Add" (source=`machine_add`) was a full-strength `should_read` (4.0)
  training/eval label indistinguishable from a verified one. Its effective training
  label is now capped at `could_read` until an explicit verdict/outcome resolves —
  measured to lift honest forward-ranking ρ on real reading decisions ~0 → 0.29.
- **Daemon + active-learning retrains now apply the `hybrid_gt` verdict overlay**
  (threaded `triage_db_path` through `load_or_train`), matching `/admin/retrain` —
  the three paths previously trained on different labels (raw CSV vs. overlaid).

### Added

- **Honest split-by-population gate metrics.** Training metadata + ModelCard now
  report `oof_spearman_verified` (OOF on dated reading-decisions — the gate's real
  ranking ability, ~0.14) alongside the aggregate `oof_spearman` (inflated by ~72%
  trivially-rejected feed rows). Recency-weighted training was measured (half-life
  sweep) and **rejected** (hurt forward ρ); the negative is recorded in `label_weights`.

- **Paper-quality benchmark harness.** `tools/bench_paper_quality.py` scores the
  pipeline against an Opus-4.8-authored, frozen, FIREWALLED gold set across 4
  deterministically-graded tracks — paper-type detection, checklist↔gold Cohen's κ,
  self-verify precision/recall + false-positive rate, and Docling-vs-fitz recall — with
  faithbench-style rigor (mean+median, std/SEM across run-means, tri-state, resumable).
  Unit-tested graders + firewall (`tests/test_bench_paper_quality.py`). The `--provider sota`
  flag routes to the SAME remote client production deep-review uses (kather/`sota`, via
  `resolve_stage`), so the production grader can be benchmarked, not just the local feed
  grader. Local thinking-model latency is unblocked via an ollama native `/api/chat`
  `think:false` shim (`/v1 enable_thinking:false` + `/no_think` are ignored by qwen3.5).
- **Benchmark gold widened 5→12 papers (11 types) + production-grader comparison.**
  `gold_v1.jsonl` now spans diagnostic-accuracy, empirical-ML, dataset-benchmark, position,
  clinical-prediction (TRIPOD), RCT (CONSORT-AI), systematic-review (PRISMA), narrative-review
  (SANRA), case-report (CARE), theory, and survey — clinically weighted, each row's band/grade
  derived by the real `coverage_grade` (no hand drift; the 5 originals reproduce exactly).
  Widened results: **type detection 0.917** (11/12 — down from the small-N 1.00, one real
  `clinical_prediction`→`empirical_ml` confusion). The headline finding (see Fixed): the quality
  band collapsed to `flag` — the benchmark localized THREE stacked conservatism layers (verbatim
  grounding [fixed], self-verify demotion, overstatement red-flag).
- **Benchmark the production grader at the production tier (the "grader gap" was a measurement
  artifact).** The sota runs were initially at `--max-chars 12000` (the LEAN ollama tier), but
  production sota uses **60000** + `self_consistency=3`. Re-run at the production tier (no other
  change): checklist↔Opus **κ 0.32→0.71**, band exact-match **6/12**, within-±1 **1.00**,
  coverage-MAE **0.08** — the production grader was never broken, it was being measured starved.
  **REJECTED (with numbers):** per-criterion evidence retrieval (decompose-verify / VeriScore
  lineage) was implemented + benchmarked and was a NET NEGATIVE vs the full-body baseline
  (band-exact 6/12→1/12, κ 0.71→0.665, coverage-MAE 0.08→0.42 — restricting the grader to top-k
  retrieved chunks loses the holistic view); code reverted. Residual (within-±1, not chased): the
  structural EMP-leakage red-flag over-fires on empirical-ML papers (transformer/bert/chexnet).
  Reports: `data/paper_quality_bench/runs/{before_fix,sota,sota_fulltier,sota_part2}/`.
- **Proven: the 3 conservatism fixes improved the PRODUCTION grader — not just the tier.** A clean
  A/B at a CONSTANT 60K tier — pre-session grader (verbatim grounding + skeptical self-verify + ≥2
  overstatement) vs current (fuzzy grounding + confirm-by-default + ≥3 overstatement): band-exact
  **2/12→6/12**, κ **0.618→0.710**, within-±1 **0.83→1.00**, coverage-MAE **0.570→0.080**,
  over-flagging **18→8 of 24 paper-runs** (gold has 1 flag). The earlier "collapse to flag" was REAL
  at the production tier (verbatim grounding zeroed coverage even at 60K), not a lean-tier artifact —
  Layer 1 (fuzzy grounding) is the unlock. Run: `data/paper_quality_bench/runs/sota_presession`.


- **Shared UI foundation (whole-app UX pass).** New reusable primitives that
  replace per-page ad-hoc copies: `components/ui/{Spinner,Skeleton,Async,Badge,
  HintBanner}.jsx` (Badge = `StatusPill`/`PriorityBadge`/`ActionBadge`),
  `utils/humanizeError.js` (strips `HTTP NNN:` prefixes, maps statuses, never
  renders `[object Object]`), `components/paper/PaperDetailView/` (one configurable
  paper-detail assembly replacing the duplicated bodies in Annotate + Library
  InlineAnnotate), and hooks `useKeyboardNav` / `useOptimisticAction` /
  `useFocusOnChange` (generalized from Annotate). Behavior-preserving consolidation.
- **Power-tool interaction parity.** Pending Changes gains keyboard nav
  (j/k move · space select · a apply · r reject), optimistic apply/reject with
  rollback, focus-follows-action, and an "HTML allowed" hint on the note editor.
  Triage Monitor surfaces the previously buried Approve/Reject in a sticky per-row
  action bar, adds keyboard nav over the jobs list, and auto-refreshes calibration
  when a job finishes.
- **Config-UX simplification — backend.** New `services/setup/` domain + the
  `/api/setup/*` endpoints (frozen contract) backing a first-run onboarding flow:
  - `GET /api/setup/status` — one readiness probe across config / LLM (default
    provider, key-PRESENCE bool, advisory reachability) / filesystem paths /
    Zotero / trained classifier, with a `ready` gate.
  - `GET /api/setup/detect-zotero` — read-only per-OS probe for likely Zotero
    data dirs (db_exists first).
  - `PUT /api/setup/paths` — write the allowlisted `PDF_ROOT` / `ZOTERO_DATA_DIR`
    keys into `.env` (byte-for-byte preserving all other lines; 422 on a
    non-existent path or a non-allowlisted key).
  - `POST /api/setup/validate-config` — dry-run GoalsConfig validation
    (`field_errors`) + an optional default-provider connection probe; persists
    nothing.
- **`zotero-summarizer setup`** — an interactive terminal onboarding flow that
  reuses the same `services/setup` primitives (no duplicated logic with the HTTP
  layer).
- **Phase-0 bootstrap** — on `serve` startup, absent `goals.yaml` / `.env` are
  created from safe defaults (the `.env` secret placeholder is commented, never a
  real key) and the triage DB is migrated when absent. Idempotent; never
  overwrites existing files. Removes the manual `cp *.example` + `migrate` steps.
- `models/setup.py` — the Pydantic contract for `/api/setup/*`. `api_key_env` is
  only ever an env-var NAME; key presence is a BOOL — no secret value is ever in
  a response.
- **Config-UX simplification — frontend.** A first-run wizard (`/setup`: Connect
  Zotero → Connect LLM → Describe research) with Zotero path auto-detect, a live
  LLM connection test, and inline validation; a `SetupGate` that redirects a
  brand-new user once (skippable/resumable). The Settings page is re-chunked into
  Essentials (research goals, triage criteria, the default LLM provider, Zotero
  paths) + a single collapsible Advanced disclosure (full stage routing,
  classifier gate, corpus), with a readiness strip (Zotero · LLM · Goals · Model).
  Empty-state "finish setup" cards on `/today` and `/library`.

### Changed

- **Scan-hygiene dedup.** Lifted 3 copy-paste idioms to shared helpers: prewarm-k
  → `_flight.resolve_prewarm_k`, atomic JSON writes → `_common.write_json_atomic`,
  golden-CSV reads → `_common.load_golden_rows` (+`now_iso` aliases `now_iso_z`).
  Allowlisted 6 frozen faithbench/quality_eval slop nits.

- **Review fleet — "Predict next 5" advances.** Re-running the fleet now skips
  picks that already have a proposed verdict or a user label and pre-decides the
  next 5 *undecided* picks instead of re-chewing the same top ones (`_select_keys`
  over a wider queue window). Button renamed "Predict next 5"; `top_k` 10→5.
- **Auto-rescore the library after a big backlog drain.** When a drain adds ≥
  `ZS_AUTORESCORE_MIN_ITEMS` (default 10) new items, the whole library is rescored in
  the background (single-flight; no-op if one is running or the gate isn't ready) so
  fresh papers get a relevance score without a manual Rescore. Label fetch FROM Zotero
  is already continuous (the queue reads verdict/emoji tags on every build); writes
  stay behind the explicit Sync button.
- **Honest quality calibration scaffold.** New `quality_calibration` +
  `GET /api/library/review-fleet/calibration`: agreement (and Cohen's kappa) between
  the fleet's proposed verdicts and your confirmed labels, flagged `insufficient`
  until enough matched pairs accumulate — so the panel presents agreement as
  self-consistency across runs, not human-validated accuracy (the method clause now
  says so).
- **Quality in ranking + a "quality papers" filter.** Deep-review grade/band now ride
  on each queue row; a bounded quality lift (`_ranking._quality_bonus`, capped so it
  can't cross a relevance band or override the measured goal/prestige blend) floats
  well-graded papers up; a Quality (A/B · C/D) filter chip appears once rows are graded.
- **Expanded review opens in a right-side panel.** The full review/editor was a
  full-width block below the row; it now renders in a sticky right-hand panel (~44%,
  stacks on mobile) so the queue stays readable while you decide. Figure captions no
  longer double-render (drop the label line when the caption already begins with it).
  Zotero `itemType` is now read and used as a weak prior for paper-type detection.
- **Read-next layout — three labelled regions (Laws-of-UX).** The main panel was
  one undifferentiated column that put Predict next to the Zotero "last synced"
  line, so a Predict click looked like it should change a Sync timestamp. Split
  into `Find` (search + smart filters) / `Review queue` (a dedicated
  `PredictionsBar` + the ranked list) / `Export to Zotero` (the whole-library
  writes + their last-synced status), separated by the primitives' hairline rhythm.
  Removed an orphan `NotConfiguredCard` that rendered for already-configured users;
  the action `StatusBanner` moved to one shared slot at the bottom.
- **Review surface — aggressive subtract (Laws-of-UX).** Decision-only by default:
  the proposed-verdict card drops its duplicate grade chip / rationale / flag pills
  (flags still gate one-tap Confirm via one terse note); the expanded review drops
  the `digest:` chip and folds TLDR, method clause, decisive signals, overstated
  claims, the full checklist, legend and the 15-row digest into one "Details"
  disclosure. Each of verdict / grade / flags / rationale now renders once.
- **Paper-review render — SOTA flatten (Laws-of-UX).** Killed the "embedding in
  embedding in embedding": the in-app brief `<iframe>` (a whole second design
  system) is gone — the review now renders natively from the cached `deep_review`
  via one `PaperReview` component + shared primitives (`paper/review/{tones,
  primitives,briefModel,PaperReview}`), so the digest is shown once (was twice).
  Nested cards collapsed to one container + hairline dividers + reading-grade type
  (13–16px / 66ch, was 10–11px); `PaperDetailView` Decide/Act boxes, the teal
  `InlineAnnotate` card, the indigo DigestBlock, the emerald "Previously" box and
  the slate Ask answer cards are all flat now. `PaperReaderPane` shows native
  figure thumbnails + "Open full brief ↗" instead of the iframe. Grade/decision/
  band colours consolidated into `tones.js` (removed `GRADE_CLS`/`DECISION_CLS`/
  `PROPOSAL_CLS` drift). The standalone `presentation.html` got a matching visual
  refresh (reading scale, less box density, dark-mode parity) — class names,
  decision-aid copy and audit invariants preserved.
- **Settings simplification (Laws-of-UX).** Removed the legacy
  `llm.draft_model / refine_model / api_base / api_key_env` inputs (duplicated the
  `llm_routing` editor — Occam's Razor; the backend still auto-migrates the legacy
  block). Classifier-gate sub-fields now render only when the gate is enabled
  (Hick's Law). The LLM API secret is name-only in the UI — never a raw-secret
  field.
- `vite.config.js` — the dev `/api` proxy target is now `VITE_API_TARGET`-overridable
  (defaults to `http://localhost:8000`), so a sandbox backend on another port can
  be previewed without editing the config.

- `services/llm/operational_check.py` — extracted a public
  `probe_provider(provider, model)` as the single shared probe mechanism behind
  both `check_stages()` and the setup config-draft validator (one probe, not two).
- **Layering fix:** the Settings ModelCard handler (`model_card` +
  `_model_dir` / `_load_latest_runlog_entry`) moved from `api/routes/admin.py`
  to `services/model/model_card.py` (no api→api import); `admin.py` re-exports it,
  so the `/api/admin/model` route and behavior are unchanged.

### Fixed

- **"Predict next 5" silently did nothing on a heavily-labeled library.** The
  fleet ranked only a 40-row window, but the queue pins labeled papers to its top,
  so every window row was already-decided → 0 undecided picks selected. It now
  scans the whole ranked library. It also **fetches the arXiv PDF** for a PDF-less
  pick (backup-first, connector-guarded) and re-reviews it, and **recomputes stale
  digest-less cached reviews** instead of skipping them forever — so re-running
  keeps adding deep reviews + proposals.
- **`serve` died with `Errno 48 address already in use` when a previous server
  was still running.** It now reclaims its port first (`lsof` discovery →
  SIGTERM, then SIGKILL if it clings), so a re-run replaces the old instance.
  Opt out with `--no-kill`.
- **Deep review crashed with `'str' object has no attribute 'model_copy'` on an
  empty/malformed LLM completion.** onprem's `pydantic_prompt` returns the raw
  string when its parser can't build the model; `assess_digest` then `.model_copy()`'d
  a str. `assess_digest` now salvages a JSON blob with `extract_json_blob` (mirroring
  the existing pattern in `triage/summarization`) and raises cleanly on a truly empty
  completion — caught at deep_review's per-item boundary. Verified live (full digest +
  Zotero note, no crash).
- **Deep-review failures were always blamed on an unreachable endpoint.**
  `_summarize_errors` appended "endpoint … may be unreachable" to every all-failed
  run, even a parse/validation error where the endpoint clearly responded —
  misdirecting debugging. The suffix is now gated on a connectivity-looking error;
  other causes are reported verbatim.
- **Library Rescore / Sync reloaded the MiniLM embedder once per 50-item predict
  batch (178× in ~3 min).** `_build_aux_providers` built a fresh `EmbeddingCache`
  (instance-local model memo) per `gate.predict()`. `_resolve_embedding_cache` now
  reuses the runtime singleton. Verified: a full 1877-item rescore = **0 reloads**.
- **Ask-the-paper returned an unhandled 500 on empty/unparseable LLM output.**
  `ask_paper` now catches the parse `ValueError` and abstains (untrusted LLM output
  at the boundary), without changing faithbench's distinct exception-counting.
- **"Predict next 5" reported every paper `failed` when a deep-review job was already
  in flight** (e.g. the startup prewarm). The fleet read a foreign job settling as
  "our item done". `deep_review.start()` now returns `accepted`; the fleet waits then
  re-claims the slot for its own item.
- **Retrain double-ran on a fast double-click.** The `_RETRAIN_LOCK.locked()`
  precheck raced (the worker acquired later, on its own thread). `retrain()` now
  claims the lock synchronously; the worker releases it on every exit path.
- **Sort-ranks (Call Number) re-stamped every item every run** even when ranks were
  unchanged — a no-op `set_field` touches `dateModified`/version → a full-library
  phantom sync. Now skips items whose Call Number already equals the computed rank.
- **Trash 500'd the whole batch when Zotero held the DB lock**, after the dont_read
  labels were already committed (partial state). `mark_feed_items_read` is now
  best-effort: reports `marked_read: 0` + `marked_read_error` instead of failing.
- **Test/hygiene:** fixed a stale `test_offline` mock (missing `quality_review` →
  `AttributeError` once `_model_targets` started reading `shadow_claim_check`) and
  removed a stale `slop_allowlist.txt` grandfather (`quality_eval.py:73`, no live
  finding) so `test_allowlist_reconcile` passes.

- **Quality band: near-perfect-metric red flag false-fired on the Adam β2
  hyperparameter (the within-±1 residual).** The structural EMP leakage red-flag
  matched ANY bare near-1 decimal (`0.98`/`0.999`) — i.e. the Adam β2 optimizer
  setting present in essentially every modern ML methods paper — so it capped
  Transformer (β2=0.98) and BERT (β2=0.999) to `flag` despite an otherwise A-grade
  0.8/0.833 coverage, where the Opus gold says `neutral`. Receipts: both papers had
  `missing_critical=1` (so the ≥2-critical rule was NOT the cause) and the structural
  flag fired on the β2 decimal. Fix: a near-1 value counts as a HEADLINE METRIC only
  when a performance-metric word (accuracy/AUC/F1/…/"reached") sits within ~40 chars
  (`_near_perfect_metric` in `quality_eval.py`); a bare hyperparameter decimal no
  longer trips it, while a genuine "0.99 AUC … no leakage" still does. Surgical: of
  the 12 gold papers the old flag fired only on chexnet/transformer/bert (all β2/
  near-1 false positives) and the new gate fires on none; chexnet stays `flag` via
  its 3 missing-criticals. Both targets → gold `neutral`; band exact-match **6/12→
  8/12**. Regression test `tests/test_quality_eval.py`.
- **Quality checklist grounding was too strict (collapsed coverage to ≈0).**
  The shared `quote_is_grounded` required a VERBATIM span, but the rubric LLM (local
  AND the remote `sota` grader) paraphrases ~2/3 of its evidence quotes → almost no
  "yes" grounded → checklist coverage ≈ 0. Fix: `quote_is_grounded(..., fuzzy=True)` —
  an NFKC-normalized token-`SequenceMatcher` that grounds a paraphrase whose tokens
  form contiguous runs covering ≥80% of the quote, while still rejecting hallucinated
  / scattered-word quotes. Opted into by the CHECKLIST only; `library.qa` + goal
  summaries keep the strict verbatim bar (faithbench abstention guard unchanged by
  construction). Measured on the 12-paper benchmark: checklist coverage-MAE vs the
  Opus gold **0.72→0.47**; with self-verify off, marcus now matches gold EXACTLY
  (highlight/A/1.0, was flag/D/0.0). Regression test `tests/test_grounding.py`. NOTE:
  the band still skews `flag` because two *downstream* conservatism layers over-fire on
  the now-grounded items — self-verification demotes correct grounded criticals
  (marcus highlight/A→flag/D) and the overstatement red-flag caps a perfect-coverage
  paper (case_report cov 1.0/grade A → band flag); both tuned below.
- **Quality band over-conservatism: self-verify + overstatement red-flag over-fired.**
  Once grounding was fixed, the band still skewed `flag` because two downstream safety
  layers over-fired on the now-grounded items. (a) The self-verification 2nd pass demoted
  CORRECT grounded critical items — `SELF_VERIFY_PROMPT` reframed from "skeptical reviewer,
  REJECT over-claims" to **confirm-by-default, reject only when confident** the quote fails
  the criterion (keeps catching the clear over-claims its Track-3 eval validated). (b) The
  abstract-vs-body overstatement check capped a perfect-coverage paper to `flag` on ≥2 (often
  false-positive) overstatements — `OVERSTATEMENT_PROMPT` now flags only CLEAR/material
  over-claims, and the red-flag gate raised 2→**3** so a soft cluster can't sink a well-covered
  paper (`quality_eval.py`). Measured on the 12-paper sota benchmark before→after: band
  exact-match **0.167→0.333**, within-±1 **0.58→0.83**, coverage-MAE **0.47→0.30**, κ
  **0.32→0.44** — band distribution went from all-`flag` to highlights/neutrals appearing;
  marcus + case_report now match the Opus gold exactly. Regression test in
  `tests/test_quality_eval.py` (2 overstatements no longer flag a covered paper; 3 do).
- **Self-verification 2nd pass (catches the LLM positivity bias).** After the rubric,
  one extra short LLM call re-checks the CRITICAL items marked met — does the grounding
  quote ACTUALLY establish the criterion, or did the first pass over-claim? Rejected
  items are demoted to missing-critical (drops the band) and recorded in
  `QualityEval.self_verification_demoted`. Config `quality_review.self_verification`
  (default on). Proven live on local ollama: it demoted "internal CV mislabeled as
  external validation" and confirmed a real external cohort (`tools/eval_self_verify_live.py`).
- **Optional Docling PDF parser (structured tables + figure captions).** Gated by
  `quality_review.use_docling` (needs `uv pip install docling`); fitz stays the default.
  On a real PDF, fitz extracted 0 tables / 0 figures where Docling recovered 2 structured
  tables + 3 deduped figure captions — the fix for truncated tables / mis-extracted
  figures (`tools/eval_docling_vs_fitz.py`).
- **Paper-type fallback no longer mis-routes consensus guidelines.** The low-confidence
  supertype keyed "has experiments" on a generic methods section, so a Delphi consensus
  guideline (e.g. ESMO EBAI, which describes its *Delphi methodology*) fell back to
  `generic_empirical` and could still draw empirical critiques. It now keys on the
  strong we-built/ran-it signals (`propose`/`rct`) → routes to the review supertype.
  Surfaced by a real-paper evaluation (CheXNet / a LLM survey / ESMO EBAI) run through
  the live gates (`tests/test_real_paper_eval.py`).
- **Deep review judged every paper by the same empirical-ML rubric.** A review/policy
  paper was flagged for "no ablation / no dataset split / no leakage discussion" and a
  cited high number tripped the leakage red-flag. Deep review now DETECTS the paper type
  (`paper_type.detect`: structural signals + itemType prior + one LLM call, safe-supertype
  fallback) and judges it against the recognized standard for that type
  (`_paper_type_checklists.CHECKLISTS`, each item citing its EQUATOR/source URL): SANRA
  for narrative reviews, PRISMA/AMSTAR-2 for systematic reviews, TRIPOD+AI/CLAIM/CONSORT-AI
  for clinical, REFORMS/leakage-taxonomy for empirical ML, etc. Structural leakage flags
  fire only for empirical types.
- **Quality scores were unvalidated 1-5 LLM self-reports.** The band + A–D grade are now
  DERIVED from transparent checklist COVERAGE (weighted % of applicable items met, N/A
  excluded, critical items double-weighted) via the pure `coverage_grade` — no LLM number
  in the headline. New `coverage_*`/`missing_critical`/`paper_type` fields on `QualityEval`.
- **Red flags showed near-duplicates; the gloss contradicted them.** The 3 self-consistency
  runs phrased one concern differently and the exact-`set()` dedup kept all — now merged by
  token-Jaccard (`_dedupe_near`). The QUALITY gloss is derived from the actual red-flag list
  (`_gloss` / `bandGloss`) so it never says "No red flags" while listing some (server brief
  + React `PaperReview`).

- **"Predict next 5" silently did nothing.** The fleet counted every *processed*
  pick as `completed`, so a run over PDF-less papers reported `completed=N →
  status:"ready"` with zero proposal cards — indistinguishable from success. Now it
  tallies `proposed`/`skipped_no_fulltext`/`failed`; a run that decides nothing
  surfaces as `status:"done_empty"`, and the UI names the cause ("no full text → use
  Fetch full text") instead of going quiet. Cold-cache runs now show live "paper i
  of n — building its deep review" progress (`fleet.py`, `test_review_fleet_job.py`).

- **"Triage backlog" silently did nothing.** Undeclared+uninstalled `lightgbm` → gate
  stayed `None` → the gate-only drain crashed per-item, swallowed unlogged. Declared
  `lightgbm`; added `services/readiness.py` (boot log + `setup/status.subsystems[]` +
  `require()` 503 guard on the drain route); drain boundary logs; Today shows the error.

- **Pending Changes: no more React "setState during render".** The inline
  `ChangeEditor` lazily seeded its draft *during render* (a setState-in-render
  anti-pattern that logged a console warning every render); it now derives the
  displayed value via a pure read-through, with the save path's existing
  `buildDraft` fallback covering an unedited change. Caught by live preview, not
  by build/test/lint.
- **First-run wizard no longer traps the user on the LLM step.** Next now gates on
  a structurally-valid provider (type + base URL + key env-var name + model), not
  on a passing live connection test — a new user whose secret/endpoint isn't ready
  yet (the name-only posture sets it outside the app) can still finish setup. The
  connection test is advisory; its error drops the raw `HTTP <code>:` prefix.
- **Wizard progress indicator** no longer shows a later step as "done" (green ✓)
  before it is reached — a step is credited only once it is both reached and valid.
- **First-run no longer shows raw errors behind the setup card.** `/library` and
  `/today` gate their Zotero-backed fetches on a connected reader, so an
  unconfigured user sees only the "finish setup" card (was: "Failed to load
  sidebar" + "Failed to load queue: Unexpected server error").
- `GET /api/library/reading-queue` returns a clean **503 `zotero_unavailable`**
  when Zotero isn't configured (matching the `/api/zotero/*` routes) instead of a
  500 from an unhandled reader-unavailable error.
