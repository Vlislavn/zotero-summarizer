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
CUSTOM_BASE_URL=https://api.kather.ai/v1
CUSTOM_API_KEY=your_kather_api_key_here
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
| `CUSTOM_BASE_URL` | For Today triage | OpenAI-compatible base URL for the "Today" backlog triage (e.g. `https://api.kather.ai/v1`). Unset = the backlog-triage button reports the provider is missing |
| `CUSTOM_API_KEY` | For Today triage | API key for `CUSTOM_BASE_URL`. The backlog drain uses `model: sota` on this endpoint |
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

### Today backlog triage provider (`CUSTOM_*`)

The "Today" tab's on-demand backlog drain (`POST /api/daily/triage-backlog`)
scores survivors of the gate with a **second** provider, independent of the
`llm:` block above. It is configured purely from `.env`:

```dotenv
CUSTOM_BASE_URL=https://api.kather.ai/v1
CUSTOM_API_KEY=your_kather_api_key_here
```

The model id is `sota` (the alias api.kather.ai exposes); the client is built
by `services/_adapters.build_triage_llm()`. This keeps the primary `llm:`
provider (often a fast local model) for summaries + library triage while the
backlog drain uses a stronger SOTA model only on the gate's survivors. If
`CUSTOM_*` is unset the drain refuses to start with a clear error; nothing
else is affected.

### Switching to OpenAI (api.openai.com)

OpenAI's API rejects unknown request fields with HTTP 400. The repo defaults
omit any provider-specific kwargs, so a fresh OpenAI setup works out of the box:

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_API_BASE=https://api.openai.com/v1
```

```yaml
llm:
  draft_model: gpt-4o-mini
  refine_model: gpt-4o-mini
  api_base: ${OPENAI_API_BASE}
  api_key_env: OPENAI_API_KEY
  # extra_body intentionally omitted for OpenAI
```

If you're switching back to a vLLM-served reasoning model (OnPrem, qwen3, etc.),
re-enable the `enable_thinking=false` toggle by setting `extra_body`:

```yaml
llm:
  extra_body:
    chat_template_kwargs:
      enable_thinking: false
```

Anything you put under `llm.extra_body` is forwarded verbatim to the underlying
OpenAI-compatible client as the `extra_body` request kwarg.

### `prestige:` section (Phase 1.8 OpenAlex enrichment)

Off by default. Flip `enabled: true` to blend author h-index, venue impact, and
citation count into the composite score:

```yaml
prestige:
  enabled: true
  weight: 0.15                   # share of the LLM component
  cache_ttl_days: 30
  fallback_neutral: 3.0          # used when OpenAlex has no record
  user_agent_email: "you@example.com"   # polite-pool mailto
  require_doi: false             # skip title-fallback lookups
```

When enabled the daemon logs one line per triaged item:

```
[tick_…] prestige: h=22 venue=3450 cites=156 score=3.78 composite=3.45
```

### `full_text_refine:` section (Phase 1.8 two-stage triage)

Off by default. When enabled, after plateau selection picks the top items the
daemon fetches their open-access PDFs (arXiv direct → Unpaywall → URL ending in
`.pdf`) and re-scores them with the full-text pipeline before materializing:

```yaml
full_text_refine:
  enabled: true
  top_k: 2                       # refine only the top-K plateau picks
  max_pdf_bytes: 20000000        # hard cap on download size (~20 MB)
  fetch_timeout_secs: 30
  unpaywall_email: "you@example.com"   # required for non-arXiv OA papers
```

PDFs are streamed to `~/.cache/zotero-summarizer/pdfs/` and verified against
the `%PDF` magic bytes before extraction. Failures (no OA source, timeout,
non-PDF response) are logged and the abstract-derived score is kept.

### `feeds:` section (RSS feed daemon)

The feed daemon ([feeds.md](feeds.md)) reads its config from the `feeds:` block:

```yaml
feeds:
  enabled: true
  inbox_collection_name: Inbox
  dedup_against_library: true
  # --- daemon ----------------------------------------------------------
  daemon_enabled: true
  daemon_tick_seconds: 300        # 5 min between ticks
  daemon_batch_size: 5            # items LLM-scored per tick
  mark_processed_as_read: true    # write feedItems.readTime = now
  outcome_window_days: 7          # wait N days before scoring outcome
  outcome_check_per_tick: 3       # resolve up to N due outcomes per tick
  # --- daily selection -------------------------------------------------
  # Use daily_selection_at for clock-based delivery (fires once per calendar
  # day after the target local time).  Use daily_selection_interval_hours as
  # a fallback when daily_selection_at is not set (fires every N hours).
  daily_selection_at: "08:00"           # fire at 08:00 local time every day
  # daily_selection_interval_hours: 24  # alternative: fire every 24 h
  daily_window_hours: 24
  daily_target_min: 1
  daily_target_max: 2
  daily_force_black_swan_every_run: false
  # --- legacy one-shot CLI --------------------------------------------
  default_since_days: 7
  default_item_type: journalArticle

selection:
  target_fraction: 0.05    # used by the one-shot `feeds run` only
  hard_min: 10
  hard_max: 15
  kneedle_sensitivity: 1.0

surprise:
  black_swan_fraction: 0.10
  min_score: 0.30
  black_swan_tag: "🦢 black-swan"
```

Tuning notes:

- **Throughput**: raise `daemon_batch_size` if your hardware can take more
  LLM calls per tick; lower `daemon_tick_seconds` for tighter cadence.
- **More aggressive pre-filter**: raise `corpus.similarity_threshold` from
  −0.3 toward 0.0 to fast-reject more items before LLM calls.
- **Different daily target**: change `daily_target_max` (default 2). Going
  higher than ~5/day defeats the "1–2 best" goal.
- **Fixed delivery time**: set `daily_selection_at: "08:00"` to receive papers
  at the same local time every morning. Remove the key (or set
  `daily_selection_interval_hours: 24`) to fall back to "fire every 24 h since
  the last run" behavior.
- **Per-session model override**: `--model TEXT` on `feeds serve`, `feeds run`,
  or `feeds tick` overrides `llm.refine_model` for that session without editing
  this file. Useful for temporarily switching to a faster local model.

### `classifier_gate:` section (Phase 1.13/1.14 fast-reject)

The classifier gate batch-predicts feed items before the LLM. See
[feeds.md §Classifier gate](feeds.md#phase-113-classifier-gate-fast-reject-before-llm)
for the full prose and current performance numbers.

```yaml
classifier_gate:
  enabled: true                      # off → daemon runs without the gate
  model_name: lightgbm               # lightgbm (default, fast + SHAP) | tabpfn | logreg
  drop_priorities: [dont_read]       # priorities that skip the LLM
  raw_score_dont_read_below: 0.05    # see "Calibration override" below
  pca_dim: 100                       # TabPFN only
  n_folds: 5                         # CV folds for threshold derivation
  audit_sample_per_tick: 1           # Phase 1.15 counterfactual audit
```

**Calibration override (`raw_score_dont_read_below`)**: the isotonic
calibrator inflates the low end of the score distribution, so the trained
`t_could` threshold can collapse to ~0.001 — meaning no item is ever
predicted `dont_read` from the calibrated score alone. When the override
is > 0, items whose **raw** (uncalibrated) probability falls below it are
force-flipped to `dont_read` inside the daemon's `_apply_classifier_gate`.
This is a Phase 1.14 workaround for an underlying calibration pathology;
it does not currently apply to `classifier.predict_new_items` callers
(see [feeds.md](feeds.md) for the scope caveat and
[baseline-ceiling-20260515.md](baseline-ceiling-20260515.md) for the
broader 4-class κ ≈ 0.04 issue).

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
