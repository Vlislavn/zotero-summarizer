# Zotero Summarizer

Local-first Zotero paper triage app. It browses your local Zotero library, extracts PDF text, summarizes and scores papers with an OpenAI-compatible LLM, queues suggested Zotero changes for review, and only writes approved changes back to Zotero.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Build the React UI (Phase 1.18 — replaces the legacy ui.html)
cd frontend && npm install && npm run build && cd ..
cp .env.example .env
zotero-summarizer migrate
zotero-summarizer serve --host 127.0.0.1 --port 8000 --reload
```

OnPrem is a required dependency. It is listed in `pyproject.toml`, so the install command above installs it from PyPI. If needed, install it explicitly with `pip install onprem`.

Open:

```text
http://127.0.0.1:8000/
```

## Documentation

- [User guide](docs/user_guide.md) — daily workflow, what each tab does, how the verdict labels work.
- [Frontend dev guide](docs/frontend.md) — React layout, shared components, how to add a route.
- [Configuration](docs/configuration.md), [API](docs/api.md), [Architecture](docs/architecture.md), [Operations](docs/operations.md), [Feeds](docs/feeds.md).
- [Architecture decisions](docs/decisions/) — most recent: [2026-05-15 UI redesign](docs/decisions/2026-05-15-ui-redesign.md).

## Configure

Edit `.env`:

```dotenv
OPENAI_API_KEY=your_api_key_here          # primary LLM (summaries, library triage)
OPENAI_API_BASE=https://api.openai.com/v1
PDF_ROOT=/Users/your-user/Zotero/storage
ZOTERO_DATA_DIR=/Users/your-user/Zotero

# Optional: provider for the "Today" backlog triage (scores feed papers
# with a SOTA model). Point at api.kather.ai or any OpenAI-compatible API.
CUSTOM_BASE_URL=https://api.kather.ai/v1
CUSTOM_API_KEY=your_kather_api_key_here
```

`ONPREM_PATH` is optional and only exists for unusual local source-checkout setups. The normal path is PyPI installation with `pip install onprem`.

> **Two LLM providers, by design.** `OPENAI_API_*` drives summaries and
> library triage (often a fast local model via Ollama/vLLM). `CUSTOM_*`
> drives the on-demand "Today" backlog drain, which scores the freshest
> feed papers with a SOTA model (`model: sota` on api.kather.ai). The
> cheap LightGBM gate fast-rejects obvious non-matches first, so only the
> survivors cost a SOTA call. If `CUSTOM_*` is unset, the backlog-triage
> button reports that the provider is not configured; everything else
> still works.

Edit `goals.yaml` for research goals, triage criteria, model names, and prompts. Keep the LLM base URL generic:

```yaml
llm:
  draft_model: GPT-OSS-120B
  refine_model: GPT-OSS-120B
  api_base: ${OPENAI_API_BASE}
  api_key_env: OPENAI_API_KEY
```

## Commands

```bash
zotero-summarizer serve              # FastAPI server (browser UI)
zotero-summarizer mcp                # MCP server over stdio
zotero-summarizer migrate            # Init/migrate local SQLite stores
zotero-summarizer smoke-test         # Verify package + app construction
```

Feed processor (primary workflow — runs in the background):

```bash
zotero-summarizer feeds list                              # Discover feed names and IDs
zotero-summarizer feeds serve                             # Long-running background daemon
zotero-summarizer feeds serve --model qwen3:8b            # Use a different model temporarily
zotero-summarizer feeds run --feeds "Agents"              # One-shot: exhaust one feed by name
zotero-summarizer feeds run --feeds 2 --model qwen3:8b   # One-shot by ID with model override
zotero-summarizer feeds tick                              # Single tick (cron-friendly, no lock)
```

Low-level server alternative:

```bash
uvicorn zotero_summarizer.api.app:app --host 127.0.0.1 --port 8000 --reload
```

## Web UI

Single-page React app at `frontend/`, served by FastAPI at `/`. Primary tabs:

- **Today (cull)** — your daily slate of feed papers, ranked, each card
  tagged with its **bucket** + source **feed**. Make a binary, batch call:
  tick papers and **Add to library** (materialize into the Zotero *Inbox*,
  positive training signal) or **Trash** (negative signal). No fine label
  here. If no papers are scored yet, Today **auto-runs a background triage**
  of your feed backlog (via the `CUSTOM_*` SOTA provider) and fills in when
  it finishes; if a window is empty it falls back to recent items so the tab
  is never blank.
- **Library → Read next (read)** — your *unread* library papers ranked by
  model relevance (★ score + a one-line reason); already-read items (🧠/👀
  emoji) hidden by default with a toggle. Click one to read + label it.
- **Annotate** — set/override the fine `must`/`should`/`could`/`don't` label
  on every golden-CSV / library / feed row. Your manual label always wins: it
  drives the list badge and the priority filters, and survives a
  Refresh-labels re-export. Keyboard: `j`/`k` to move, `1`/`2`/`3`/`4` to
  label. A `🎯 border` filter surfaces the highest-information papers.
- **Settings** — research goals, triage criteria, model config, plus
  **Refresh labels** (re-export the golden CSV from Zotero) and **Retrain
  model** (rebuild the gate on your current labels).

Power tools (Library, Triage, Feed Review, Pending, Re-label Audit) live behind
a "More" disclosure in the top navigation.

Dev: `cd frontend && npm run dev` (port 5173, proxies `/api/*` to `:8000`).
Build: `cd frontend && npm run build`.

## Safety Model

Triage never writes directly to Zotero. It creates pending tag, note, and collection changes. You review those changes in the UI, then explicitly apply or reject them. Apply creates a Zotero SQLite backup first.

## Verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m zotero_summarizer.cli smoke-test
```
