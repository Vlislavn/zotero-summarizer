# Configuration

Configuration is split between `.env` and `goals.yaml`.

`.env` is for local secrets, paths, and runtime knobs. It is ignored by git.

`goals.yaml` is for user-editable product behavior: research goals, triage criteria, model names, prompt templates, and corpus settings.

## Required OnPrem Dependency

The app uses OnPrem for the LLM wrapper and PDF extraction adapter. OnPrem is a required PyPI dependency in `pyproject.toml`, so it is installed by:

```bash
pip install -e ".[dev]"
```

Manual equivalent:

```bash
pip install onprem
```

`ONPREM_PATH` in `.env` is only a fallback import hint for unusual local source-checkout setups.

## Environment Variables

Create a local `.env` from the template:

```bash
cp .env.example .env
```

Recommended `.env` schema:

```dotenv
OPENAI_API_KEY=your_api_key_here
OPENAI_API_BASE=https://api.openai.com/v1
SUMMARY_TIMEOUT_SECONDS=900
TRIAGE_JOB_CONCURRENCY=4
PDF_ROOT=/Users/your-user/Zotero/storage
ZOTERO_DATA_DIR=/Users/your-user/Zotero
ONPREM_PATH=/Users/your-user/code/onprem
ZOTERO_SUMMARIZER_HOME=/Users/your-user/code/personal/zotero-summarizer
APP_LOG_LEVEL=INFO
APP_LOG_FILE=server.log
```

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | API key passed to the OpenAI-compatible LLM endpoint |
| `OPENAI_API_BASE` | Yes | Generic OpenAI-compatible base URL used by `goals.yaml` |
| `SUMMARY_TIMEOUT_SECONDS` | No | Per-paper timeout, default `420` |
| `TRIAGE_JOB_CONCURRENCY` | No | Parallel papers per triage job, default `4`, max `16` |
| `PDF_ROOT` | Recommended | Restricts PDF reads to this directory tree |
| `ZOTERO_DATA_DIR` | Recommended | Local Zotero data directory containing `zotero.sqlite` |
| `ONPREM_PATH` | No | Fallback path to a local OnPrem checkout; normal installs should use PyPI package `onprem` |
| `ZOTERO_SUMMARIZER_HOME` | No | Project/data root for `.env`, `goals.yaml`, and local SQLite files |
| `APP_LOG_LEVEL` | No | Backend log level, default `INFO` |
| `APP_LOG_FILE` | No | Log file path relative to project root, default `server.log` |

## `goals.yaml` Schema

Top-level shape:

```yaml
research_goals:
  - Your active research goal
triage_criteria:
  - What makes a paper worth reading
relevance_scale:
  1: Low relevance
  2: Some relevance
  3: Medium relevance
  4: High relevance
  5: Critical relevance
reading_priority_scale:
  must_read: Critical paper
  should_read: Highly relevant paper
  could_read: Useful reference
  dont_read: Skip
summary_structure:
  - Executive Summary
output_language: English
llm:
  draft_model: GPT-OSS-120B
  refine_model: GPT-OSS-120B
  api_base: ${OPENAI_API_BASE}
  api_key_env: OPENAI_API_KEY
prompts:
  refine: "..."
  triage: "..."
corpus:
  enabled: true
  embedding_model: sentence-transformers/all-MiniLM-L6-v2
  similarity_threshold: -0.3
  stale_days_for_weak_negative: 30
```

`api_base: ${OPENAI_API_BASE}` is expanded from `.env` at startup. Keep the provider URL in `.env` so the repo remains generic.

## LLM Notes

The app uses an OpenAI-compatible endpoint through the OnPrem LLM wrapper. The LLM adapter passes:

```python
extra_body={"chat_template_kwargs": {"enable_thinking": False}}
```

This matters for vLLM-served reasoning models because thinking mode can consume the entire token budget before content is produced.

To switch providers:

1. Change `OPENAI_API_BASE` in `.env`.
2. Change `llm.draft_model` and `llm.refine_model` in `goals.yaml`.
3. Restart the server.
4. Check `GET /api/health`.

## Local Data

The app creates these files under `ZOTERO_SUMMARIZER_HOME` or the project root:

- `triage_history.db`
- `corpus_cache.db`
- `server.log`

These are runtime files and are ignored by git.
