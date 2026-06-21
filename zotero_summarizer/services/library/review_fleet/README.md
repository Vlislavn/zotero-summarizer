# services/library/review_fleet — background pre-decision of Read-next verdicts

Phase-2 fleet. It pre-decides a reading verdict for the top-N Read-next picks in the
background so the human only **Confirms** or **Overrides** — never decides from a
blank slate. The expensive judgement already happened inside `deep_review`
(read/skim/skip, the A–D grade, the abstract-vs-body overstatement check, the 3-band
quality verdict, the per-goal board), cached in `deep_reviews.json`. This fleet
**reads those cached signals** and folds them into a `ProposedVerdict`. It makes **no
LLM call** of its own.

```
reading_queue next-K undecided ─fleet (serial, single-flight)─┐
                                                    │  per item:
   deep_review.get_cached_review(key) ─hit──────────┤   reuse cached digest+quality
        │ miss                                       │
        └ deep_review.start([key]) ─poll till done──┘   (one model load at a time)
                                                    │
   propose.propose_verdict(digest, quality, goals) ─┤   pure truth-table, NO LLM
                                                    ▼
   verdict_store.upsert(key, ProposedVerdict) ─> data/<model_dir>/proposed_verdicts.json
                                                    │
   reading_queue.build_reading_queue ─reads sidecar─┘  rec["proposed_verdict"] = …
```

| file | responsibility |
|---|---|
| `propose.py` | the **pure, deterministic, LLM-free** truth-table: `propose_verdict(digest, quality, *, goal_summaries)` → `ProposedVerdict`. Maps the digest's `read_decision` (read/skim/skip) × `grade` (A–D) × goal-match × quality signals (`quality_band`, `overstatements`, `red_flags`) to one of `must/should/could/dont_read` + `confidence` + `rationale` + `flags`. **Asymmetry (load-bearing):** a wrong HIDE costs more than a wrong KEEP, so a `skip` may propose `dont_read` ONLY on a REAL goal-miss (the board was evaluated and none fired). A goal MATCH, or an UNKNOWN board (`None`/empty/malformed — e.g. the goal-summary LLM call errored and `deep_review` swallowed it to `None`), keeps `could_read`: absence of evidence is never evidence to hide. No digest at all → a safe `could_read`. An unknown-goal skip is also kept below the card's 0.6 one-tap-Confirm floor so a human checks it. Confidence is cut (and a flag added) when `quality_band == "uncertain"` or any overstatement, so the UI foregrounds the proposals worth double-checking. Side-effect-free → fully unit-testable without a model. |
| `verdict_store.py` | atomic JSON sidecar at `data/<model_dir>/proposed_verdicts.json` keyed by `item_key`. `read_all()` / `upsert(key, proposal)` / `clear(key)`. Writes via the shared `_common.write_json_atomic` (tmp+replace) — same idiom as `deep_review`'s cache; path resolved via `classifier_persistence.DEFAULT_MODEL_DIR` (never hardcoded). Holds **suggestions only** — distinct from the triage DB's `label_verdicts` (the user's confirmed labels). |
| `fleet.py` | the single-flight background job (own `FlightLatch`) + `status()` (`{status, total, completed, proposed, no_fetchable_source, needs_library_login, failed, error, started_at, progress}`). `completed` counts rows **processed**; `proposed` counts verdicts **written** — so a run over PDF-less picks (`needs_pdf` / no `digest`) is `completed>0, proposed==0` and reports `status="done_empty"` (the honest "decided nothing"), never a false `ready`. The per-outcome tally names the REAL reason: `no_fetchable_source` (web article / no arXiv / no OA copy) vs `needs_library_login` (a proxied/paywalled source EXISTS but the university browser isn't logged in — actionable). `progress` carries `{item_key, index, total, deep_review}` so a cold pick's long review shows "paper i of n". `_select_keys` takes the next `top_k` **undecided** picks — it ranks the WHOLE library (`build_reading_queue(limit=_SELECTION_SCAN_LIMIT)`, matching the UI's QUEUE_LIMIT) then drops rows that already carry a `proposed_verdict` (the fleet's own output) or a `user_priority` (a human label), so a **re-run advances** down the queue. A fixed window was the silent-no-op bug: the queue PINS labeled papers to its top, so on a heavily-labeled library a small slice was all-decided and selected ZERO undecided picks. For each item **serially** (RAM safety): `_ensure_review` (`deep_review.get_cached_review`, or `deep_review.start([key])` then **poll** until it settles — never two model loads at once) → `propose_verdict` → `verdict_store.upsert`. A cached review with no `digest` that is NOT `needs_pdf` is a **stale failure** — treated as a miss and recomputed (deep review works now). When a pick still has no usable full text, the fleet **acquires a PDF** via `_pdf_acquire.acquire_pdf_for` (arXiv/OA headless → the university browser for Cloudflare/SSO paywalls) into a LOCAL cache and re-reviews `force=True` with `pdf_overrides={key: path}` — reviewed FROM THAT PATH, **never a Zotero write**, so a verdict works while Zotero is open. No fetchable source → `no_fetchable_source`; a proxied source the browser can't reach (not logged in / `browser` extra absent) → `needs_library_login`. `start()` reports whether it **accepted** the single-flight slot — when a FOREIGN deep-review job (the startup prewarm, or the user's own "Run deeper review") holds it, the fleet waits for that job to drain then **re-claims** the slot for its own item, rather than reading the foreign job's settle as "our item failed". It gives up on an item only when ITS OWN accepted job produced no cache entry. A per-item failure is logged and skipped; a job-level failure sets `error`. Never writes `label_verdicts` (a suggestion is never an auto-applied label) and — unlike before — performs **no Zotero write at all** (PDFs go to a local cache, not an attachment; the arXiv-attach path now lives only behind the Library "Fetch full text" button). |
| `prewarm.py` | launch-time `schedule_on_startup(config, app_state)`, modeled 1:1 on `deep_review_prewarm`. Spawns the fleet for the top-`quality_review.prewarm_on_startup_k` picks (env override `ZS_REVIEW_FLEET_PREWARM_K`, validated fail-loud; `0` disables). Skipped when deep review is disabled or `zotero_reader is None`. Daemon-thread + best-effort: a failure is logged and swallowed, never blocks boot. |
| `__init__.py` | re-exports `start`, `status`, `schedule_on_startup`. |

**Surfaces:** `reading_queue.build_reading_queue` reads `verdict_store.read_all()` once and
attaches `rec["proposed_verdict"] = proposals.get(item_key)` to each row — **but never
routes the proposal through `_verdict_priorities`**, so a `dont_read` *suggestion* can't
auto-hide a paper (only the user's confirmed `dont_read` label does). Routes:
`POST /api/library/review-fleet/run {top_k}` → `fleet.start`; `GET
/api/library/review-fleet/status` → `fleet.status`. Wired into `lifecycle.startup`
next to the deep-review prewarm.

**Boundaries:** imports `deep_review`/`reading_queue` (sibling library modules),
`_flight` (shared single-flight latch), `models.triage` (`ProposedVerdict`), and
`model/classifier_persistence` (the model-dir path) — standard `services/` rules.
