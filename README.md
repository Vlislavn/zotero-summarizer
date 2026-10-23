# Zotero Summarizer

Native RSS read-state maintenance also completes the existing WAL-consistent,
integrity-checked backup before changing unread markers. Repeated failures pause
at six retained app-backup artifacts without deleting recovery snapshots; normal
success retains five. See the [raw read-state safety boundary](docs/usage.md#native-rss-read-state-backups)
for explicit export/recovery steps.

A local-first reading assistant for [Zotero](https://www.zotero.org/). It reads the
RSS feeds you follow, scores each new paper for how worth-reading it is (a cheap ML
gate first, an LLM for the survivors), and gives you a small daily slate to cull. Your
keep/trash decisions train the model, so tomorrow's slate is sharper.

```
  app RSS pool (self-fetched) → [ML gate → LLM] → ranked daily slate → you cull / read / label
        ▲                                                                      │
        └──────────────── retrain on your labels ◄─────────────────────────────┘
        reviewed + explicitly added picks → Zotero Inbox · approved tag/note changes → Zotero (backup first)
```

**Local-first · no telemetry · trained on _your_ labels** (nothing ships with the repo —
the model learns from how you triage). The app owns current decisions and their history;
Zotero remains the PDF/citation surface and a synced representation of approved tags/notes.

Optional HackerNoon tag feeds can be added explicitly in Settings → Sources through
ordinary RSS subscriptions. Saving does not fetch or triage; see
[RSS subscriptions](docs/usage.md#rss-subscriptions).

Pending rejection is all-or-none for the captured selection. Missing/stale rows
return a conflict without changing valid siblings; incomplete replies never claim
success. See [usage](docs/usage.md#rejecting-pending-changes).

## Review recovery and search constraints

Accepted deep reviews retain original-source evidence for their digest findings.
A [local read-only JSON export](docs/verification-audit.md) exposes those existing
checks without another model call or native SQLite read. Library receipts expose
validated retained-source evidence, not current-attachment proof. Legacy evidence
stays unavailable; literal membership is not scientific validation, and audit-effort
benefits are unmeasured. Audit identity covers declared digest fields, not
optional model enrichment.

Deep review rejects demonstrated access/error/login templates before model calls.
Failures show their stage and recovery separately from scientific quality; a failed
attempt or missing-source placeholder does not replace a current valid review.
`verify-deep-review --capture-local` explicitly saves sensitive decoded diagnostics
under `data/`; do not share them. Public source, issues and PRs must not include
private infrastructure identities, personal paths, library screenshots or private
evaluation artifacts. Use portable examples and explicitly configured routes.
It performs no Zotero writes and disables thinking
for its diagnostic calls. See [library](zotero_summarizer/services/library/README.md)
and [CLI](zotero_summarizer/cli/README.md) contracts.

Reviews keep idea value, type-applicable evidence and avoidable writing friction separate.
High-friction or weak-evidence full reads become targeted skims, preserving useful concepts
without treating them as established claims. Failed independent assessment stays
explicitly ungraded across reviews, proposals and saved digest notes; the raw model
self-grade remains separate. No 12–20-paper scientific/human acceptance or model
superiority is claimed.

Full reviews keep contribution, caveats and findings together before folded assessment.
Goal evidence remains accessible without repeated summaries; approved Zotero notes use
persisted paper-specific content. See [usage](docs/usage.md#reading-reviews-and-zotero-notes)
for optional long-field budgets and the separate native/user acceptance boundaries.
App-owned notes carry visible ownership metadata because native editors discard
HTML comments. Keep that metadata to retain same-note updates; unrelated manual
notes remain unowned. Note writes use Zotero's native HTML envelope; existing
notes are not automatically migrated. Refinement validates an unambiguous complete typed JSON root,
uses the canonical schema for its single repair, and preserves source outcome status
(observed versus simulated/projected) rather than presenting every result as measured.
Long-source mapping uses bounded work units with narrow truncation recovery; exhausted
segments still fail. Computed priorities are distinguished from the model's pre-mapping
proposal without changing numerical scoring. Marker-like text alone cannot own a note.
Saved findings, reading hints and tags are not silently list-capped. Independent
preservation/mutation tests run without private inputs. Frozen real-review browser
replays require `ZS_REVIEW_ACCEPTANCE_MANIFEST=/path/to/frozen-input.json`; without
that explicit input they skip, not pass. They check literal retention, not scientific
truth, subjective usability or native Zotero persistence.

Optional triage method/code links must match complete literal URLs in the original
abstract, PDF text/annotations or supplied metadata—not generated map notes. Rejected
links are recorded locally under `data/method-artifact-rejections.jsonl`; whitespace-padded
model candidates are rejected, not silently trimmed. Receipt failures remain errors.
Exact membership does not prove contribution relevance or reproducibility; legacy
saved notes are not revalidated. See the [triage contract](zotero_summarizer/services/triage/README.md).

When web-article review is enabled, HTML-only pages can be reviewed from a bounded,
text-only extraction of the selected main document's body text—not a page facsimile.
Images, layout, CSS-generated content, embedded frames, and shadow trees are outside
that scope; incomplete or over-budget extraction fails rather than clipping a prefix.
An unavailable declared scholarly PDF is not replaced by a paywall snapshot. See the
[browser article memory boundary](docs/browser-article-memory-boundary.md).

Search's folded explicit constraints are user-owned. Model proposals are visible,
not hard gates; inferred refinement exclusions cannot silently remove results.
Confirmed publication types require source metadata, with bounded exact-DOI recovery;
unconfirmed metadata stays unknown. Legacy saved plans retain their original filters.
The plan discloses domain context, source accounting and scholarly coverage limits.
See [search](zotero_summarizer/services/search/README.md).

Search also offers **Experimental original-source question evidence**, off by default
for each new page visit. With questions and explicit opt-in, a bounded original-text
pass can promote sufficient answers before review selection. Inspect located quotes,
partial/unchecked states and evaluator provenance; unknown is not a negative result.
The configured Feed/Deep review routes must differ. Saved evidence is active only
while loaded provider/model/settings and local evaluator protocol still match;
missing metadata means unchecked, not assumed valid. Reads build no clients or
requests. Historical receipts remain intact, and retained pre-promotion tie order
restores the constrained baseline when evidence becomes stale. Legacy sessions
cannot reconstruct an unrecorded tie order. No quality or latency win has been
measured and issue #30 remains open. [Usage and limits](docs/usage.md#experimental-search-question-evidence).

The shared Today/Library paper page offers **Related in my library** over saved
metadata, including already-read papers. An optional, explicitly consented
comment-context experiment uses the configured review model on complete seed and
candidate notes. It remains unqualified and off by default; no external discovery
or automatic Add occurs. Stage-client cache publication is bound to its actual
effective routing snapshot and invalidation generation, not current metadata alone.
Saving seed notes/verdicts hides prior related context and fences old reads until
an explicit fresh load. See [related saved papers](docs/related-saved-papers.md)
for privacy, budgets, durable results and outstanding owner evaluation.

## Requirements

- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**
- At least one **RSS feed** added in the app (arXiv, bioRxiv, or a **PubMed** saved
  search — see [docs/usage.md](docs/usage.md) "Adding sources").
- Optional: **Zotero desktop** for PDFs/citations and approved Zotero writeback;
  existing Zotero feed subscriptions can be imported in one click.
- Optional: an **OpenAI-compatible LLM endpoint** — **local** (Ollama, vLLM,
  LM Studio, `mlx_lm.server`) or **hosted** (any API). ML-only mode needs none.
- **Node 20.19+** to build the browser UI from a clean checkout.

**Hardware** — the app itself is light; the only heavy part is the **optional local LLM**:

| You run… | Need | What you get |
|---|---|---|
| **Hosted API, or no LLM** | ~8 GB RAM · any modern CPU · **no GPU** | ML triage + Library search run on-device; a hosted API adds summaries / brief / ask with **no local-LLM RAM** |
| **A local ~7–20B LLM** | 16–32 GB unified RAM (Apple Silicon) or an NVIDIA GPU | local inference for summaries, briefs and Q&A; RSS/PDF acquisition may still need network |
| **A local ~35B LLM** | 48 GB+ unified memory, or 24 GB+ VRAM | highest-quality deep reviews + quality grading |

The on-device ML (relevance gate + search) runs on **CPU** — no GPU required for the app.
**Disk:** ~1.5 GB of ML models (downloaded once) plus your data under `data/`. The LLM is
**optional**: with none at all, the ML-only "Triage backlog" still ranks your feed.

## Quickstart

```bash
# 1. Install (uv creates the env and installs from the lockfile)
uv sync

# 2. Build the browser UI (frontend/dist is generated, not committed)
cd frontend && npm ci && npm run build && cd ..

# 3. Run — first launch auto-creates goals.yaml + a .env skeleton and migrates the DB
uv run zotero-summarizer serve
```

First run bootstraps everything and the in-app **`/setup` wizard** walks you through the
rest — no manual file copying:

```
install ─▶ build UI ─▶ serve ─▶ /setup wizard ─▶ Today
                               Connect Zotero
                               Choose AI or ML-only
                               Describe research
```

Open <http://127.0.0.1:8000/>. A brand-new install lands on the **`/setup` wizard**
(choose Full local / Hosted / No LLM → Connect Zotero → choose a model if needed → Describe research) with Zotero-path
auto-detect and an optional live LLM connection test. The web wizard can store an entered
API key in the OS keyring and never returns it to the browser. The headless CLI asks only for
an env-var name and saves the chosen routing before its optional probe. Use
`setup --mode no-llm` when you want classifier-only triage without an endpoint.

**Verification is separate from saving.** Doctor now refuses local LLM inference and
local dry-runs as **unknown**: no adapter supplies the effective runtime context,
placement and memory domain needed for hardware-safe admission. Listed models,
profile estimates and free RAM are not proof. No download starts automatically and
loopback needs no API key. Hosted and No LLM verification remain available; issue
#11's hardware-safe Full-local completion is not yet delivered. Literal 127/8 and
IPv4-mapped IPv6 loopbacks share this guard; mixed-runtime recovery names the
failing endpoint rather than borrowing a sibling's Ollama command. Numeric IPv4
shorthand and reserved `.localhost` names also use local admission without DNS.
Stop fences later inventory requests, and configuration edits retain the active
Stop control. External hosted credential changes invalidate earlier success. See
[setup verification](docs/usage.md#setup-verification-and-recovery).
Local calibration uses the same admission at both client constructions, including
cached Tier-1 runs; it cannot bypass UNKNOWN to load a model or publish success.
Hosted calibration and ordinary application clients retain their existing behavior.

After setup, go to **Today** and click **Triage backlog**
to score your unread feed papers, then start culling. *(Going offline? Run `uv run
zotero-summarizer prefetch-models` once while online — see [docs/usage.md](docs/usage.md).)*

For remote use, explicitly configure the owner-authenticated HTTPS-to-private-Unix
[deployment boundary](docs/local-deployment-boundary.md). Ordinary `serve` remains
loopback-only; `make iphone` refuses rather than exposing unauthenticated LAN HTTP.
This is not a claim of physical-device/offline/revocation acceptance.

## What you'll do

- **Today — cull.** A ranked slate of fresh feed papers. Generate and inspect a full-text
  review before explicitly **Add to library** (materialized into the Zotero *Inbox*);
  **Trash** remains immediate without a review. Both decisions train the gate.
  Cards separate **Relevance** from AI evidence; expand **Why selected** for the
  actual policy/picker observations. Unknown evidence does not distinguish absent,
  stale or failed reviews. Ranking, thresholds and labels are unchanged; this is
  observability, not measured recommendation-quality improvement.
- **Library — read.** Your unread papers, ranked by relevance. For each you get:
  - a **paper brief** — at-a-glance read verdict, goal-match board (which of your goals it
    serves), a reference-free **quality grade** (FLAG / NEUTRAL / HIGHLIGHT), and figures;
  - a visible **reading plan** — ordered targets, parts to skip and the original's added value beside the saved reading verdict; missing estimates stay unknown;
  - **ask the paper** — grounded Q&A that quotes the text and abstains when the answer isn't there;
    long conversations retain complete current-source handles only when shorter than raw history.
    Prior conversation stays untrusted, not evidence; see the
    [checkpoint contract](zotero_summarizer/services/library/README.md#qa-evidence-checkpoints);
  - **deep review** — an on-demand full-text digest + quality assessment for your top picks;
    map-reduce verifies claims against the original paper, not its generated notes.
    Empty source text or chunk summaries fail rather than becoming a skip recommendation.
- **Annotate — label.** When you actually read one, give it the fine label
  (`must` / `should` / `could` / `don't`). That's your ground truth; the model retrains on it.

- **Weekly — browse saved digests.** Open **Weekly**, choose a saved research-feed
  CLI run, follow its paper links, or download canonical JSON and Markdown.
  Bookmark `/weekly?run=<bundle-name>` to reopen it. Refresh saved runs after
  publishing with `uv run zotero-summarizer research-feed run --from 2026-08-22
  --to 2026-08-29 --cached-only`. Browsing is read-only and needs the local server;
  missing reviews, unknown evidence and unreadable reports stay explicit.
  Weekly paper links also stay passive; starting a review requires **Generate review**.

Open PDFs and take notes in Zotero as usual; come back here to triage.

## Configuration

Two files under your project root, both gitignored and **created automatically on first
run** — no templates to copy:

| File | You touch | Managed by |
|---|---|---|
| `.env` | optional CLI-managed API-key environment values | the app writes the two Zotero paths here via the `/setup` wizard / `setup` CLI; the web wizard stores pasted keys in the OS keyring |
| `goals.yaml` | nothing by hand | app-authored — edit research goals + LLM routing in **Settings**, don't hand-edit |

The Settings page is split into **Essentials** (research goals, triage criteria, the default
AI on/off, LLM provider, Zotero paths — always visible) and a collapsible **Advanced** section (full
stage routing, classifier gate, corpus). Secrets stay **name-only** everywhere in the UI: it
collects the env-var name, never the raw value. Everything else has working defaults. Full
reference in [docs/usage.md](docs/usage.md).

## Commands

```bash
uv run zotero-summarizer serve            # FastAPI server + browser UI (auto-bootstraps on first run)
uv run zotero-summarizer setup            # headless guided onboarding (same flow as the /setup wizard)
uv run zotero-summarizer doctor           # verify the real configured pipeline
uv run zotero-summarizer calibrate        # optional measured runtime calibration
uv run zotero-summarizer migrate          # init / upgrade the local databases (serve does this for you)
uv run zotero-summarizer prefetch-models  # download ML models for offline use (--check = status)
uv run zotero-summarizer feeds serve      # optional feed triage and in-place slate reviews (no automatic Zotero Add)
uv run zotero-summarizer goldenset train-classifier  # retrain the relevance gate on your labels
uv run zotero-summarizer faithbench build --max-builder-windows 64  # larger QA-source budget; more builder calls
```

## Going further

- **[docs/usage.md](docs/usage.md)** — the daemon, how the model learns from your labels,
  offline / air-gapped use, the safety model, and the full config reference. Faithbench QA
  builds cover contiguous 6,000-character windows (32 per paper by default); its CLI/env
  budget and increased call cost are documented there.
- **[Research-feed evaluation evidence](docs/issue-evidence-research-feed.md)** — why the current
  30-paper fixture is unscorable for production inclusion quality (not a 0%-precision result).
  `research-feed run --from 2026-08-22 --to 2026-08-29` reuses RSS triage and current
  reviews, co-producing concrete project proposals in the same digest call. Original
  quote membership is checked, not scientific truth. Effort estimates are explicitly
  unverified; code alone establishes no duration. `--cached-only` avoids startup/model
  work but needs the existing goals config for current qualification; errors remain
  visible (CLI exit 1). Weekly acquisition never opens a browser; missing abstract
  stages are errors, and omitted projects remain unknown. Shared abstract prompts
  escape source boundary markup; semantic injection resistance is not established.
  Saved v1 reports are not rewritten. Partial-run diagnostics stay on stderr so
  CLI stdout remains parseable JSON; verification disables declared chat thinking
  or minimizes active three-level reasoning to low without changing digest effort.
- **[docs/architecture.md](docs/architecture.md)** — how it works, the layering rules, and
  the dev / verification workflow, including owned application-logger isolation
  between tests.
- **[Native issue delegation](docs/native-agent-workflow.md)** — pinned async Pi adapter,
  exclusive workers, independent review, per-child cwd/role checks, isolated failure
  handling, task-level authority boundaries, scout-artifact readback, restart/activation
  checks and offline workflow contract tests (not model-quality or sandbox acceptance).
- **[CHANGELOG.md](CHANGELOG.md)** — notable changes (latest: the guided first-run setup).
