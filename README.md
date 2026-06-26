# Zotero Summarizer

A local-first reading assistant for [Zotero](https://www.zotero.org/). It reads the
RSS feeds you follow, scores each new paper for how worth-reading it is (a cheap ML
gate first, an LLM for the survivors), and gives you a small daily slate to cull. Your
keep/trash decisions train the model, so tomorrow's slate is sharper.

```
  RSS feeds (in Zotero) → [ML gate → LLM] → ranked daily slate → you cull / read / label
        ▲                                                                      │
        └──────────────── retrain on your labels ◄─────────────────────────────┘
                 approved tag/collection changes → Zotero (backup first)
```

**Local-first · no telemetry · trained on _your_ labels** (nothing ships with the repo —
the model learns from how you triage). Zotero stays the source of truth; the app only
writes back changes you approve.

## Requirements

- **Python 3.10+** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**
- **Zotero desktop** with at least one **RSS feed** subscribed (arXiv, bioRxiv,
  or a **PubMed** saved search — see [docs/usage.md](docs/usage.md) "Adding sources")
- An **OpenAI-compatible LLM endpoint** — **local** (Ollama, vLLM, LM Studio,
  `mlx_lm.server`) or **hosted** (any API). You provide the base URL + key.
- *(developers only: Node 18+ to rebuild the UI — the built UI is already shipped)*

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

# 2. Run — first launch auto-creates goals.yaml + a .env skeleton and migrates the DB
uv run zotero-summarizer serve
```

First run bootstraps everything and the in-app **`/setup` wizard** walks you through the
rest — no manual file copying:

```
uv sync ─▶ serve ─▶ open the app ─▶ /setup wizard ─▶ set ONE secret in .env ─▶ Triage backlog
            (auto-bootstrap        Connect Zotero    by the env-var NAME the
             goals.yaml/.env       Connect LLM       wizard shows you (e.g.
             + migrate the DB)     Describe research  OPENAI_API_KEY) — then restart
```

Open <http://127.0.0.1:8000/>. A brand-new install lands on the **`/setup` wizard**
(Connect Zotero → Connect LLM → Describe research) with Zotero-path auto-detect and a live
LLM connection test. Prefer the terminal? Run `uv run zotero-summarizer setup` for the same
guided flow headless.

The wizard never asks for your raw API key — it collects the **env-var name** that holds it
(e.g. `OPENAI_API_KEY`). You set the actual secret value yourself in `.env` (or your shell),
then restart. With Zotero and the LLM connected, go to **Today** and click **Triage backlog**
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
| `.env` | the **one secret** (your API key, set by the name the wizard shows) | the app writes the two Zotero paths here for you via the `/setup` wizard / `setup` CLI |
| `goals.yaml` | nothing by hand | app-authored — edit research goals + LLM routing in **Settings**, don't hand-edit |

The Settings page is split into **Essentials** (research goals, triage criteria, the default
LLM provider, Zotero paths — always visible) and a collapsible **Advanced** section (full
stage routing, classifier gate, corpus). Secrets stay **name-only** everywhere in the UI: it
collects the env-var name, never the raw value. Everything else has working defaults. Full
reference in [docs/usage.md](docs/usage.md).

## Commands

```bash
uv run zotero-summarizer serve            # FastAPI server + browser UI (auto-bootstraps on first run)
uv run zotero-summarizer setup            # headless guided onboarding (same flow as the /setup wizard)
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
