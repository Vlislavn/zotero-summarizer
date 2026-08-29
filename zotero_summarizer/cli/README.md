# cli — the `zotero-summarizer` command-line interface

Each command group lives in its own module and owns both its handlers and its
argparse registration. `__init__.build_parser()` just wires the groups together,
so no single file holds the whole parser.

```
__init__.build_parser()
   ├─ register_app(subparsers)        # _app.py:     serve · mcp · migrate · smoke-test · prefetch-models · verify-deep-review
   ├─ register_setup(subparsers)      # _setup.py:   setup · doctor · calibrate
   ├─ register_feeds(subparsers)      # _feeds.py:   feeds run/list/serve/tick/preview/select-daily
   ├─ register_goldenset(subparsers)  # _goldenset.py: export · train · eval-baseline · tune · suggest
          ├─ register_goldenset_classify(gs_sub)  # _goldenset_classify.py: classify · classify-llm
          ├─ register_goldenset_predict(gs_sub)   # _goldenset_predict.py:  predict-feed · analyze-notes · compare
          ├─ register_goldenset_migrate(gs_sub)   # _goldenset_migrate.py:  migrate-verdicts-to-zotero
          └─ register_goldenset_setup_tag_colors(gs_sub)  # _goldenset_setup_colors.py: setup-tag-colors
   ├─ register_faithbench(subparsers)  # _faithbench.py: faithbench build/run/judge/report
   └─ register_research_feed(...)      # _research_feed.py: research-feed run
main() = build_parser().parse_args(argv).func(args)
```

| file | responsibility |
|---|---|
| `__init__.py` | `build_parser()` + `main()` (the entry point `zotero-summarizer`) |
| `__main__.py` | enables `python -m zotero_summarizer.cli` |
| `_helpers.py` | shared CLI helpers: feed-id resolution, the feeds lock, run-log writing, slugs |
| `_app.py` | `serve` (uvicorn `api.app:create_app`, `factory=True`; **reclaims its port first** via `_free_port` — `lsof` finds any leftover listener, SIGTERM→SIGKILL — so a re-run replaces the old server instead of dying on `Errno 48 address already in use`; skip with `--no-kill`) / `mcp` / `migrate` / `smoke-test` / `prefetch-models` (download the ML models for offline use; `--check` reports cache status; includes the MiniCheck claim-checker only when `quality_review.shadow_claim_check` is on). `__init__.apply_offline_env()` (called at CLI import, before any transformers import) turns `ZS_OFFLINE`/`HF_HUB_OFFLINE` into cache-only model loading. `verify-deep-review` runs the real digest + quality path headlessly against the live `deep_review` model on ONE already-built paper's cached `qa_text` (`--item-key`, default `4NIMLFMV`; `--with-goals` adds the heavier goal board) — prints per-phase timing + the digest JSON; the end-to-end "does a review actually produce a digest" check without Zotero or the server. It computes `sub_concurrency` via the SAME `_common.deep_review_sub_concurrency` helper the background job uses (and prints it in the tier line), so its wall-clock is a faithful production receipt — a remote provider's rubric/goal sub-calls run in parallel here exactly as they would in the app. `--provider`/`--model` override the deep_review stage for THIS run only (e.g. `--provider default --model qwen3.5:4b` to drive the pipeline against a local ollama model when the configured provider is unreachable/over-budget) |
| `_setup.py` | `setup` reuses bootstrap/path/goals services; `--mode local --profile light\|balanced\|existing` resolves the same hardware-gated profile definitions as web and prints (never runs) an explicit model pull command. Hosted and skip/no-LLM paths remain. `doctor [--json|--fix|--check id]` runs the shared persisted real-pipeline checklist; `calibrate` retains measured hardware/endpoint tuning. |
| `_feeds.py` | the `feeds` subcommands (drive the RSS daemon) |
| `_goldenset.py` | golden-set export + ML lifecycle (train/eval/tune/suggest) + group wiring |
| `_goldenset_classify.py` · `_goldenset_predict.py` | the heavier classify/predict/analyze commands (`classify-llm` runs any OpenAI-compatible model) |
| `_goldenset_migrate.py` | `migrate-verdicts-to-zotero` — one-time transfer of in-app verdicts (`label_verdicts`) into Zotero `label:<priority>` tags (`--dry-run`, idempotent, library items only, single batch backup) |
| `_goldenset_setup_colors.py` | `setup-tag-colors` — prints the one-time Zotero setup (colors + number keys 1-4 for the four `label:<priority>` tags) for native keypress labeling. Non-destructive (prints the plan; writes nothing into your synced Zotero settings); `--json` for machine output |
| `_faithbench.py` | `faithbench build/run/judge/report` — faithfulness mini-benchmark of the deep_review-stage model (span-verified QA + traps + review-claim grounding). `run` is resumable via `--run-id` and takes `--provider/--model` to sweep a model for THIS run only (no goals.yaml edit; recorded in the manifest); `judge` uses the pinned remote judge (`CUSTOM_BASE_URL`/`CUSTOM_API_KEY`). See `services/faithbench/README.md` |
| `_research_feed.py` | `research-feed run --from … --to … [--venue …]`: bounded weekly JSON+Markdown; generates missing cards through existing deep review unless `--cached-only`; Zotero stays dry-run unless `--queue-zotero` is explicit. |

Handlers use lazy imports inside the function bodies to keep CLI startup fast.
Hosted setup persists routing before its optional probe; `setup --mode no-llm`
persists a valid ML-only choice that can later be changed in Settings.
