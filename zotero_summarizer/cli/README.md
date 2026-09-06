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
main() = parse args → validate goldenset budgets → install Settings → dispatch
```

| file | responsibility |
|---|---|
| `__init__.py` | `build_parser()` + `main()` (the entry point `zotero-summarizer`) |
| `__main__.py` | enables `python -m zotero_summarizer.cli` |
| `_helpers.py` | shared CLI helpers: feed-id resolution, run-log writing, slugs |
| `_app.py` | `serve` (uvicorn `api.app:create_app`, `factory=True`; leaves existing listeners intact and reports a bind error on an occupied port; the process-killing helper and `--no-kill` option are removed) / `mcp` / `migrate` / `smoke-test` / `prefetch-models` (download the ML models for offline use; `--check` reports cache status; includes the MiniCheck claim-checker only when `quality_review.shadow_claim_check` is on). `__init__.apply_offline_env()` (called at CLI import, before any transformers import) turns `ZS_OFFLINE`/`HF_HUB_OFFLINE` into cache-only model loading. `verify-deep-review` runs the real digest + quality path headlessly against the live `deep_review` model on ONE already-built paper's cached `qa_text` (`--item-key`, default `4NIMLFMV`; `--with-goals` adds the heavier goal board) — prints per-phase timing + the digest JSON; the end-to-end "does a review actually produce a digest" check without Zotero or the server. It computes `sub_concurrency` via the SAME `_common.deep_review_sub_concurrency` helper the background job uses (and prints it in the tier line), so its wall-clock is a faithful production receipt — a remote provider's rubric/goal sub-calls run in parallel here exactly as they would in the app. `--provider`/`--model` override the deep_review stage for THIS run only (e.g. `--provider default --model qwen3.5:4b` to drive the pipeline against a local ollama model when the configured provider is unreachable/over-budget) |
| `_setup.py` | `setup` reuses bootstrap/path/goals services; `--mode local --profile light\|balanced\|existing` resolves the same hardware-gated profile definitions as web and prints (never runs) an explicit model pull command. Hosted and skip/no-LLM paths remain. `doctor [--json|--fix|--check id]` runs the shared persisted real-pipeline checklist; `calibrate` retains measured hardware/endpoint tuning. |
| `_feeds.py` | the `feeds` subcommands (drive the RSS daemon) |
| `_goldenset.py` | golden-set export + ML lifecycle (train/eval/tune/suggest) + group wiring |
| `_goldenset_classify.py` · `_goldenset_predict.py` | the heavier classify/predict/analyze commands (`classify-llm` runs any OpenAI-compatible model) |
| `_goldenset_migrate.py` | `migrate-verdicts-to-zotero` — one-time transfer of in-app verdicts (`label_verdicts`) into Zotero `label:<priority>` tags (`--dry-run`, idempotent, library items only, single batch backup) |
| `_goldenset_setup_colors.py` | `setup-tag-colors` — prints the one-time Zotero setup (colors + number keys 1-4 for the four `label:<priority>` tags) for native keypress labeling. Non-destructive (prints the plan; writes nothing into your synced Zotero settings); `--json` for machine output |
| `_faithbench.py` | `faithbench build/run/judge/report` — faithfulness mini-benchmark of the deep_review-stage model (span-verified QA + traps + review-claim grounding). `run` is resumable via `--run-id` and takes `--provider/--model` to sweep a model for THIS run only (no goals.yaml edit; recorded in the manifest); `judge` uses the pinned remote judge (`CUSTOM_BASE_URL`/`CUSTOM_API_KEY`). See `services/faithbench/README.md` |
| `_research_feed.py` | `research-feed run --from … --to … [--venue …]`: bounded weekly JSON+Markdown; generates missing cards through existing deep review unless `--cached-only`; Zotero stays dry-run unless `--queue-zotero` is explicit. |

Handlers use lazy imports inside the function bodies to keep CLI startup fast.

`goldenset classify` and `classify-llm` snapshot hybrid ground truth before model
work and evaluate current results against that same snapshot. The shared CSV
reader and hybrid overlay are reused; publishing predictions does not overwrite
source labels. CV/holdout keys come from the current report, and a limited LLM run
does not score old columns on unprocessed rows. Verdict changes during a run apply
to the next run, not its metrics.
Both commands take rows and `input_csv_sha256_prefix` from one read before model
work or prediction publication. Later CSV replacement cannot change the logged
input hash. The hash describes the whole source CSV snapshot, before hybrid labels
and optional filters; it does not claim to hash the cross-store effective dataset.

All ML lifecycle commands use the selected project's effective label overlay:
`train-classifier` passes `triage_db_path` for both forced training and cached
loading; `eval-baseline` (including learning curves), `tune`, `predict-feed` and
Tier-3 `calibrate` apply the same `hybrid_gt.apply_hybrid` before model work.
This includes continuous targets, machine-add outcome tiers and retraction
filtering, without rewriting the golden source. Changed verdicts invalidate
model reuse through the existing training-input identity. Missing/broken stores
raise; CLI does not silently switch to raw CSV labels. Fold/feature leakage is
separate from label routing.

`predict-feed` reads the advertised bounded unread pool; the orphaned
`exclude_annotated` branch (no parser option ever supplied it) is removed.
There is no new exclusion option or generic CLI loader.

Goldenset numeric/coupled validation runs in `main()` immediately after argparse,
before Settings loading or handler I/O. The group owns one validator rather than
per-option type factories or a custom parser. Folds require ≥2; counts, row caps,
workers, PCA and maximum note length require ≥1. Abstract truncation and minimum
note length allow zero; omitted optional caps still mean all. Holdout must be
finite in [0, 1), retaining zero as its explicit disable value. Note bounds must
be ordered, and seeds plus repeat offsets must fit the 32-bit RNG range.
Learning fractions require `--learning-curve`, must be nonempty, finite, strictly
increasing in (0, 1], and are parsed once before dispatch. Learning-curve fitting
now receives the advertised `--n-repeats` budget (including the CLI default 5);
it previously ignored that option and used the service default 3. Invalid input
exits with argparse status 2; no source/output mutation or model/provider work.
Raw `build_parser().parse_args()` remains syntactic parsing; semantic command
validation belongs to the sole production dispatcher. Other command groups keep
their own contracts.

`predict-feed` no longer accepts unused `--calibration`/`--threshold-strategy`;
its regressor has no such tuning step. `classify` retains its used options.
`classify-llm` resolves the credential environment name from the same config as
its model/endpoint, unless `--api-key-env` explicitly overrides it. A missing
configured credential raises; another provider's environment key is not used.
Provider/schema failure aborts `classify-llm` with the original exception before
CSV predictions, benchmark reports or run logs are written, including when earlier
rows succeeded. Successful summaries use `rows_skipped` (missing title/abstract),
not the former `rows_failed`; provider failures are no longer successful summaries.

`main()` installs the selected project's Settings before dispatch, so nested
provider construction uses the same timeout and model/tuning paths as the command.
Default model/history outputs are `data/models/`; tuning uses
`data/optuna-best-params.json`. Training publishes `<classifier>.zip` with model
and metadata together; run logs report the resolved artifact path (legacy joblib
inputs remain readable). Existing explicit output overrides still work.

Feed commands share bootstrap; tick exclusion lives in the service, not a CLI
PID file. `feeds tick` omission uses the configured daemon batch; `feeds run`
passes None to read all unread rows. Both return nonzero for item/provider errors.
`feeds serve --max-ticks 0` exits before startup, negative counts fail, and daemon
errors propagate instead of entering an automatic retry loop. `feeds run --dry-run`
disables startup background jobs/retraining/rescoring and simulates triage against
the existing RSS pool without refreshing subscriptions or writing decisions.
Hosted setup persists routing before its optional probe; `setup --mode no-llm`
persists a valid ML-only choice that can later be changed in Settings.

`migrate-verdicts-to-zotero` plans from the full verdict snapshot, without the old
5000-row cutoff. Dry-run and apply use the same list; library-only checks, connector
guard, idempotent tag comparison and the single batch backup remain unchanged.
