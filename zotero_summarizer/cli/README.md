# cli — the `zotero-summarizer` command-line interface

Each command group lives in its own module and owns both its handlers and its
argparse registration. `__init__.build_parser()` just wires the groups together,
so no single file holds the whole parser.

```
__init__.build_parser()
   ├─ register_app(subparsers)        # _app.py:     serve · mcp · migrate · smoke-test · prefetch-models
   ├─ register_feeds(subparsers)      # _feeds.py:   feeds run/list/serve/tick/preview/select-daily
   └─ register_goldenset(subparsers)  # _goldenset.py: export · train · eval-baseline · tune · suggest
          ├─ register_goldenset_classify(gs_sub)  # _goldenset_classify.py: classify · classify-llm
          └─ register_goldenset_predict(gs_sub)   # _goldenset_predict.py:  predict-feed · analyze-notes · compare
main() = build_parser().parse_args(argv).func(args)
```

| file | responsibility |
|---|---|
| `__init__.py` | `build_parser()` + `main()` (the entry point `zotero-summarizer`) |
| `__main__.py` | enables `python -m zotero_summarizer.cli` |
| `_helpers.py` | shared CLI helpers: feed-id resolution, the feeds lock, run-log writing, slugs |
| `_app.py` | `serve` (uvicorn `api.app:create_app`, `factory=True`) / `mcp` / `migrate` / `smoke-test` / `prefetch-models` (download the ML models for offline use; `--check` reports cache status). `__init__.apply_offline_env()` (called at CLI import, before any transformers import) turns `ZS_OFFLINE`/`HF_HUB_OFFLINE` into cache-only model loading |
| `_feeds.py` | the `feeds` subcommands (drive the RSS daemon) |
| `_goldenset.py` | golden-set export + ML lifecycle (train/eval/tune/suggest) + group wiring |
| `_goldenset_classify.py` · `_goldenset_predict.py` | the heavier classify/predict/analyze commands (`classify-llm` runs any OpenAI-compatible model) |

Handlers use lazy imports inside the function bodies to keep CLI startup fast.
