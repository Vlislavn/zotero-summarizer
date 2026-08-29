# Zotero Summarizer

A local-first reading assistant for [Zotero](https://www.zotero.org/). It reads the
RSS feeds you follow, scores each new paper for how worth-reading it is (a cheap ML
gate first, an LLM for the survivors), and gives you a small daily slate to cull. Your
keep/trash decisions train the model, so tomorrow's slate is sharper.

```
  app RSS pool (self-fetched) → [ML gate → LLM] → ranked daily slate → you cull / read / label
        ▲                                                                      │
        └──────────────── retrain on your labels ◄─────────────────────────────┘
        daily picks → Zotero Inbox · approved tag/note changes → Zotero (backup first)
```

**Local-first · no telemetry · trained on _your_ labels** (nothing ships with the repo —
the model learns from how you triage). The app owns current decisions and their history;
Zotero remains the PDF/citation surface and a synced representation of approved tags/notes.

## Requirements

- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**
- **Zotero desktop**, plus at least one **RSS feed** added in the app (arXiv,
  bioRxiv, or a **PubMed** saved search — see [docs/usage.md](docs/usage.md)
  "Adding sources"); existing Zotero feed subscriptions can be imported in one click
- Optional: an **OpenAI-compatible LLM endpoint** — **local** (Ollama, vLLM,
  LM Studio, `mlx_lm.server`) or **hosted** (any API). ML-only mode needs none.
- **Node 20.19+** to build the browser UI from a clean checkout.

**Hardware** — the app itself is light; the only heavy part is the **optional local LLM**:

| You run… | Need | What you get |
|---|---|---|
| **Hosted API, or no LLM** | ~8 GB RAM · any modern CPU · **no GPU** | ML triage + Library search run on-device; a hosted API adds summaries / brief / ask with **no local-LLM RAM** |
| **A local ~7–20B LLM** | 16–32 GB unified RAM (Apple Silicon) or an NVIDIA GPU | summaries, paper brief, ask-the-paper, deep review — fully offline |
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
(Connect Zotero → choose AI-assisted or ML-only → Describe research) with Zotero-path
auto-detect and an optional live LLM connection test. The web wizard can store an entered
API key in the OS keyring and never returns it to the browser. The headless CLI asks only for
an env-var name and saves the chosen routing before its optional probe. Use
`setup --mode no-llm` when you want classifier-only triage without an endpoint.

After setup, go to **Today** and click **Triage backlog**
to score your unread feed papers, then start culling. *(Going offline? Run `uv run
zotero-summarizer prefetch-models` once while online — see [docs/usage.md](docs/usage.md).)*

## What you'll do

- **Today — cull.** A ranked slate of fresh feed papers. One binary call each: **Add to
  library** (keep → materialized into your Zotero *Inbox*) or **Trash**. Both train the gate.
- **Library — read.** Your unread papers, ranked by relevance. For each you get:
  - a **paper brief** — at-a-glance read verdict, goal-match board (which of your goals it
    serves), a reference-free **quality grade** (FLAG / NEUTRAL / HIGHLIGHT), and figures;
  - **ask the paper** — grounded Q&A that quotes the text and abstains when the answer isn't there;
  - **deep review** — an on-demand full-text digest + quality assessment for your top picks.
- **Annotate — label.** When you actually read one, give it the fine label
  (`must` / `should` / `could` / `don't`). That's your ground truth; the model retrains on it.

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
uv run zotero-summarizer feeds serve      # optional background daemon (auto-triage + daily pick)
uv run zotero-summarizer goldenset train-classifier  # retrain the relevance gate on your labels
```

## Going further

- **[docs/usage.md](docs/usage.md)** — the daemon, how the model learns from your labels,
  offline / air-gapped use, the safety model, and the full config reference.
- **[docs/architecture.md](docs/architecture.md)** — how it works, the layering rules, and
  the dev / verification workflow.
- **[CHANGELOG.md](CHANGELOG.md)** — notable changes (latest: the guided first-run setup).
