# Usage guide

Everything beyond the [README](../README.md) Quickstart: first-run setup, the two ways to
run triage, how the model learns from your labels, offline use, the safety model, and the
full config reference. (Architecture and dev workflow live in [architecture.md](architecture.md).)

## First-run setup

There's nothing to copy and nothing to migrate by hand. On the first `serve` (or `setup`),
**Phase-0 bootstrap** runs: it writes a default `goals.yaml`, a minimal `.env` skeleton (a
*commented* secret placeholder + empty `PDF_ROOT` / `ZOTERO_DATA_DIR` lines — never a real
key), and migrates the triage DB if absent. It's idempotent — existing files are never
overwritten — so re-running is a no-op.

Then a **guided wizard** takes over. Two equivalent front ends, same `services/setup`
primitives underneath (they can't drift):

- **Web — the `/setup` wizard.** A brand-new install is redirected here once
  (skippable/resumable): **Connect Zotero** (auto-detects likely Zotero data dirs, picks the
  one whose `zotero.sqlite` exists) → **Connect LLM** (pick the provider, run a live
  connection test) → **Describe research** (your goals). Empty-state "finish setup" cards on
  `/today` and `/library` link back here until you're ready.
- **Terminal — `zotero-summarizer setup`.** The same three steps headless: detect/confirm
  the Zotero dir, configure + probe the LLM provider, set your research goals.

**Secrets are name-only.** Neither front end ever has a raw-secret field. You give the
**env-var name** that holds your key (e.g. `OPENAI_API_KEY`); the wizard checks whether that
var is set and runs an *advisory* reachability probe, but you set the actual value yourself
in `.env` (or your shell). That's deliberate: the app collects a name, you own the secret.

The wizard can finish before the secret/endpoint is live — **Next** gates only on a
structurally-valid provider (type + base URL + key env-var name + model), not on a passing
connection test. After editing `.env`, **restart the server** to apply the change.

## Two ways to run triage

Scoring papers always runs the same pipeline (cheap ML gate → LLM for survivors →
SQLite). What *triggers* it is your choice:

- **UI, on demand (simplest).** Run `serve` and click **Triage backlog** on *Today*
  whenever you want fresh papers scored. No background process.
- **Daemon, automatic (optional).** `zotero-summarizer feeds serve` runs a background
  loop that scores unread items every few minutes and, each morning, auto-materializes
  the 1–2 best into your Zotero *Inbox* — so a slate is waiting without you clicking
  anything. The `feeds.*` block in `goals.yaml` only matters if you run this.

The daemon is convenience, not a requirement.

## Bring your own ground truth

The relevance model is trained on a **golden dataset that you build** — nothing ships
with the repo. You create it just by using the app:

1. **Cull and label.** Add/Trash on *Today* and the fine labels (`must` / `should` /
   `could` / `don't`) in *Annotate* are recorded as engagement signals. You can also
   signal directly in Zotero with emoji tags: 🧠 deeply engaged, 👀 skimmed, 👎 not relevant.
2. **Refresh labels** (Settings tab, or `zotero-summarizer goldenset export`) writes
   `data/zotero-summarizer-golden.csv` from those signals — that file *is* your ground truth.
3. **Retrain model** (Settings, or `zotero-summarizer goldenset train-classifier`) rebuilds
   the relevance gate on your labels.

Until you have labels, the gate stays off and the app simply LLM-scores everything —
fully usable while your dataset grows. A few dozen keep/trash decisions is enough to
start; quality improves as you label more.

## Offline / air-gapped use

The app is **local-first**: the UI, Zotero I/O, the ML relevance gate, and Library
search (BM25 + embeddings + cross-encoder rerank) all run on your machine. There is no
telemetry and the served UI pulls no external assets (no CDN/fonts). Only two things
ever need the internet, and both are one-time or swappable:

1. **The ML models** download from Hugging Face on first use (see the [README hardware
   table](../README.md#requirements) for sizes). Pre-cache them once while online:

   ```bash
   uv run zotero-summarizer prefetch-models          # download what's needed
   uv run zotero-summarizer prefetch-models --check   # report what's cached, no download
   ```

   Then run with cache-only model loading (skips the Hugging Face hub check, so a
   disconnected machine never hangs on a network timeout):

   ```bash
   ZS_OFFLINE=1 uv run zotero-summarizer serve        # or set ZS_OFFLINE=1 / HF_HUB_OFFLINE=1 in .env
   ```

2. **The LLM** (summaries, deep review, quality review) needs an OpenAI-compatible
   endpoint. Point it at a **local** server — Ollama, vLLM, LM Studio, `mlx_lm.server` —
   in `goals.yaml` / `.env` and you're fully offline. With **no** LLM at all, everything
   still works except the LLM-written summaries; the **ML-only "Triage backlog"** drain
   (the classifier gate) still scores your feed, and Library search/ranking is unaffected.

The optional enrichments — OpenAlex prestige, Unpaywall, arXiv full-text fetch — are
**off by default** and skip gracefully when offline.

### Deep review on MLX (fast, on-demand)

The `deep_review` stage (the full-text digest + quality) sends ~16k-token prompts.
On ollama those prefill in ~3 min/call; `mlx_lm.server` does the same in ~16 s. So
`goals.yaml` routes **`deep_review` → the local MLX provider** (`:8080`) and keeps the
high-volume **`feed`/`backlog` → ollama** (`:11434`) — the feed daemon never depends
on MLX being up.

The 35B (~20 GB) is **on-demand + memory-gated** so it can't blow the 48 GB box:

```bash
tools/mlx-deep-review.sh   # frees ollama, refuses if RAM is tight, caps the KV
                          # cache (PROMPT_CACHE_BYTES=4G), then serves the 35B at :8080
```

It aborts with a clear message unless ~26 GiB is available (tunable: `MLX_MIN_FREE_GB`),
so it never swaps the machine. While MLX is down, the deep-review panel shows an
"unreachable" banner (`GET /api/admin/llm-reachability`) and triage keeps running on
ollama. Run it foreground (Ctrl-C stops it); single-instance.

**Prewarm on launch.** By default the app background-computes the top-5 not-yet-cached
deep reviews on startup (`quality_review.prewarm_on_startup_k`, env override
`ZS_DEEP_REVIEW_PREWARM_K`; `0` disables), so the **first** open is instant, not just the
second. It runs on the same single-flight job as the "Run deeper review" button — serial
on the local MLX (RAM-safe), parallel only on a remote provider — and skips papers that
already have a cached review, so re-launches are cheap. With MLX down it simply logs the
provider as unreachable and warms nothing.

## Safety model

Triage never writes directly to Zotero. It queues pending tag / note / collection
changes; you review and explicitly **apply or reject** them in the UI. Applying takes a
Zotero SQLite backup first. The app reads Zotero's local SQLite DB directly — it never
logs into anything on your behalf.

## Configuration reference

Two files under the project root, both gitignored and created for you on first run (see
[First-run setup](#first-run-setup)). The split of who owns what:

- **`.env` = secrets you set + the two Zotero paths the app manages.** You add your API key
  by name; the `/setup` wizard / `setup` CLI write `PDF_ROOT` / `ZOTERO_DATA_DIR` here for you.
- **`goals.yaml` = app-authored; don't hand-edit.** Edit research goals + LLM routing in the
  **Settings** page; the app serializes the file. (Hand-edits are tolerated but the app is the
  writer of record.)

### Settings page — Essentials vs Advanced

Settings is chunked to keep the common path short:

- **Essentials (always visible):** research goals, triage criteria, the default LLM provider,
  Zotero paths — plus a readiness strip (Zotero · LLM · Goals · Model).
- **Advanced (one collapsible disclosure):** full stage routing, classifier gate (sub-fields
  appear only when the gate is enabled), corpus.

The legacy `llm.draft_model / refine_model / api_base / api_key_env` inputs were **removed**
from the UI — they duplicated the LLM-routing editor. The backend still auto-migrates an old
`llm` block in `goals.yaml`, so existing configs keep working. The LLM API secret is
**name-only** in the UI — never a raw-secret field.

### The guarded `.env` path writer + restart banner

When the wizard or Settings writes a Zotero path, it goes through an **allowlisted,
validated** writer: only `PDF_ROOT` / `ZOTERO_DATA_DIR` may be set, the path must exist
(otherwise the write is rejected), and every other line in `.env` is preserved byte-for-byte
(your secret is never touched). Path changes are read at process start, so after a write the
UI shows a **restart banner** — restart `serve` to apply.

### Setup HTTP endpoints (`/api/setup/*`)

The wizard is backed by a small read-mostly contract you can also hit directly:

| Endpoint | What it does |
|---|---|
| `GET /api/setup/status` | one readiness probe across config / LLM (provider, key-*presence* bool, advisory reachability) / paths / Zotero / trained classifier, with a `ready` gate |
| `GET /api/setup/detect-zotero` | read-only per-OS probe for likely Zotero data dirs (DB-present first) |
| `PUT /api/setup/paths` | the allowlisted `.env` path writer described above (422 on a bad/non-allowlisted key) |
| `POST /api/setup/validate-config` | dry-run config validation + an optional provider connection probe; persists nothing |

No setup response ever contains a raw secret — `api_key_env` is always just the env-var
**name**, and key state is a presence boolean.

### `.env` — secrets, paths, runtime knobs

You set the **secret** rows by hand; the **path** rows are written for you by the wizard /
`setup` CLI (you can still edit them directly).

| Key | Required? | What it is |
|---|---|---|
| `OPENAI_API_KEY` | yes (the one secret) | API key for the primary LLM. This is the value the wizard collects *by name* — you set it here yourself. (Use whatever name your provider profile's `api_key_env` points at.) |
| `OPENAI_API_BASE` | no | Optional OpenAI-compatible base URL when your provider profile references `${OPENAI_API_BASE}`; otherwise the base URL lives in the provider profile in `goals.yaml` |
| `PDF_ROOT` | app-managed | Your Zotero PDF storage, e.g. `/Users/you/Zotero/storage` — written by the setup flow; blank → defaults to your home dir |
| `ZOTERO_DATA_DIR` | app-managed | Your Zotero data dir, e.g. `/Users/you/Zotero` — written by the setup flow; blank → defaults to `~/Zotero` |
| `CUSTOM_BASE_URL` / `CUSTOM_API_KEY` | no | Optional second provider for the *Today* "Triage backlog" drain (a stronger model for the freshest papers). Leave blank to skip |
| `SUMMARY_TIMEOUT_SECONDS` | no | LLM call timeout (default 900) |
| `TRIAGE_JOB_CONCURRENCY` | no | Parallel triage jobs (default 4, max 16) |
| `ZS_OFFLINE` | no | `1` → cache-only model loading (offline) |
| `ZS_DEEP_REVIEW_PREWARM_K` | no | Top-N deep reviews to background-prewarm on launch (overrides `quality_review.prewarm_on_startup_k`, default 5; `0` disables) |
| `APP_LOG_LEVEL` / `APP_LOG_FILE` | no | Logging |

### `goals.yaml` — research goals, models, prompts

**App-authored — edit it through Settings, not by hand.** First run writes a valid default
with placeholder research goals; you replace them in the **`/setup` wizard**, the `setup`
CLI, or the Settings **Essentials** panel. The parts a new user cares about:

```yaml
research_goals:                 # 1–6 free-form lines; what the model optimizes for
  - Multiagent systems in clinical and scientific research
  - Multimodal AI for clinics
triage_criteria: [ ... ]        # the evaluation rubric (ships with sensible defaults)
llm_routing:                    # provider profiles + per-stage model routing
  ...                           # edit in Settings → Advanced; the secret is name-only
                                # (api_key_env names an env var; the value lives in .env)
```

The default provider and per-stage routing are managed in Settings (Advanced → stage
routing). A legacy top-level `llm:` block in older configs is **auto-migrated** into
`llm_routing` on load — its UI inputs were removed (they duplicated the routing editor).

Other blocks (`corpus`, `prestige`, `feeds`, `quality_review`, `classifier_gate`, …)
have working defaults — leave them until you need them. `quality_review.shadow_claim_check`
(default off) opts into the local MiniCheck encoder claim-checker; see
[services/model/README.md](../zotero_summarizer/services/model/README.md).

All app state (the two SQLite DBs, your golden dataset, logs, ML artifacts) lives under
`data/` (gitignored).

## Command reference

```bash
uv run zotero-summarizer serve            # FastAPI server + browser UI (auto-bootstraps goals.yaml/.env + migrates on first run)
uv run zotero-summarizer setup            # headless guided onboarding (Zotero dir, LLM provider, research goals)
uv run zotero-summarizer migrate          # init / upgrade the local SQLite stores (serve does this for you; here for re-runs)
uv run zotero-summarizer mcp              # MCP server over stdio (agent surface)
uv run zotero-summarizer smoke-test       # verify the app constructs
uv run zotero-summarizer prefetch-models  # download the ML models for offline use (--check = status only)

# Feeds (optional daemon / one-shots)
uv run zotero-summarizer feeds list                 # discover feed names + IDs
uv run zotero-summarizer feeds serve                # background daemon (auto-triage + daily pick)
uv run zotero-summarizer feeds run --feeds "Agents" # one-shot: exhaust one feed
uv run zotero-summarizer feeds tick                 # single tick (cron/launchd-friendly)

# Ground-truth lifecycle
uv run zotero-summarizer goldenset export            # write data/zotero-summarizer-golden.csv
uv run zotero-summarizer goldenset train-classifier  # (re)train the relevance gate on your labels
```
