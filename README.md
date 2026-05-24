# Zotero Summarizer

Local-first paper-triage assistant for [Zotero](https://www.zotero.org/). It reads
the RSS feeds you follow in Zotero, scores each new paper for how worth-reading it
is (a cheap ML gate first, an LLM for the survivors), and gives you a small daily
slate to cull. Your keep/trash decisions train the model, so tomorrow's slate is
sharper. Zotero stays the source of truth — the app only writes back tags and
collection memberships you approve, after backing up first.

Everything runs on your machine. There is **no shipped training data**: the model
learns from *your* labels (see [Bring your own ground truth](#bring-your-own-ground-truth)).

## Prerequisites

- **Python 3.10+**
- **Node 18+** (to build the React UI)
- **Zotero desktop** with at least one **RSS feed** subscribed (the app reads
  Zotero's own SQLite DB; it never logs into anything on your behalf)
- An **OpenAI-compatible LLM endpoint** — a local one (Ollama, vLLM, LM Studio)
  or a hosted API. You provide the base URL + key.

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Build the UI
cd frontend && npm install && npm run build && cd ..

# 3. Configure (both files are gitignored — see Configuration below)
cp .env.example .env            # then edit: LLM key/URL + Zotero paths
cp goals.example.yaml goals.yaml # then edit: your research goals

# 4. Create the local databases
zotero-summarizer migrate

# 5. Run
zotero-summarizer serve --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. On the **Today** tab, click **Triage backlog** to
score your unread feed papers, then start culling.

## How you use it

Two stages, both of which train the model:

1. **Today (cull).** A ranked slate of fresh feed papers. For each, make one
   binary call — **Add to library** (keep, a positive signal → materialized into
   your Zotero *Inbox*) or **Trash** (a negative signal). No fine-grained label here.
2. **Library → Read next, then Annotate (label).** Your unread library papers
   ranked by relevance. When you actually read one, give it the fine label
   (`must` / `should` / `could` / `don't`) and notes in **Annotate**.

Open PDFs and take notes in Zotero as usual; come back here to triage.

## Two ways to run triage

Scoring papers always runs the same pipeline (gate → LLM → SQLite). What *triggers*
it is your choice:

- **UI, on demand (simplest).** Just run `serve` and click **Triage backlog** on
  *Today* whenever you want fresh papers scored. No background process.
- **Daemon, automatic (optional).** `zotero-summarizer feeds serve` runs a
  background loop that scores unread items every few minutes and, each morning,
  auto-materializes the 1–2 best into your Inbox — so a slate is waiting without
  you clicking anything. The `feeds.*` config block only matters if you run this.

Use whichever fits. The daemon is convenience, not a requirement.

## Bring your own ground truth

The model is trained on a **golden dataset** that *you* build — nothing ships with
the repo. You create it just by using the app:

1. **Cull and label** as above. Add/Trash on *Today* and the fine labels in
   *Annotate* are recorded as engagement signals. (You can also signal directly in
   Zotero with emoji tags: 🧠 deeply engaged, 👀 skimmed, 👎 not relevant.)
2. **Refresh labels** (Settings tab, or `zotero-summarizer goldenset export`) writes
   `data/zotero-summarizer-golden.csv` from those signals — that file *is* your
   ground truth.
3. **Retrain model** (Settings, or `zotero-summarizer goldenset train`) rebuilds the
   relevance gate on your labels.

Until you have labels, the gate stays off and the daemon/UI simply LLM-scores
everything — the app is fully usable while your dataset grows. A few dozen
keep/trash decisions is enough to start; quality improves as you label more.

## Configuration

Two files, both gitignored; copy the committed `*.example` templates to create them.

**`.env`** — secrets, paths, runtime knobs:

```dotenv
OPENAI_API_KEY=...                        # primary LLM (summaries, library triage)
OPENAI_API_BASE=https://api.openai.com/v1 # any OpenAI-compatible base URL
PDF_ROOT=/Users/you/Zotero/storage
ZOTERO_DATA_DIR=/Users/you/Zotero

# Optional second provider for the Today "Triage backlog" drain (a stronger
# model for the freshest papers). Any OpenAI-compatible endpoint; unset to skip.
CUSTOM_BASE_URL=https://your-provider.example/v1
CUSTOM_API_KEY=...
```

**`goals.yaml`** — your research goals, triage criteria, model names, and prompts.
Keep the LLM base URL generic so it follows `.env`:

```yaml
llm:
  draft_model: your-model
  refine_model: your-model
  api_base: ${OPENAI_API_BASE}
  api_key_env: OPENAI_API_KEY
```

All app state (the two SQLite DBs, your golden dataset, logs, ML artifacts) lives
under `data/` (gitignored).

## Commands

```bash
zotero-summarizer serve            # FastAPI server + browser UI
zotero-summarizer migrate          # init/upgrade the local SQLite stores
zotero-summarizer mcp              # MCP server over stdio (agent surface)
zotero-summarizer smoke-test       # verify the app constructs

# Feeds (optional daemon / one-shots)
zotero-summarizer feeds list       # discover feed names + IDs
zotero-summarizer feeds serve      # background daemon (auto-triage + daily pick)
zotero-summarizer feeds run --feeds "Agents"   # one-shot: exhaust one feed
zotero-summarizer feeds tick       # single tick (cron/launchd-friendly)

# Ground-truth lifecycle
zotero-summarizer goldenset export # write data/zotero-summarizer-golden.csv
zotero-summarizer goldenset train  # (re)train the relevance gate on your labels
```

## Safety model

Triage never writes directly to Zotero. It queues pending tag/note/collection
changes; you review and explicitly apply or reject them in the UI. Applying takes a
Zotero SQLite backup first.

## Development

```bash
pre-commit run --all-files                       # LOC / layering / README guardrails
KMP_DUPLICATE_LIB_OK=TRUE pytest -q --forked     # backend tests (see ARCHITECTURE.md)
cd frontend && npm run lint && npm test && npm run build
```

Architecture, layering rules, and the mental model live in
[docs/architecture.md](docs/architecture.md).
