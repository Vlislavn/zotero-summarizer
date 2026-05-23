# Zotero Summarizer — User Guide

## 1. What this tool does

This tool reads your Zotero library and the RSS feeds you follow, scores each new paper for how worth reading it is, and proposes a small daily slate of the best picks. The workflow has **two stages**:

1. **Today (cull):** make one quick call per paper — **Add to library** (keep it to read) or **Trash** (clearly not relevant). Both choices train the model. You do *not* pick a detailed priority here.
2. **Library → Read next (read):** your unread library papers, ranked by model relevance, so you know what to read right now. When you actually read one, you give it the detailed `must_read` / `should_read` / `could_read` / `dont_read` label and notes (in `Annotate`).

Your decisions at both stages become the training signal that makes tomorrow's slate sharper.

Zotero stays the source of truth for your library: this tool reads it, scores it, and writes back tags and collection memberships. Nothing here replaces the Zotero UI for note-taking, annotation, or PDF reading — open the PDF in Zotero as usual; come back here to triage.

## 2. Daily workflow

The day looks like this. Open the app in your browser (the React SPA is served at `/`).

**Stage 1 — `Today` (cull).** If it's the first run of the day it may spend a minute triaging fresh feed papers, then shows a ranked slate. Each card shows where the pick came from — its **bucket** (model / surprise / audit / diversity) and its source **feed** — plus the composite/prestige scores and a collapsed "why this paper?" breakdown. Skim the title and abstract, **tick** the papers worth reading, then commit one batch action: **Add to library** (they're materialized into your Zotero *Inbox* collection and recorded as a positive training signal) or **Trash** (recorded as a strong negative and marked read). Acted papers drop off the slate.

```
+-------------------------------------------------------+
|  Zotero Summarizer    [Today] [Annotate] [Settings] ..|
+-------------------------------------------------------+
|  Today's reading — cull the feed into your library    |
|  [x] 2 selected     [ Add 2 to library ] [ Trash 2 ]  |  ← batch action bar
|                                                       |
|  +--------------------------------------------------+ |
|  |[x] Title of paper 1                              | |
|  |    Smith J, Lee P     Nature Methods  2026       | |
|  |    [bucket model] [feed bioRxiv] [composite 2.9] | |  ← provenance
|  |    > Triage rationale     > Why this paper?      | |
|  +--------------------------------------------------+ |
|  +-- [ ] paper 2 ----------------------------------+  |
|  +-- [ ] paper 3 ----------------------------------+  |
+-------------------------------------------------------+
```

**Stage 2 — `Library` → `Read next` (read).** Open `Library` and switch to **Read next**: your *unread* library papers ranked by model relevance, each row showing a **★ relevance score** and a one-line reason (e.g. "Topic match"). Already-read papers (those you tagged 🧠/👀 in Zotero, or annotated) are hidden by default with a **"Show already-read"** toggle. Click a paper to open it in `Annotate`, where you read it and set the detailed label (`1`/`2`/`3`/`4` = `must`/`should`/`could`/`don't`) and notes. When you tag/annotate it in Zotero it leaves the queue.

Once a week (or whenever your slate looks off), use `Annotate` to batch-review the labels the system has derived for older items. Use `j` / `k` to move and `1` / `2` / `3` / `4` to assign priorities — your verdict saves and the UI advances to the next paper before the network round-trip finishes.

## 3. The three primary tabs

The `NavBar` shows only three tabs by default. Everything administrative is tucked under a `Power tools` disclosure on the right.

### Today

The daily slate, ranked most-relevant first — a **cull** surface, not a labelling surface. Each card shows the title, authors (with the top author's h-index when OpenAlex has it), venue and year, two **provenance** badges (the allocation **bucket** — model / surprise / audit / diversity — and the source **feed**), the composite and prestige scores, an optional triage rationale, and a collapsed "why this paper?" waterfall.

You make a **binary** decision per card, in batch:

- **Tick** the checkbox on each paper worth reading (a "Select all" toggle and a sticky action bar appear once anything is selected).
- **Add N to library** — materializes the selected papers into your Zotero **Inbox** collection (the daemon's direct-create path) and records each as a positive training signal (`should_read`, provisional — you refine it later when you actually read it). They show up in `Library → Read next`.
- **Trash N** — records each as a strong negative (`dont_read`) and marks the feed items read so they leave Zotero's unread view.

There are deliberately **no** `must/should/could/don't` buttons and **no** "time well spent / wasted" rating here — the fine label belongs to Stage 2, after you've actually read the paper (see `Library → Read next` and `Annotate`). Acted-on papers drop out of the slate on the next refresh.

#### Where the slate comes from (and why it's never empty)

The slate is built from feed papers that have been **triaged** (scored).
If nothing has been triaged recently, Today does two things automatically:

- **Falls back** to the most recent scored items so the tab is never blank
  (you'll see a *"showing older items"* note).
- **Auto-runs a background triage** of your un-scored feed backlog. The free
  LightGBM gate fast-rejects obvious non-matches; survivors are scored by
  the SOTA model configured in `CUSTOM_BASE_URL` / `CUSTOM_API_KEY`
  (api.kather.ai, `model: sota`). A progress line shows
  *"Triaging your feeds via sota… scored N, gate-rejected M"*; the list
  fills in when it finishes. You can also trigger this from the terminal
  with `zotero-summarizer feeds run`.

If `CUSTOM_*` is not set in `.env`, the auto-triage reports the provider is
missing — set those keys (see the README "Configure" section) to enable it.

### Library → Read next

The `Library` tab has two modes: **Browse / triage** (the classic filterable
table — see Power tools below) and **Read next**, the Stage-2 reading queue.

Read next lists your **unread** library papers ranked by the gate's relevance
score (highest first), so the top of the list is genuinely "what to read now",
not just the most-recently-added. Each row shows a **★ relevance score** (1–5)
and a one-line reason for it (e.g. *"Topic match"*, *"Like papers you kept"*).

- **Already-read papers are hidden by default.** A paper counts as read once it
  carries an engagement/veto emoji tag in Zotero (🧠 distilled, 👀 skimmed, 👎 / ❌
  / 🥱 veto). A **"Show already-read"** toggle reveals them.
- **Click a row to read + label it.** It opens that exact paper in `Annotate`
  with the full **"Why this score?"** waterfall, where you set the detailed
  `must/should/could/don't` label and notes. When you then tag or annotate it in
  Zotero, it drops out of the queue on the next refresh.

The scores are computed by a background job the first time you open Read next
(you'll see *"Scoring your library…"*), then cached — subsequent opens are
instant. The cache is keyed by the trained model, so it only recomputes when the
gate is retrained (not on every paper you add). If the model isn't ready yet,
the queue falls back to a recency ordering and says so.

### Annotate

A two-column master-detail surface for batch-cleaning the 1,767 rows in `zotero-summarizer-golden.csv` (~1,114 library rows, ~632 `feed:*` rows, ~21 `note:*` rows).

The left column is a filterable paper list. Priority chips: `must_read` / `should_read` / `could_read` / `dont_read` / `all` / `🎯 border`. A search box filters by title. The flag chips (`weak_must_read` / `near_must_read` / `manual_override` / `any`) are tucked under an **"Advanced filters"** disclosure to keep the default view simple.

**Your manual label always wins.** Each row's badge shows the *effective* priority — your verdict if you've cast one, otherwise the derived label — and the priority chips filter by that effective value. So a paper you manually re-classified to `must_read` shows as `must_read` and appears under the `must_read` chip, with a small `was: could_read` note showing what the automatic derivation said. This holds even after you click **Refresh labels** (which re-derives the CSV from Zotero): your manual label is stored separately and is re-applied on top, so it never silently reverts to the automatic value.

If a paper you labelled later leaves the golden CSV (e.g. you removed its Zotero engagement signals, or it was a feed item that aged out), its verdict is **not** lost — it appears flagged `orphaned` and stays viewable, editable, and deletable.

The **`🎯 border`** chip is active learning: it ranks library rows by how close the model's score sits to a class boundary, so re-labelling them gives the most training value per click. The list is computed in the background (it scores every library row) and cached, so the first open after a data change shows "Scoring…" then fills in.

The right column is a sticky 3-zone layout (`PaperDetailLayout`):

```
+-- AnnotateView ----------------------------------------+
| Filters: [must][should][could][don't][all]  flag: [..] |
| Search: [............]    Showing 200 of 632          |
|                                                       |
| +-- Papers ----------+   +-- Detail -----------------+ |
| | ABCD1234 Title 1  >|   | Title (sticky top)        | |
| | EFGH5678 Title 2   |   | Smith J (h=42), Lee P     | |
| | feed:42 Title 3    |   | Nature 2026  DOI: ...     | |
| | ...                |   |---------------------------| |
| | (scrolls)          |   | Abstract                  | |
| |                    |   | Tags                      | |
| |                    |   | Provenance breakdown      | |
| |                    |   | SHAP waterfall (6 bars)   | |
| |                    |   | Annotations / Notes       | |
| |                    |   |---------------------------| |
| |                    |   | Your verdict (sticky bot) | |
| |                    |   | [must][should][could][n't]| |
| |                    |   | Comment textarea          | |
| +--------------------+   +---------------------------+ |
+-------------------------------------------------------+
```

Keyboard shortcuts (the verdict panel header echoes them: `1 must · 2 should · 3 could · 4 don't · j/k navigate`):

- `j` — next paper in the filtered list
- `k` — previous paper
- `1` — must_read
- `2` — should_read
- `3` — could_read
- `4` — dont_read

Verdicts are **optimistic**. When you press `1`, the UI flashes "Saved ... → must_read", advances to the next paper, and the mutation runs in the background. If the save fails, the UI bounces back to the failed paper and shows the error — but for the 99.9% happy path you stay in flow.

### Settings

Research goals, triage criteria, output language, LLM models (draft / refine, API base, API key env var), corpus similarity threshold, and classifier-gate config (enabled, model name, drop priorities, raw-score floor, audit sample size). Edits round-trip the full `GoalsConfig` payload so nested fields the form doesn't surface (prompts, prestige config, full-text refine) are preserved.

Settings also has a **Model lifecycle** panel:

- **Current model** card — the trained gate's metadata: classifier, objective, OOF Spearman ρ, training size, when it was trained, the git commit, and the golden-CSV sha it was trained on.
- **Refresh labels** — re-export `zotero-summarizer-golden.csv` from your current Zotero engagement signals. (Your manual verdicts are stored separately and still win — see Annotate.)
- **Retrain model** — rebuild the classifier gate on the current labels (your manual verdicts overlaid). Runs as a background job with a progress bar; the model card refreshes when it finishes.

## 4. How prestige is computed

`zotero_summarizer/services/prestige.py:37-49` defines the formula. Given an OpenAlex work for the paper:

```
prestige = 1.0 + 4.0 × (0.50 · h_norm + 0.30 · venue_norm + 0.20 · cites_norm)
```

with each input log-scaled into `[0, 1]`:

```
h_norm     = log(1 + max_author_h_index) / log(1 + 100)
venue_norm = log(1 + venue_works_count)  / log(1 + 50000)
cites_norm = log(1 + cited_by_count)     / log(1 + 1000)
```

Range is `[1.0, 5.0]`. A paper with all-zero metrics maps to `1.0` (the floor); a paper where OpenAlex has no record at all maps to `3.0` (neutral — we don't punish unknown authors). Numbers above the reference ceilings saturate.

The right-hand panel on each paper shows the three raw inputs next to the waterfall:

> top-author h-index **42** · venue works **8,127** · citations **312**

### The "Why this score?" waterfall

`PrestigeWaterfall.jsx` explains the score. It starts from a **baseline** (the model's average score across all papers) and each bar shows what pushed *this* paper above or below it: green bars (right of the axis) push the score up, red bars (left) pull it down. Feature names are shown in plain language — *"Baseline (avg)"*, *"Topic match"*, *"Like papers you kept"*, *"Goal match"*, *"Prestige"* — not raw model keys. The header shows the final composite and prestige:

> Composite **2.93** · Prestige **0.46** (0–1)

This panel now appears for **library** items too: opening a paper from `Library → Read next` (or any library row in `Annotate`) scores it with the gate on the spot — reusing the exact score the queue ranked it by — so the waterfall always explains why it sits where it does. Only items the gate can't score (no abstract, or the gate disabled) fall back to a one-line "no model reasoning" note.

## 5. What the four reading priorities mean

These four labels are **derived**, not user-chosen, for the bulk of the golden CSV. The derivation lives in `zotero_summarizer/services/goldenset.py:_infer_label`. Inputs: your emoji tags on the Zotero item, the count of annotations on its attached PDF, the count of user-written notes (LLM-pasted notes are stripped first), whether it sits in trash, and how long ago you added it (180-day exponential decay).

- **must_read** — the strongest engagement signal: a strong-positive emoji on a recent item, or many annotations / user notes. Treat as "open this today."
- **should_read** — moderate engagement: a positive emoji or some annotation activity. Worth a closer look this week.
- **could_read** — neutral by default. No strong signal either way. Skim the abstract.
- **dont_read** — either a hard veto emoji (🥱 / 👎 / ❌), an item you trashed, or accumulated negative signals. Skip unless something changed.

The same `Annotate` detail panel is the **Label Audit** surface: open any paper and the `Provenance breakdown` block lists the chain of reasoning behind its derived priority — which emojis matched, how many annotations / notes counted, the decay factor for its age, and the final score that fell into one of the four bins.

## 6. Two kinds of decision: keep/trash vs the priority label

The workflow deliberately splits the decision in two, so you're never asked for a fine judgment before you've read the paper:

- **Keep / Trash (Stage 1, `Today`)** is a **coarse, pre-read** call: "is this worth pulling into my library at all?" *Add to library* trains a positive signal; *Trash* trains a strong negative. That's all Today asks for.
- **`must_read` / `should_read` / `could_read` / `dont_read` (Stage 2, `Annotate`)** is the **fine, post-read** judgment: "now that I've read it, how should it be prioritized?" It's what the model is trained to reproduce and what the calibration metrics compare against.

A paper you *Add to library* gets a provisional `should_read`; when you later read it and set a real label in `Annotate`, your manual label wins (see Annotate). The two never conflict — the coarse call seeds training immediately, the fine label refines it.

> Note: an earlier build also collected a per-slot "Time well spent / Wasted my time" rating on Today. That after-reading rating has been removed from the Today UI in favour of the two-stage flow above.

## 7. Power tools

Open the `Power tools` disclosure (top-right of the NavBar) for five administrative surfaces. You won't need these day-to-day; reach for them only in the situations described.

- **Library** — two modes. **Read next** is the Stage-2 reading queue (unread library papers ranked by relevance; see section 3). **Browse / triage** is the classic filterable table: browse your Zotero items by collection or tag and kick off a fresh triage job on a subset (use when you want to re-score a particular collection, e.g. after editing your `research_goals` in Settings).
- **Triage** — monitor running triage jobs, watch their progress, and inspect calibration metrics (Cohen's κ, Pearson r vs derived labels, F-class). Use when a job is in flight or when you want to know how well the current scorer matches your golden labels.
- **Feed Review** — items the RSS daemon parked for human review. Two buckets: gate-rejected (the classifier-gate dropped them but they look borderline) and awaiting-review (the daemon needs your call before saving to Zotero). Use weekly to keep the feed pipeline unblocked.
- **Pending** — queued Zotero edits awaiting apply. The daemon batches `addCollection` / `addTag` writes and parks them here so you can review before they hit your library. Use after a large triage job, or when something looks wrong and you want to abort a batch.
- **Re-label Audit** — test-retest reliability. Pulls a random sample of papers you've already labeled, asks you to label them again without showing your prior answer, and reports Cohen's κ, ICC, and Pearson r between the two runs. Use quarterly to check whether your own labels are consistent.

## 8. Common workflows

### "I see a `feed:*` paper in Annotate, the label is `must_read`, but I disagree"

1. Open `Annotate`. The default filter is `must_read`.
2. Click the paper. The right column loads: title, authors with h-index, abstract, tags, provenance breakdown, SHAP waterfall.
3. Read the abstract. Check the SHAP waterfall — which features pushed the score up?
4. Press `3` to assign `could_read`. The UI saves your verdict optimistically and immediately loads the next paper. A green "Saved feed:42 → could_read" flash confirms.

### "Today's slate looks off, I want to investigate"

1. Open `Annotate`.
2. Leave the priority filter on `must_read`.
3. Switch the flag filter to `weak_must_read` to surface labels that barely cleared the threshold.
4. Scan the list. For each paper that looks wrong, click it, read the provenance breakdown, and re-label with `1` / `2` / `3` / `4`.
5. Optionally switch to `near_must_read` (items just below the threshold that you might want to promote) and repeat.

### "The model seems too generous on prestige"

1. Open `Annotate` and select any paper you suspect.
2. Look at the `PrestigeWaterfall` panel. The footer shows the raw inputs: top-author h-index, venue works count, citation count.
3. Manually re-apply the formula from section 4 and check whether the published number matches. If a 30-author Nature paper is getting prestige `5.0` purely because of one famous co-author, that's a deliberate design choice (`max_author_h_index`, not average) — but if you think the weights are wrong, file an issue with a few example item_keys and the inputs.

## 9. Troubleshooting

### "I see `(no authors)` on a paper"

The `AuthorByline` component prints a source-specific message when the `authors` array is empty:

- For a library row (`source: library`): `(no authors listed in Zotero)`. The Zotero item's `creators` table is empty — fix it in Zotero itself.
- For a feed row (`source: feed`): `(authors not in feed metadata)`. The RSS feed didn't include `<author>` tags; we can't synthesize them.
- For a note row (`source: note`): `(parent paper has no authors)`. The parent library item has no creators; same fix as the library case.

### "The page didn't update after my verdict"

The Annotate page invalidates its own React Query caches on every successful save, but it doesn't reach across to the `Today` slate or the `Library → Read next` queue. After labelling in `Annotate`, reload the tab (or click `Refresh` / re-open the tab) to see other surfaces reflect it. Note that a new priority label changes the Read-next *ranking* only after the gate is retrained (Settings → Retrain), since the queue ranks by the model's score.

### "I want to undo a verdict"

Open the paper in `Annotate`. The verdict panel shows the existing verdict in green ("Previously: must_read · 2026-05-14 ..."). Click `Delete`. A confirmation dialog appears; click OK. The verdict is removed and the derived label takes over again. If you only want to change the value, click `Edit` instead and pick a different priority.

### "The Today slate is empty"

Today triages your feed backlog automatically when it has nothing to show — you'll see *"Triaging your feeds via sota…"* with a running count, and the slate fills in when it finishes. If it instead says the provider is missing, set `CUSTOM_BASE_URL` and `CUSTOM_API_KEY` in `.env` (see the README "Configure" section) and reload. You can also trigger triage from the terminal:

```bash
zotero-summarizer feeds run        # exhaust the backlog now
zotero-summarizer feeds serve      # or run the daemon continuously
```

If there genuinely are no feed items in Zotero at all (you follow no RSS feeds, or none have new items), add feeds in Zotero first — the slate is built from Zotero's feed items.

## Important notes

- On `Today`, **Add to library** is a real Zotero write — it creates the item in your **Inbox** collection immediately — and trains a positive signal; **Trash** trains a negative and marks the feed item read. Both also drop the card from the slate on refresh.
- The `Library → Read next` score is cached per trained model; after a retrain the first open recomputes ("Scoring…") then is instant again.
- Optimistic auto-advance in `Annotate` means a network failure briefly looks like success, then jumps back. If you see the UI snap backwards with an error flash, your last save did not land.
- Verdicts you set in `Annotate` are stored separately from the derived labels in `zotero-summarizer-golden.csv`. The CSV is regenerated from your Zotero engagement signals; your verdicts persist in the app database.
