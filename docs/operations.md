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
zotero-summarizer serve              # FastAPI server (browser UI)
zotero-summarizer mcp                # MCP server over stdio
zotero-summarizer migrate            # Init/migrate local SQLite stores
zotero-summarizer smoke-test         # Verify package + app construction
```

### Feed processor

The primary user workflow — see [feeds.md](feeds.md) for the full guide.

```bash
zotero-summarizer feeds list                                    # Show feeds (names + IDs)
zotero-summarizer feeds preview <id> --unread-only              # Peek at unread items
zotero-summarizer feeds serve                                   # Background daemon
zotero-summarizer feeds serve --model qwen3:8b                  # Daemon with model override
zotero-summarizer feeds serve --feeds "Agents" --max-ticks 10   # Bounded daemon run
zotero-summarizer feeds run --feeds "Agents"                    # One-shot: all unread from feed
zotero-summarizer feeds run --feeds 2 --model qwen3:8b          # By ID with model override
zotero-summarizer feeds tick                                    # One tick, no lock (cron-safe)
zotero-summarizer feeds select-daily                            # Manually trigger daily selection
```

`--feeds` accepts either numeric IDs (`--feeds 2,5`) or **name substrings** (`--feeds "Agents"`). Run `feeds list` to discover names. Disambiguation: if a substring matches more than one feed, the command exits with a helpful error.

`--model TEXT` overrides `goals.yaml llm.refine_model` for the current session only — no file edits needed. Useful for switching to a faster local model without touching config.

`feeds serve` and `feeds run` acquire a PID lock (`feeds.lock` in the project root). A second invocation while one is running prints the active PID and exits. `feeds tick` and `feeds select-daily` are designed to run alongside the daemon and do NOT acquire the lock.

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

## Interpreting classifier-gate metrics

Two artifacts describe how well the paper-prediction model performs:

- `~/.cache/zotero-summarizer/models/lightgbm.json` — the deployed model's
  metadata. `oof_auc` here is a **single-fold point estimate** from the
  training run (the OOF prediction concatenated across the training-time
  K-fold split). It does not carry a confidence interval and should not
  be quoted as the model's "true" AUC.
- `eval-baseline-*.json` and [docs/baseline-ceiling-20260515.md](baseline-ceiling-20260515.md)
  — produced by `goldenset eval-baseline`, this is a 5×5 repeated
  stratified K-fold CV with BCa-bootstrap (B=2000) confidence intervals.
  **Always quote the CI'd numbers from this artifact**, not `lightgbm.json`.

Today's honest baseline (n=1393, commit b2f116e):

```
Spearman ρ = 0.205  [0.183, 0.224]   # primary ranking metric
AUC        = 0.570  [0.557, 0.584]   # binary keep-vs-skip ability
NDCG@10    = 0.694  [0.668, 0.720]   # top-10 ranking quality
Cohen's κ  = 0.042  [0.035, 0.049]   # 4-class agreement — near zero
```

The 4-class κ ≈ 0.04 means `must / should / could / dont` chips in the UI
should be read as decoration, not as a confident classification. Sort and
trust the composite ranking, not the discrete label.

The learning curve in [baseline-ceiling-20260515.md §3](baseline-ceiling-20260515.md)
shows Spearman **peaking at n=836** and declining to n=1393 with
non-overlapping 95% CIs, suggesting label-quality regression in the most
recently added ~550 labels. The recommended diagnostic experiment is
retraining on the n=836 subset (or with `in_trash` / low-signal rows
filtered out) to test H1 directly.

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

Feed daemon log pattern (each line is prefixed with the tick ID):

```
[tick_…] found 12 unread: feed2=7 feed3=5          ← items found per feed
[tick_…] triage [1/12] feed2: "Paper title..."      ← per-item progress
[tick_…] skip dedup: "Title" (already in library)   ← dedup info
[daily_…] → inbox: "Title"  composite=4.10          ← selected
[daily_…] materialized: "Title"  key=AB12CD34       ← written to Zotero
```

If the Zotero DB is locked (Zotero syncing), you will see:

```
WARNING: DB locked [apply_feed_materialization] (attempt 1/3) — retrying in 5s
```

The writer retries up to 3× with 5-second waits. If all retries fail, the item stays in `triaged_pending` state and is automatically included in the next daily selection run (within 24 h).

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

`daily_materialized: 0` in feeds run output:

- Usually caused by a Zotero DB lock (Zotero syncing). The writer retries 3× automatically.
- If Zotero was actively syncing and all retries failed, items stay `triaged_pending` and will be materialized on the next run within 24 h.
- Check for `WARNING: DB locked` lines in the log.

`feeds run / feeds serve` prints "daemon already running (PID …)":

- Another `feeds serve` or `feeds run` process is active.
- Kill it or wait for it to finish before starting a new one.
- If the process crashed without cleaning up, delete `feeds.lock` from the project root manually.

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
