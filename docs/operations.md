# Operations

## Install

OnPrem is required and installed from PyPI.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -e ".[dev]"` installs the package dependencies from `pyproject.toml`, including `onprem`. To install it manually, use:

```bash
pip install onprem
```

Verify the dependency is importable:

```bash
python -c "import onprem; print('onprem ok')"
```

## Start

```bash
zotero-summarizer migrate
zotero-summarizer serve --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Dashboard:

```text
http://127.0.0.1:8000/results
```

## CLI

```bash
zotero-summarizer serve
zotero-summarizer mcp
zotero-summarizer migrate
zotero-summarizer smoke-test
```

Low-level server command:

```bash
uvicorn zotero_summarizer.api.app:app --host 127.0.0.1 --port 8000 --reload
```

## Verification

```bash
curl -s http://127.0.0.1:8000/api/health | python -m json.tool
.venv/bin/python -m pytest -q
.venv/bin/python -m zotero_summarizer.cli smoke-test
```

Expected health shape:

```json
{
  "status": "ok",
  "config_loaded": true,
  "draft_model": "GPT-OSS-120B",
  "refine_model": "GPT-OSS-120B",
  "api_base": "https://api.openai.com/v1"
}
```

## Smoke Checklist

1. Start the server.
2. Confirm `GET /api/health` returns `status: ok`.
3. Open `/` and verify the library page loads.
4. Confirm `/api/zotero/status` is available.
5. Select a Zotero item with a local PDF.
6. Run triage.
7. Confirm pending tag/note/collection changes are queued.
8. Review pending changes.
9. Apply approved changes.
10. Open `/results` and verify the result appears.

## MCP

The MCP server is API-client based. Start the local FastAPI server first, then run:

```bash
zotero-summarizer mcp
```

Set `ZOTERO_SUMMARIZER_API_BASE` if the API is not running at the default:

```dotenv
ZOTERO_SUMMARIZER_API_BASE=http://127.0.0.1:8000
```

## Logs

Default log file:

```text
server.log
```

Watch progress:

```bash
tail -f server.log
```

Typical long-running logs include:

- batch start and finish
- per-item progress
- PDF extraction timing
- refine and triage timing
- persistence success/failure
- item errors and timeouts

## Zotero Saved Searches

After applying priority tags, create Zotero saved searches:

- `zs:must_read`
- `zs:should_read`
- `zs:could_read`
- `zs:dont_read`

These become live reading queues inside Zotero.

## Troubleshooting

`zotero_unavailable`:

- Check `ZOTERO_DATA_DIR`.
- Confirm `zotero.sqlite` exists.
- Close Zotero before write operations unless force apply is intentional.

`path_not_allowed`:

- The requested PDF is outside `PDF_ROOT`.
- Set `PDF_ROOT` to the Zotero storage directory or a parent directory.

`llm_timeout`:

- Raise `SUMMARY_TIMEOUT_SECONDS`.
- Use a faster model.
- Confirm the endpoint in `OPENAI_API_BASE` is reachable.

Empty LLM responses from reasoning models:

- The app passes `chat_template_kwargs.enable_thinking=false`.
- If using a custom adapter, preserve that setting for vLLM-served reasoning models.

Stale scores after editing `goals.yaml`:

- Restart the server.
- Re-run triage for affected items.
