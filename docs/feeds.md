# Zotero RSS Feed Processor

The feeds subsystem turns Zotero's RSS-feed inbox into a personal-research filter:
papers you actually want to read land in your **Inbox** collection with tags,
matched user-collection assignments, and a concise note. Everything else gets
triaged, scored, marked read, and stays out of your way.

There are two operating modes:

- **Daemon** (`feeds serve`) — **recommended**. A long-running background
  process that lazy-picks a few unread feed items every N minutes, scores them,
  marks them read, and once per day materializes the 1–2 best into your Inbox.
- **One-shot** (`feeds run`) — Phase 1 batch mode. Processes a date-window of
  feed items in a single pass and queues changes for review via the web UI.

Most users want the daemon.

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
   in preferences — the daemon does NOT download PDFs itself; Zotero does it
   once an item lands in the user library.
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

### Preview what the daemon will see

```bash
zotero-summarizer feeds preview 2 --unread-only --limit 10
```

Shows the 10 oldest unread items from feed 2. No LLM calls, no writes.

### Run the daemon

```bash
zotero-summarizer feeds serve
```

This is the primary command. Logs go to stderr. Press Ctrl-C to stop cleanly
(in-flight tick finishes first).

To process only a subset of feeds (useful while bootstrapping):
```bash
zotero-summarizer feeds serve --feeds 2,3,5
```

To run for a bounded number of ticks (useful for testing):
```bash
zotero-summarizer feeds serve --max-ticks 3
```

### One tick on demand (cron-friendly)

If you'd rather schedule via `launchd` / `systemd` / `cron` than have a
long-running process:

```bash
zotero-summarizer feeds tick --batch-size 5
```

Runs exactly one tick (triage + mark-read + due-outcome resolution +
optionally daily-selection if 24h have elapsed) and exits.

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
   feeds. No feed starves; the smallest feeds get a turn too.
2. **Dedup**: items already in `processed_feed_items` (our DB) are skipped, and
   items with a DOI/arXiv already in your library are dropped as
   `rejected_dedup_library`.
3. **Corpus fast-reject**: each survivor's (title + abstract) is embedded
   (sentence-transformers/all-MiniLM-L6-v2) and compared against your existing
   engaged-vs-rejected corpus. If `corpus_affinity < similarity_threshold`
   (default `-0.3`), the item is fast-rejected without LLM calls.
4. **LLM triage**: the rest go through abstract-only refine + triage prompts.
   Output: composite score (1–5), reading priority, dimension scores,
   suggested user-collection matches, tags.
5. **Record**: every item is written to `processed_feed_items` with decision
   `triaged_pending` (or `rejected_low_score`, `rejected_dedup_library`,
   `skipped_error`).
6. **Mark read**: `feedItems.readTime = datetime('now')` is written for **all
   processed items** (selected and rejected) so Zotero's unread badge clears
   naturally.
7. **Resolve outcomes**: up to N (default 3) materializations from prior runs
   whose 7-day window has elapsed are checked — see "Outcome detection" below.
8. **Daily selection trigger**: if ≥ 24 h since the last daily run, run it now.

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
  daily_selection_interval_hours: 24
  daily_window_hours: 24          # how far back to gather triaged_pending rows
  daily_target_min: 1             # always pick at least this many
  daily_target_max: 2             # never pick more than this
  daily_force_black_swan_every_run: false

  # --- One-shot legacy fields (kept for `feeds run`) --------------------
  default_since_days: 7
  default_item_type: journalArticle
```

## Auditing what the daemon did

The full decision log lives in `triage_history.db`. Useful queries:

```sql
-- Today's tick activity
SELECT decision, COUNT(*) AS n
FROM processed_feed_items
WHERE created_at >= datetime('now', '-1 day')
GROUP BY decision
ORDER BY n DESC;

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
```

## Defenses

1. **Indirect prompt-injection from RSS abstracts** is mitigated at two layers:
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
| Daemon runs but Zotero shows no new items in Inbox | Daily selection hasn't fired yet (waits 24 h from last) | `zotero-summarizer feeds select-daily` |
| All items in Inbox get `rejected_low_score` | Corpus pre-filter too aggressive | Lower `corpus.similarity_threshold` in `goals.yaml` (default −0.3) |
| Unread badge in Zotero doesn't decrease | `feeds.mark_processed_as_read` was disabled | Set it to `true` and re-run |
| LLM endpoint errors abort the tick | Auth/quota/network — `_is_fatal_llm_error` short-circuits | Fix `.env`; next tick picks up the abandoned items |
| Outcome detection says everything is `deleted_all` | Items materialized while Inbox didn't exist | Check that Inbox collection exists in Zotero (auto-create normally handles this) |
