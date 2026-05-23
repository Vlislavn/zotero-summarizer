# Zotero RSS Feed Processor

The feeds subsystem turns Zotero's RSS-feed inbox into a personal-research filter:
papers you actually want to read land in your **Inbox** collection with tags,
matched user-collection assignments, and a concise note. Everything else gets
triaged, scored, marked read, and stays out of your way.

There are three operating modes:

- **Daemon** (`feeds serve`) — A long-running background process that
  lazy-picks a few unread feed items every N minutes, scores them, marks them
  read, and once per day materializes the 1–2 best into your Inbox.
  Auto-materializes; no human review step.
- **One-shot, review-mode** (`feeds run`, the **default** since Phase 1.14) —
  Exhausts **all** unread items from one or more feeds in a single pass,
  triages each through the classifier gate + LLM, and parks them as
  `awaiting_review`. **Nothing is written to Zotero yet.** Open
  `http://localhost:8000/review` (via `zotero-summarizer serve`) to approve /
  reject / relabel each item with per-paper SHAP score breakdown.
- **One-shot, auto-materialize** (`feeds run --auto-materialize`) —
  The pre-1.14 behaviour: exhaust the feed, then immediately run daily
  selection + write the top-K into Inbox. No review step.

Most users run `feeds run` interactively (review mode) when they want
explicit control, and `feeds serve` continuously when they want hands-off
delivery. The classifier gate (Phase 1.13) sits in front of both: items the
trained model labels `dont_read` skip the LLM entirely.

---

## Target throughput: 1–2 papers per day

The daemon's default config (`goals.yaml feeds.daily_target_min: 1`,
`daily_target_max: 2`) materializes **1–2 best papers into Inbox per day**. With
48 RSS feeds × ~900 unread items per week, no human can read everything; the
selection job runs `kneed`-based plateau detection over the rolling 24-hour
window of triaged items and picks only the elbow.

Continuous lazy-load characteristics:
- Each tick processes a small batch (default 5 items every 5 min) so the daemon
  never blocks for hours.
- Eventually-consistent coverage: across ~288 ticks/day (24 h × 12/hour), 1 440
  items get evaluated — typically enough to drain the day's incoming volume.
- Ctrl-C / SIGTERM finishes the in-flight tick cleanly and exits; nothing is
  half-applied.

---

## Quick start

### Prerequisites

1. Zotero installed at the default location (`~/Zotero`).
2. Zotero is configured to fetch your RSS feeds (Tools → Feeds → … or OPML import).
3. Zotero's "Automatically retrieve metadata and PDFs for added items" is **on**
   in preferences. For the routine inbox flow Zotero downloads PDFs once an
   item lands in the user library. When the optional `full_text_refine` mode
   is enabled (Phase 1.8), the daemon ALSO fetches OA PDFs over HTTP for the
   top plateau picks before materialization, so it can re-score them with full
   text. See [configuration.md](configuration.md#full_text_refine-section-phase-18-two-stage-triage).
4. An LLM endpoint configured in `goals.yaml` and `.env`. Local Ollama works:
   ```env
   OPENAI_API_KEY=ollama
   OPENAI_API_BASE=http://localhost:11434/v1
   ```
   and in `goals.yaml`:
   ```yaml
   llm:
     draft_model: gpt-oss:20b
     refine_model: gpt-oss:20b
     api_base: ${OPENAI_API_BASE}
     api_key_env: OPENAI_API_KEY
   ```

### Inspect your feeds

```bash
zotero-summarizer feeds list
```

Output is a table of all feeds Zotero has imported, with library IDs you can
use to filter later. Example:

```
    ID  NAME                                                       LAST UPDATE
------------------------------------------------------------------------
     2  Agents — Core LLM & Orchestration                          2026-05-13 ...
     3  bioRxiv — Bioinformatics                                   2026-05-13 ...
   ...
```

The NAME column is what you pass to `--feeds` when using substring matching (see below).

### Preview what the daemon will see

```bash
zotero-summarizer feeds preview 2 --unread-only --limit 10
```

Shows the 10 oldest unread items from feed 2. No LLM calls, no writes.

### Run the daemon

```bash
zotero-summarizer feeds serve
```

This is the primary command. Logs go to stderr (and `server.log`). Press Ctrl-C to stop cleanly (in-flight tick finishes first).

To process only a subset of feeds, use either the numeric ID or a **name substring** (case-insensitive; must match exactly one feed):
```bash
zotero-summarizer feeds serve --feeds "Agents"       # substring match
zotero-summarizer feeds serve --feeds 2,3,5          # numeric IDs
zotero-summarizer feeds serve --feeds "Agents,3"     # mixed
```

To run for a bounded number of ticks (useful for testing):
```bash
zotero-summarizer feeds serve --max-ticks 3
```

To temporarily use a different LLM model without editing `goals.yaml`:
```bash
zotero-summarizer feeds serve --model qwen3:8b
```

`feeds serve` acquires a PID lock (`feeds.lock` in the project root). A second invocation while one is running exits immediately with the active PID. The lock is released on clean exit; if the process crashes, delete `feeds.lock` manually.

### One-shot run for a specific feed (review-mode default)

Exhaust ALL unread items from one feed right now and stage them for review in
the UI — Zotero stays untouched until you click Approve:

```bash
zotero-summarizer feeds run --feeds "Agents"
zotero-summarizer feeds run --feeds "Agents" --gate-only        # skip LLM entirely (bootstrap labels fast)
zotero-summarizer feeds run --feeds "Agents" --model qwen3:8b   # different LLM
zotero-summarizer feeds run --feeds 2 --dry-run                 # preview, no writes
zotero-summarizer feeds run --feeds 2 --auto-materialize        # pre-1.14: write directly to Inbox
```

The classifier gate runs first (dropping items predicted `dont_read` per the
trained model's thresholds + the `raw_score_dont_read_below` floor), surviving
items go through the LLM (skipped entirely when `--gate-only`), and everything
that gets a verdict lands in `processed_feed_items` with
`decision = 'awaiting_review'`. Start the web UI to triage them:

```bash
zotero-summarizer serve            # FastAPI on http://localhost:8000
# then open http://localhost:8000 → "Feed Review" tab
```

#### `--gate-only` (Phase 1.14)

Skips the LLM entirely. Each survivor of the classifier gate is recorded as
`awaiting_review` with a **synthesised** `SummarizeResponse` (placeholder
rationale; the SHAP attribution panel does the explaining). Use this when
your golden CSV is still small and you want fast iteration:

```
gate_only run → SHAP-bar review in UI → relabel batch → background retrain
              ↑                                                       │
              └───────────────── tighter thresholds on next run ──────┘
```

Each round of relabels tightens the gate's `t_could` / `t_must` thresholds
(driven by Youden's J on the new training distribution), so by round 3 the
gate is filtering 70-80% of items pre-LLM. Implies review mode; incompatible
with `--auto-materialize`.

#### Feed Review UI

Each row shows the predicted priority chip, composite score, an **open ↗**
link (DOI / arXiv / guid), abstract preview, **TreeSHAP** bars explaining
which features drove the score (semantic match, corpus affinity, prestige,
etc.), and an author/venue context panel (max h-index, venue works_count,
citations). Tabs:

- **Awaiting review** — items the gate kept. Approve / Reject / Relabel
  (must/should/could/dont).
- **Gate-rejected** — items the gate dropped pre-LLM. Approve/Reject are
  hidden (they're already rejected); only **Relabel** is shown. `dont_read`
  on a gate_rejected row means "I confirm the gate was right"; the others
  mean "model false-negative — promote to Inbox + train".

When you've gone through the batch:

- **"Apply N approved → Zotero"** materialises every `user_approved` row
  via `apply_feed_materialization` (the daemon-direct create path — NOT the
  pending_changes pipeline, which was designed for already-existing library
  items). Materialised rows transition to `decision = 'selected'` with
  `decision_reason = 'materialized_via_review_ui'` and get a 7-day outcome
  window scheduled.
- **"Confirm remaining as dont_read"** (gate-rejected tab) — bulk-appends
  every still-untouched gate_rejected row to the golden CSV as `dont_read`.
  Semantics: *"no click = I confirm the model was right"*. Idempotent
  (already-present `item_key`s are skipped). Useful at the end of a review
  session — turns a quick scan into a strong negative-class training signal.

`feeds run` acquires `feeds.lock` in the project root. A second invocation
while one is running exits immediately. Items triaged by a prior run for the
same feed are skipped via the `(feed_library_id, feed_item_id)` UNIQUE
constraint and never re-processed.

### One tick on demand (cron-friendly)

If you'd rather schedule via `launchd` / `systemd` / `cron` than have a
long-running process:

```bash
zotero-summarizer feeds tick --batch-size 5
```

Runs exactly one tick (triage + mark-read + due-outcome resolution +
optionally daily-selection if 24h have elapsed) and exits. Does NOT acquire
the PID lock — designed to run safely alongside a running daemon.

### Manually trigger daily selection

If you want to materialize papers right now instead of waiting for the next
24-hour boundary:

```bash
zotero-summarizer feeds select-daily
```

`--dry-run` is supported: plateau-selects but writes nothing.

---

## What happens during a tick

1. **Round-robin pick**: K unread feed items (default 5) are drawn across all
   feeds. No feed starves; the smallest feeds get a turn too. In review mode
   (`feeds run` without `--auto-materialize`) K is unlimited — the whole feed
   is exhausted in one pass.
2. **Dedup**: items already in `processed_feed_items` (our DB) are skipped, and
   items with a DOI/arXiv already in your library are dropped as
   `rejected_dedup_library`.
3. **Classifier gate** (Phase 1.13): trained LightGBM / TabPFN / LogReg model
   batch-predicts every survivor. Items whose `predicted_priority` is in
   `feeds.classifier_gate.drop_priorities` (default `[dont_read]`) skip the
   LLM and land as `gate_rejected` with `shap_contribs_json` populated. The
   surviving items carry their SHAP contributions + OpenAlex author/venue
   stats onto the LLM path.
4. **Corpus fast-reject**: each survivor's (title + abstract) is embedded
   (sentence-transformers/all-MiniLM-L6-v2) and compared against your existing
   engaged-vs-rejected corpus. If `corpus_affinity < similarity_threshold`
   (default `-0.3`), the item is fast-rejected without LLM calls.
5. **LLM triage**: the rest go through abstract-only refine + triage prompts.
   Output: composite score (1–5), reading priority, dimension scores,
   suggested user-collection matches, tags.
6. **Record**: every item is written to `processed_feed_items`.
   - **Auto-materialize mode** (`feeds serve`, `feeds tick`, or
     `feeds run --auto-materialize`): triaged items go to `triaged_pending`
     and daily-selection materialises the top-K → `selected` / `black_swan`,
     the rest → `rejected_daily_cutoff`.
   - **Review mode** (`feeds run` default): triaged items go to
     `awaiting_review`. Daily selection is skipped entirely so they stay
     until the user clicks through them in the UI.
7. **Mark read**: `feedItems.readTime = datetime('now')` is written for **all
   processed items** so Zotero's unread badge clears. **Skipped in review
   mode** — items stay unread so they keep appearing in Zotero's RSS view
   alongside the web-UI queue.
8. **Resolve outcomes**: up to N (default 3) materializations from prior runs
   whose 7-day window has elapsed are checked — see "Outcome detection" below.
9. **Daily selection trigger** (auto-materialize only): if the time-of-day
   target has been reached today and daily selection hasn't run yet (or if
   ≥ `daily_selection_interval_hours` have elapsed when `daily_selection_at`
   is not configured), run it now.

### Per-tick classifier retrain check

Every tick also calls `_maybe_schedule_gate_retrain`: it `sha256`-hashes
`zotero-summarizer-golden.csv` and compares against the trained gate's stored
sha. If they differ — because Reject/Relabel from the UI appended rows, or
because `goldenset ingest-annotations` ran — a daemon thread fires
`train_and_save` in the background. The current tick keeps using the stale
model; the next tick (after training finishes) sees the swap.

## Review mode (Phase 1.14)

`feeds run` without `--auto-materialize` is the **default** workflow: the
script processes the whole feed, but every triaged paper waits for you to
press a button before it touches Zotero. The loop:

```
feeds run [--gate-only]    →   classifier gate (drop dont_read pre-LLM)
                           →   LLM triage on survivors  (skipped if --gate-only)
                           →   record as awaiting_review
                           →   STOP. nothing in Zotero yet.

serve                      →   FastAPI on :8000
open /review → Awaiting    →   SHAP bars + author panel + open ↗ link
                               Approve / Reject / Relabel
open /review → Gate-reject →   spot-check rejected pile; Relabel false negatives
                               OR click "Confirm remaining as dont_read"
                               (bulk-confirm: untouched items → dont_read in CSV)

"Apply N approved → Zotero" →  apply_feed_materialization (NOT pending_changes;
                               feed items don't exist in Zotero yet — daemon-direct
                               create path: items + tags + note + Inbox + provenance)
                               → row → DECISION_SELECTED + 7-day outcome window
```

### What you see in the UI

For each row the **Feed Review** tab shows:

| Field | Source | Meaning |
|---|---|---|
| Priority chip + composite_score | `processed_feed_items` | What the model predicted |
| `open ↗` link | `doi` → `arxiv_id` → `guid` fallback | One-click to the paper |
| Title + abstract preview | feed item | Title is also a link when the URL exists |
| SHAP bars (top 4 by &#124;contribution&#124;) | `lgb.predict_proba(X, pred_contrib=True)` | Per-feature signed contribution to the logit. Positive (green) bars push toward "read"; negative (red) bars push away. The 768 SPECTER2 embedding dims collapse into one `semantic_match_specter2` bucket; the seven tabular extras (`has_doi`, `has_venue`, `year_recency`, `title_log_len`, `abstract_log_len`, `corpus_affinity`, `prestige_score`) are named individually; `bias` is the model's prior. |
| Author / venue panel | OpenAlex (cached) | Max h-index across authors, venue works_count, current citation count |
| LLM rationale | Triage prompt output (or gate-only placeholder) | Plain-text justification |
| Actions | — | Approve / Reject / Relabel (4 levels) on awaiting; only Relabel on gate-rejected |

The "Apply N approved → Zotero" button is sticky in the header so you can
click as you scroll, then commit the batch. On the gate-rejected tab there's
also a **"Confirm remaining as dont_read"** button — bulk-appends every
untouched row to the golden CSV as `dont_read` (semantics: *"no click =
I confirm the model was right"*).

### How the learning loop closes

Every UI action that mutates the label writes one row to
`zotero-summarizer-golden.csv`. The append helper (`services.library.review.append_to_golden`)
pulls the **full abstract + authors + venue + year** from Zotero's live
`feedItems` table — not from the truncated `summary.abstract_preview` — so
training has real signal, not just a 200-char snippet.

| UI action | Golden CSV row | Why |
|---|---|---|
| Approve | (no row) | Approval just means "yes, materialise this verdict". The model already predicted correctly. |
| Reject | `gold_priority_final = dont_read` | Strongest negative signal. |
| Relabel → must / should / could | corresponding label | Promotes a model mistake into a labeled training example. |
| Relabel → dont_read | `gold_priority_final = dont_read` | Same as Reject (routes through it). |
| **Bulk-confirm gate-rejected** | one `dont_read` row per untouched item | Implicit confirmation — "no click = model was right" |

The CSV's sha256 changes, and the next `feeds run` start (lifecycle.startup
in `load_or_train`) detects the diff and retrains synchronously before the
tick. For a continuous `feeds serve`, the per-tick `_maybe_schedule_gate_retrain`
fires a daemon thread; the current tick keeps using the stale model and the
swap is atomic on the next tick.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/feeds/review?state=awaiting_review` | List rows in `awaiting_review` (default) with parsed SHAP + aux_context + summary |
| `GET` | `/api/feeds/review?state=gate_rejected` | List the gate-rejected pile (same payload shape) |
| `POST` | `/api/feeds/review/{id}/approve` | (awaiting only) flip to `user_approved` |
| `POST` | `/api/feeds/review/{id}/reject` | (awaiting only) flip to `user_rejected`, append `dont_read` to golden CSV |
| `POST` | `/api/feeds/review/{id}/relabel` | (awaiting OR gate-rejected) override priority + append CSV; synthesises a SummarizeResponse on the fly for gate-rejected rows that have no LLM summary |
| `POST` | `/api/feeds/review/apply-all` | Materialise every `user_approved` row → Zotero via `apply_feed_materialization`. Returns `{applied, failed_count, failed: [{id, title, error}, …]}` |
| `POST` | `/api/feeds/review/confirm-gate-rejected` | Bulk-append a `dont_read` golden-CSV row for every still-untouched `gate_rejected` item |

### Decision state machine (review-mode branch)

```
              feeds run [--gate-only]
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
     gate_rejected           awaiting_review
       (~70% in              (the survivors —
        a mature              ranked top → SHAP)
        model)                     │
            │                      │
            │              ┌───── approve ───────┐
            │              │                     │
            │           reject /            relabel ≠ dont
            │           relabel=dont              │
            │              │                     │
            ▼              ▼                     ▼
       (stay or       user_rejected         user_approved
       confirm)          (terminal)               │
            │              │                     │
            │              │       "Apply N approved → Zotero"
            │              │                     │
            ▼              ▼                     ▼
   golden CSV       golden CSV           apply_feed_materialization
   (dont_read       (dont_read           writes item + tags + note +
   via relabel      via reject)          Inbox + provenance tag
   or bulk-                                       │
   confirm)                                       ▼
            │              │              decision = selected
            │              │              decision_reason = materialized_via_review_ui
            │              │              materialized_zotero_key = <NEW>
            │              │              outcome_eligible_at = now + 7 days
            └──────────────┴────────────┬─────────┘
                                        ▼
                           sha mismatch on next feeds run
                           → lifecycle retrains gate
```

`awaiting_review` rows are deliberately invisible to `run_daily_selection` —
the daily-selection query is `WHERE decision = 'triaged_pending'`, so review
items never get auto-promoted while you're still deliberating. Likewise the
review UI's apply path goes through `apply_feed_materialization` (which
creates a fresh Zotero item), NOT through `pending_changes` (which mutates
already-existing items).

---

## Daily selection

Once a day the daemon runs a separate selection pass over the rolling 24 h of
`triaged_pending` items:

- Gather all `triaged_pending` rows from the last 24 h, sorted by composite
  score descending.
- Run `kneed` plateau detection on the score curve.
- Pick `[hard_min, hard_max] = [1, 2]` items at the elbow.
- Optionally pick 0–1 black-swan from the rejected pool if
  `feeds.daily_force_black_swan_every_run: true` and surprise score >= 0.30.
- For each pick, **materialize directly into Zotero** (bypassing the
  pending-changes review queue — feed-sourced creates have low blast radius):
  1. INSERT into `items` + `itemData` + `creators`.
  2. Link to "Inbox" collection (auto-created if missing).
  3. Link to each LLM-suggested user collection (best-effort: skipped if
     missing).
  4. Apply tags: `zs:<reading_priority>` + top 3 LLM tags + optional
     `🦢 black-swan` + the provenance tag `/zs/feeds-v3` (auto-tag,
     `itemTags.type=1`).
  5. Add the v3 triage note (concise 3-section format with
     `<!-- zs:note_type=triage;version=3;... -->` provenance header).
- All other `triaged_pending` rows flip to `rejected_daily_cutoff`.
- Materialized rows get `outcome_eligible_at = now + 7 days`.

After daily selection, Zotero's "Find Available PDF" preference fetches PDFs
automatically. No work from us.

## Outcome detection (feedback loop)

7 days after a paper is materialized into Inbox, the daemon queries Zotero to
see what you did:

| Membership state                                  | Outcome             | Signal weight |
|---|---|---|
| Has 🧠 or 👀 tag                                  | `engaged`           | +3.0          |
| In a non-Inbox collection (filed)                 | `moved_collection`  | +1.0          |
| Only in Inbox (ignored)                           | `kept_inbox`        | −0.5          |
| Removed from all collections                      | `deleted_all`       | **−3.0**      |
| In Zotero trash (`deletedItems`)                  | `trashed`           | **−3.0**      |
| Item key resolves to nothing (hard-delete)        | `unknown`           | −1.0          |

The asymmetric magnitudes follow Schnabel et al. *Recommendations as
Treatments* (ICML 2016) — industrial newsfeeds use delete ≈ 3–10× ignore. We
sit at 6:1.

Each resolved outcome writes a `user_feedback` row that the corpus engagement
weighting picks up on its next refresh, so the system learns from your
deletions in addition to your 🧠/👀 promotions.

## Engagement scoring (Phase 1.14)

The same emoji vocabulary also drives [`goldenset export`](configuration.md)
when it rebuilds the golden CSV from your current Zotero state. As of
Phase 1.14 (user-confirmed 2026-05-14), labels come from **additive
scoring**, not the old single-tier "highest wins" rule. Each signal
contributes a delta to a baseline of `3.0` (neutral):

| Emoji | Meaning | Score Δ | Tier (audit) |
|---|---|---:|---|
| 🧠 | distilled | **+2.0** | strong_positive |
| ✅ | tried / applied | **+2.0** | strong_positive |
| 🗝 | key insight | **+2.0** | strong_positive |
| 👍 | agree / endorse | **+1.5** | high_positive |
| 💡 | idea generated | **+1.5** | high_positive |
| 👀 | skimmed | **+1.0** | medium_positive |
| 🧪 | method extracted | **+1.0** | medium_positive |
| 🧮 | statistical method | **+1.0** | medium_positive |
| ❓ | question raised | **+1.0** | critical_engagement |
| 🧱 | limitation found | **+1.0** | critical_engagement |
| ⚡ | challenged claim | **+1.0** | critical_engagement |
| 🥱 | boring / off-topic | **−1.5** | boring |
| 👎 | thumbs down | **−2.5** | strong_negative |
| ❌ | rejected | **−2.5** | strong_negative |
| 🤖 🔮 ⚪ 🗣 | meta (AI marker, vision, neutral, recommended-by) | **0.0** | meta |

Plus the engagement increments:

- **+0.25 per PDF annotation**, capped at **+2.0** (8+ annotations max out)
- **+0.5 per manual note**, capped at **+1.5** (3 notes max out)
- **`in_trash` short-circuits to `dont_read`** regardless of anything else

The final score is clamped to `[1.0, 5.0]` and binned:

| Score range | Label |
|---|---|
| `< 2.0` | `dont_read` |
| `2.0 – 3.5` | `could_read` |
| `3.5 – 4.5` | `should_read` |
| `≥ 4.5` | `must_read` |

### Why additive

Single-tier classification (Phase 1.10 — "🧠 alone wins, ignore everything
else") buried context. Real reading behaviour stacks: a paper with 🧠 + 8
annotations is *more* than just 🧠; a paper marked 🥱 but with 10 annotations
got more eyeball time than the boredom tag suggests. Sum-of-deltas captures
both. The audit column `gold_signal_tier` records the *list* of tiers that
fired (plus `ann=N` / `notes=N` counters) so you can grep the golden CSV
for any specific combination.

### Examples

```
🧠 alone                      = 3.0 + 2.0           = 5.0  → must_read
👀 alone                      = 3.0 + 1.0           = 4.0  → should_read
❓ alone                      = 3.0 + 1.0           = 4.0  → should_read
🥱 alone                      = 3.0 − 1.5           = 1.5  → dont_read
👎 + 🧠 + 💡 (2 positives)    = 3.0 − 2.5 + 2.0 + 1.5 = 4.0 → should_read
5 annotations alone           = 3.0 + 1.25          = 4.25 → should_read
1-2 annotations alone         = 3.0 + 0.25..0.50    = 3.25..3.5 → could_read or barely should_read
Item in trash                 = (hard override)     = 1.0  → dont_read
```

### Editorial guidance — which emoji for which feeling

| If you think… | …use |
|---|---|
| "I actually applied something from this" | ✅ |
| "Distilled / I'll teach this" | 🧠 |
| "Key insight I'll reuse" | 🗝 |
| "I agree / endorse" | 👍 |
| "This generated a new idea for me" | 💡 |
| "Skimmed, mostly liked it" | 👀 |
| "I'll re-use this method / stat trick" | 🧪 / 🧮 |
| "I have a question about this" | ❓ |
| "Hmm, limitation worth noting" | 🧱 |
| "I disagree with their claim" | ⚡ |
| "Off-topic / not interesting / boring" | 🥱 |
| "Hard reject, never serve me this again" | 👎 / ❌ |

The `🥱` / `❌` distinction matters: 🥱 (−1.5) leaves room for annotations to
pull a paper back up to `could_read` ("boring but I spent time on it"). 👎
(−2.5) is harder to overcome — needs two strong positives to climb out.

## Note format (v3)

Generated notes follow this structure (verified to survive Zotero's TinyMCE
note editor):

```html
<!-- zs:note_type=triage;version=3;generated_at=2026-05-13T...;source=feed-batch;run_id=... -->
<h2>🔥 Must Read</h2>
<p>One-sentence verdict from the LLM.</p>
<h2>Key findings</h2>
<ul>
  <li>F1 = 91% on benchmark X.</li>
  <li>Latency = 23 ms median.</li>
  <li>Open-sourced data + code.</li>
</ul>
<h2>Relevance to my work</h2>
<p>Aligns with your agent-autonomy goal.</p>
<p><em>score 4.2 · goal: agent autonomy · tags: agents, policy, multimodal</em></p>
```

**Distinguishing agent-written notes from your own**:
- HTML comment header (`<!-- zs:note_type=triage;version=3;... -->`).
- Item tag `/zs/feeds-v3` with `itemTags.type=1` (auto-tag — shows as a subtler
  chip in Zotero's UI).
- Concise 3-section structure with the score/goal footer.

Search by tag in Zotero: type `/zs/feeds-v3` into the tag filter.

## Log format

The daemon writes structured single-line log entries prefixed with the tick/run ID:

```
[tick_…] found 12 unread: feed2=7 feed3=5            ← items found per feed at start of tick
[tick_…] triage [1/12] feed2: "Paper title..."        ← per-item progress indicator
[tick_…] skip dedup: "Title" (already in library)     ← library dedup skip
[daily_…] → inbox: "Title"  composite=4.10            ← item selected for Inbox
[daily_…] materialized: "Title"  key=AB12CD34         ← successfully written to Zotero
```

Rejected items are logged at DEBUG level (`APP_LOG_LEVEL=DEBUG` in `.env`):
```
[daily_…] ✗ rejected: "Title"  composite=1.2  reason=flat_distribution_fallback_to_cap
```

If Zotero is running and holds a DB lock when the daemon tries to write, you will see:
```
WARNING: DB locked [apply_feed_materialization] (attempt 1/3) — retrying in 5s
```
The writer retries up to 3× with 5-second delays. If all retries fail, the item stays `triaged_pending` and is included in the next daily selection run (within 24 h). Nothing is lost.

---

## Configuration reference

All knobs live under `feeds:` in `goals.yaml`:

```yaml
feeds:
  enabled: true
  inbox_collection_name: Inbox
  dedup_against_library: true

  # --- Daemon (Phase 1.5 primary workflow) -------------------------------
  daemon_enabled: true
  daemon_tick_seconds: 300        # 5 min between ticks
  daemon_batch_size: 5            # items per tick (LLM-bound)
  mark_processed_as_read: true    # write feedItems.readTime
  outcome_window_days: 7          # wait N days before scoring an outcome
  outcome_check_per_tick: 3       # resolve up to N due outcomes per tick

  # --- Daily-selection sub-job ------------------------------------------
  # Use daily_selection_at for fixed-time delivery (fires once per calendar
  # day, after the target local time).  Falls back to interval-based when
  # the key is absent.
  daily_selection_at: "08:00"     # deliver papers at 08:00 local time every day
  # daily_selection_interval_hours: 24  # alternative: fire every 24 h
  daily_window_hours: 24          # how far back to gather triaged_pending rows
  daily_target_min: 1             # always pick at least this many
  daily_target_max: 2             # never pick more than this
  daily_force_black_swan_every_run: false

  # --- One-shot (feeds run) ---------------------------------------------
  default_item_type: journalArticle

# --- Phase 1.13/1.14 classifier gate (fast-reject before LLM) -----------
classifier_gate:
  enabled: true                  # off → daemon runs without the gate
  model_name: lightgbm           # tabpfn (best F1, slow predict) | lightgbm (fast + SHAP) | logreg
  drop_priorities: [dont_read]   # which predictions skip the LLM. [dont_read, could_read] is aggressive.
  raw_score_dont_read_below: 0.05  # Phase 1.14 override (see below)
  pca_dim: 100                   # only used by TabPFN
  n_folds: 5
```

`classifier_gate.enabled: true` makes `feeds serve`, `feeds tick`, and
`feeds run` (any mode) call the trained classifier before the LLM. The model
artifact lives at `~/.cache/zotero-summarizer/models/{model_name}.joblib`
with a `.json` twin for inspection. `goldenset train-classifier --classifier
<name> --force` regenerates it; lifecycle startup auto-retrains on sha
mismatch with the live golden CSV.

**`raw_score_dont_read_below`** (Phase 1.14): the isotonic calibrator
inflates the low end of the score distribution, so the trained `t_could`
threshold can be unusably small (~0.0007 on a small golden CSV ⇒ no items
ever predicted `dont_read`). When set > 0, every item with **raw** (uncalibrated)
probability below the floor is force-relabeled `dont_read` regardless of the
calibrated threshold. `0.05` is a sensible starting cap; raise to be more
aggressive. Set to `0` to disable.

> **Scope caveat**: this override currently lives inside
> [`_apply_classifier_gate`](../zotero_summarizer/services/feeds.py)
> (the daemon path), **not** in the underlying classifier's
> `predict`. Ad-hoc prediction CSVs produced by
> `classifier.predict_new_items` (e.g. `feed-predictions-*.csv` written by
> one-off scripts or CLI helpers) silently bypass the floor and may show
> `should_read` for items with `raw_score` well below the configured
> threshold. The daemon's gate decisions are unaffected.

**Choosing between TabPFN and LightGBM**: the deployed default is LightGBM
on n=1393 with honest 5×5 + BCa-bootstrap metrics **AUC = 0.570 [0.557,
0.584], Spearman ρ = 0.205 [0.183, 0.224], NDCG@10 = 0.694** (see
[baseline-ceiling-20260515.md](baseline-ceiling-20260515.md)). The
"TabPFN AUC ~0.80 vs LightGBM ~0.70" numbers that used to live here came
from a single fold on n=45 — point estimates, not the distribution; do not
treat them as comparable to today's CI'd baseline. TabPFN re-fits
in-context on every predict call (≈10 s for 200 items) and has no built-in
SHAP. LightGBM does TreeSHAP via `predict_proba(X, pred_contrib=True)`
natively (~1 s for 200 items), which is what powers the Feed Review tab's
per-feature bar chart. The Phase 1.14 review UI only emits SHAP for
LightGBM — switch to TabPFN by setting `model_name: tabpfn` if you want
to trade explainability for a potentially better small-n fit, but
re-measure with the same 5×5 + BCa harness before trusting the comparison.

> **Treat the 4-class output as ranking, not classification.** Cohen's κ
> on the discrete `must / should / could / dont` labels is ≈ 0.04 on the
> current golden set — the class assignment within the kept group is
> essentially noise. The ranking (Spearman / NDCG) is where the model's
> signal lives. Any UI that sorts purely by the discrete chip rather than
> by `composite_score` will surface near-random ordering inside each
> bucket. See [baseline-ceiling-20260515.md §2](baseline-ceiling-20260515.md)
> for the per-metric CI table.

### Phase 1.8 quality knobs (`prestige`, `full_text_refine`)

Two optional top-level blocks in `goals.yaml` improve daily-pick quality:

```yaml
prestige:
  enabled: true
  weight: 0.15
  user_agent_email: "you@example.com"

full_text_refine:
  enabled: true
  top_k: 2
  unpaywall_email: "you@example.com"
```

With prestige enabled, every triaged item is enriched with OpenAlex h-index,
venue impact, and citation count, which feed into the composite score. With
full-text refine enabled, the daily 1–2 picks are re-scored against the actual
PDF (fetched directly via Unpaywall / arXiv) before they land in your Inbox.
See [configuration.md](configuration.md#prestige-section-phase-18-openalex-enrichment).

## Auditing what the daemon did

The full decision log lives in `triage_history.db`. Useful queries:

```sql
-- Today's tick activity (including Phase 1.13 / 1.14 states)
SELECT decision, COUNT(*) AS n
FROM processed_feed_items
WHERE created_at >= datetime('now', '-1 day')
GROUP BY decision
ORDER BY n DESC;
--   gate_rejected, awaiting_review, user_approved, user_rejected,
--   triaged_pending, selected, rejected_daily_cutoff, …

-- What's in the review queue right now?
SELECT id, title, reading_priority, composite_score
FROM processed_feed_items
WHERE decision = 'awaiting_review'
ORDER BY composite_score DESC NULLS LAST;

-- Why didn't paper X get into the Inbox three weeks ago?
SELECT decision, decision_reason, composite_score, corpus_affinity, created_at
FROM processed_feed_items
WHERE title LIKE '%title fragment%';

-- Outcome distribution (the feedback signal)
SELECT final_outcome, COUNT(*) AS n, AVG(outcome_signal_weight)
FROM processed_feed_items
WHERE final_outcome IS NOT NULL
  AND final_outcome != 'pending'
GROUP BY final_outcome;

-- Spot-check SHAP for one row (Phase 1.14)
SELECT json_extract(shap_contribs_json, '$.shap')          AS shap,
       json_extract(shap_contribs_json, '$.aux_context')   AS author_venue
FROM processed_feed_items
WHERE id = <row_id>;
```

## Defenses

1. **Zotero DB lock resilience**: `apply_feed_materialization` and
   `mark_feed_items_read` retry up to 3× (5-second intervals) when they
   encounter `database is locked`. Zotero's sync holds an exclusive lock for
   10–60 seconds; 3 retries × 5 s = 15 s of patience covers the typical sync
   window. If all retries fail, the item stays in `triaged_pending` state and
   is automatically retried on the next daily selection run — nothing is lost.

2. **Indirect prompt-injection from RSS abstracts** is mitigated at two layers:
   - All feed-supplied text is sanitized at read time (`ZoteroReader._sanitize_text`
     strips C0/C1 controls + U+E0000-U+E007F tag chars).
   - The triage prompt wraps untrusted feed content in `<untrusted_input>` tags
     (see `goals.yaml prompts.triage`).
2. **MCP write isolation**: change types `create_*`, `inbox_*`, `promote_*`,
   and `mark_feed_*` are NEVER applied via the MCP entry point — only via the
   daemon or the human CLI. Defends against an injection-driven auto-promotion.
3. **Pre-flight format-locking**: `mark_feed_items_read` uses SQLite's
   `datetime('now')` (UTC, `YYYY-MM-DD HH:MM:SS`) to exactly match what
   Zotero's own client writes. Verified against the 11 pre-existing
   `readTime`-populated rows in a real user library.
4. **Backup-before-write** is inherited from the existing apply pipeline for
   the (rare) legacy `feeds run` path. Daemon-direct `apply_feed_materialization`
   writes do NOT back up by default (Inbox materializations are low-blast-radius
   creates of new items only) — pass `create_backup=True` if paranoid.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Daemon runs but Zotero shows no new items in Inbox | Daily selection hasn't fired yet | `zotero-summarizer feeds select-daily` |
| `daily_materialized: 0` despite items being selected | Zotero DB locked (syncing) | Check for `WARNING: DB locked` in logs. Items stay `triaged_pending` and materialize on next run. |
| "feeds daemon is already running (PID …)" | Another `feeds serve` or `feeds run` is active | Wait or kill that process. If it crashed, delete `feeds.lock` from the project root. |
| `--feeds "Name"` says "ambiguous" | Substring matches more than one feed | Use a longer substring or the numeric ID from `feeds list` |
| `--feeds "Name"` says "not found" | Substring doesn't match any feed name | Run `feeds list` and check for exact names |
| Papers arrive at wrong time of day | `daily_selection_at` not set | Add `daily_selection_at: "HH:MM"` under `feeds:` in `goals.yaml` |
| All items in Inbox get `rejected_low_score` | Corpus pre-filter too aggressive | Lower `corpus.similarity_threshold` in `goals.yaml` (default −0.3) |
| Unread badge in Zotero doesn't decrease | `feeds.mark_processed_as_read` was disabled | Set it to `true` and re-run |
| LLM endpoint errors abort the tick | Auth/quota/network — `_is_fatal_llm_error` short-circuits | Fix `.env`; next tick picks up the abandoned items |
| `feeds run` finishes but Zotero is empty | Review-mode is the default since Phase 1.14 | Open `http://localhost:8000` (Feed Review tab) and approve, OR pass `--auto-materialize` for the old behaviour |
| Review UI shows 0 items after a successful run | All items either gate-rejected (`dont_read`) or LLM errored | `sqlite3 triage_history.db "SELECT decision, COUNT(*) FROM processed_feed_items WHERE created_at >= datetime('now','-1 hour') GROUP BY decision"` |
| Approve fails with `summary missing` | Review row predates Phase 1.14 (no LLM payload saved) | Delete that row or wait for it to roll off the 30-day review window |
| SHAP bars all the same / TabPFN selected | Only LightGBM emits TreeSHAP via `pred_contrib=True` | Switch `classifier_gate.model_name` to `lightgbm` if you need attribution |
| Outcome detection says everything is `deleted_all` | Items materialized while Inbox didn't exist | Check that Inbox collection exists in Zotero (auto-create normally handles this) |
