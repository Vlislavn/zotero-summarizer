---
description: Weekly paper-gap review — find top papers per research goal and flag which are missing from the Zotero library.
---

# Weekly paper-gap review

Goal: catch on-topic papers the push-feed can't reach (old or never-announced-in-window).
You provide the judgment (which are top, curation, diversity); the app's Targeted
Search provides the grounding (real IDs/titles/citations); `in_library` is the
library check. **Never invent papers or citations — only surface what `screen` returns.**

App base URL: `http://127.0.0.1:8000`.

## Procedure

**0. Readiness.** `GET /api/admin/llm-reachability` → `{"status":"ok","stages":[{stage, provider, model, reachable, detail}, ...]}`.
If the request fails, or the `deep_review` stage isn't `reachable: true`, stop and
tell the user to start/fix the app (`make serve`) — screen needs that LLM for
intent parsing. Don't proceed on a dead endpoint.

**1. Read the goals.** Read `goals.yaml` → `research_goals` (a list). Strip any
stray surrounding quotes/commas from each entry (they're stored as quoted
strings). These are your per-goal queries.

**2. Screen each goal.** For each goal:
```
POST /api/search/screen   {"query": "<goal text>", "questions": []}
```
Returns a session dict; `candidates` is already ranked by `query_score` (best
first). Each candidate carries: `title`, `authors`, `year`, `venue`, `doi`,
`arxiv_id`, `url`, `query_score`, `cited_by_count` (OpenAlex citations, importance
signal — may be null off-channel), `in_library` (True=in Zotero / False=confirmed
absent / null=unknown), `existing_zotero_key`. Keep the **top ~8 per goal**.

**3. Curate ~12 across all goals.** Apply judgment:
- dedupe across goals (same DOI/arXiv → one entry);
- balance **seminal** (high `cited_by_count`) against **recent** (current year);
- spread across goals — don't let one goal dominate;
- one-line "why it matters" per paper + a marker: `✦NEW` when `in_library` is not
  `True`, `✓in-lib` when `in_library is True`. `null` coverage → treat as `✦NEW`
  but note "coverage unknown (no DOI/arXiv)".

**4. Present the shortlist and WAIT for explicit user confirmation.** Group by
goal; show title · why · marker · citations · year. Do not proceed to the gap
list until the user confirms (they may drop/keep entries).

**5. Gap list.** From the confirmed set, the gaps are papers where `in_library`
is **not** `True`. For each: `title · why · DOI · arXiv/OA link (url)`. Tell the
user to add them in Zotero (it auto-fetches the PDF). **Do not write to Zotero
yourself** — surfacing gaps is the deliverable; the user adds them.

**6. Stamp completion.** Get the real UTC timestamp:
```
date -u +%Y-%m-%dT%H:%M:%SZ
```
Then atomically write `data/weekly_review.json` (tmp file + `mv`, so a crash never
leaves a half-written stamp):
```json
{"last_done_at": "<that timestamp>", "reviewed_goals": ["<goal>", ...], "gaps": [{"title": "...", "doi": "...", "url": "..."}, ...]}
```
This is what the `SessionStart` hook reads to know the review ran this week — the
hook only reads it, never writes it (no clobber race).

## Notes
- This is human-in-the-loop by design: nothing reaches Zotero without the user's
  explicit action (honors "agent tools never auto-mutate user data").
- `screen` makes one intent-parse LLM call per goal (~6) plus the local reranker —
  a few seconds each, no heavy PDF/LLM work (that's phase-2 `review`, not needed here).
