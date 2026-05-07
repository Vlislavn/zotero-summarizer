# Zotero Summarizer

Local-first Zotero paper triage app. It browses your local Zotero library, extracts PDF text, summarizes and scores papers with an OpenAI-compatible LLM, queues suggested Zotero changes for review, and only writes approved changes back to Zotero.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
zotero-summarizer migrate
zotero-summarizer serve --host 127.0.0.1 --port 8000 --reload
```

OnPrem is a required dependency. It is listed in `pyproject.toml`, so the install command above installs it from PyPI. If needed, install it explicitly with `pip install onprem`.

Open:

```text
http://127.0.0.1:8000/
```

## Configure

Edit `.env`:

```dotenv
OPENAI_API_KEY=your_api_key_here
OPENAI_API_BASE=https://api.openai.com/v1
PDF_ROOT=/Users/your-user/Zotero/storage
ZOTERO_DATA_DIR=/Users/your-user/Zotero
```

`ONPREM_PATH` is optional and only exists for unusual local source-checkout setups. The normal path is PyPI installation with `pip install onprem`.

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
zotero-summarizer serve
zotero-summarizer mcp
zotero-summarizer migrate
zotero-summarizer smoke-test
```

Low-level server alternative:

```bash
uvicorn zotero_summarizer.api.app:app --host 127.0.0.1 --port 8000 --reload
```

## Docs

- [How It Works](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [API Schemas](docs/api.md)
- [Operations](docs/operations.md)

## Safety Model

Triage never writes directly to Zotero. It creates pending tag, note, and collection changes. You review those changes in the UI, then explicitly apply or reject them. Apply creates a Zotero SQLite backup first.

## Verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m zotero_summarizer.cli smoke-test
```
