# cli — the `zotero-summarizer` command-line interface

Each command group lives in its own module and owns both its handlers and its
argparse registration. `__init__.build_parser()` just wires the groups together,
so no single file holds the whole parser. `feeds tick --force-daily` was removed:
a daemon tick can no longer auto-Add or reject a pick. `feeds select-daily`
is a read-only diagnostic preview; reviewed user Adds use Today/Review.

```
__init__.build_parser()
   ├─ register_app(subparsers)        # _app.py:     serve · mcp · migrate · smoke-test · prefetch-models · verify-deep-review
   ├─ register_setup(subparsers)      # _setup.py:   setup · doctor · calibrate
   ├─ register_feeds(subparsers)      # _feeds.py:   feeds run/list/serve/tick/preview/select-daily
   ├─ register_goldenset(subparsers)  # _goldenset.py: export · train · eval-baseline · tune · suggest
          ├─ register_goldenset_feedback(gs_sub)  # _goldenset_feedback.py: analyze-feedback
          ├─ register_goldenset_classify(gs_sub)  # _goldenset_classify.py: classify · classify-llm
          ├─ register_goldenset_predict(gs_sub)   # _goldenset_predict.py:  predict-feed · analyze-notes · compare
          ├─ register_goldenset_migrate(gs_sub)   # _goldenset_migrate.py:  migrate-verdicts-to-zotero
          └─ register_goldenset_setup_tag_colors(gs_sub)  # _goldenset_setup_colors.py: setup-tag-colors
   ├─ register_faithbench(subparsers)  # _faithbench.py: faithbench build/run/judge/report
   └─ register_research_feed(...)      # _research_feed.py: research-feed run · view
main() = parse args → validate command budgets → install Settings → dispatch
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
| `_faithbench.py` | `faithbench build/run/judge/report` — faithfulness mini-benchmark of the deep_review-stage model (span-verified QA + traps + review-claim grounding). Build stages the review CSV before publishing the immutable benchmark file as its version marker; QA runs require explicit approval of every generated review-CSV row and bind that CSV hash into the manifest. `build --max-builder-windows` bounds contiguous 6,000-character source windows per paper; its CLI/env/default precedence, preflight behavior and cost are documented below. `run` is resumable via `--run-id` and takes `--provider/--model` to sweep a model for THIS run only (no goals.yaml edit; recorded in the manifest); `judge` uses the pinned remote judge (`CUSTOM_BASE_URL`/`CUSTOM_API_KEY`). See `services/faithbench/README.md` |
| `_research_feed.py` | Default RSS `research-feed run --from … --to … [--venue …]`: bounded weekly JSON+Markdown/deep review unless cached; Zotero stays dry-run unless explicitly queued. `--source telegram --input-jsonl …` selects saved-news shadow processing before startup; collect/cached/view never invoke external gates. |

`serve --host` accepts only localhost or literal loopback addresses, refuses
wildcard/remote binds with a nonzero error, and disables Uvicorn proxy-header
rewriting. Programmatic serve dispatch repeats the bind check before launch.
No listener is terminated, and no address is silently substituted. The app
factory separately guards transport peer, Host and browser Origin. This is not
remote mobile authentication; see the [deployment boundary](../../docs/local-deployment-boundary.md).

Handlers use lazy imports inside the function bodies to keep CLI startup fast.

`research-feed run --source telegram --input-jsonl POSTS --project-root ROOT`
imports canonical saved posts into the selected triage store, then explicitly runs
shadow reranking and the configured DEFAULT LLM route (not the feed override).
Obtain permitted-content/provider consent before processing originals. Add
`--collect-only` for import/export with no model/provider/config requirement;
`--cached-only` projects the existing store/profile/current configured identity
without new input, external calls, migrations or store writes. `research-feed view
--report SAVED.md` prints the retained Markdown only. Live collection/auth is not
implemented; missing input in process/collect modes errors before Settings.
Conflicting modes, Telegram venue/Zotero queue and RSS-only use of input/collect
flags also fail there. RSS remains the default and still requires both dates before
Settings/startup. Optional dates/review wait do not filter/time Telegram inference.
The selected Settings budget bounds reranker waiting, not every provider request:
the OpenAI factory forwards it while native Anthropic retains its adapter default.
Evaluation identity records the configured wait input, not an effective deadline
receipt. Shared shortlist/card defaults remain 20/10; strict admission is unchanged. See the
[news contract](../services/research_feed/README.md#offline-saved-telegram-news).

`goldenset analyze-feedback --project-root ROOT [--cutoff AWARE_ISO]` publishes one
private JSON/Markdown feedback report and prints one compact versioned receipt
with report/full-receipt pointers (no provenance inventory dump). The optional
cutoff requires a timezone and normalizes to UTC. It reuses selected Settings without
startup, migrations, Zotero or models; capture/analysis runs in a clean offline child
with external wall/RSS/available-RAM/swap-growth safeguards. Technical budgets/seed
are config/env only, not extra UI knobs. Missing/corrupt required input prints a
failure receipt then propagates/nonzero. Failure `published` is true/false/null for
owned commit/not committed/unknown, not foreign destination existence; unknown and
failure never confirm publication or authorize a cursor. Failure-only
`receipt_persistence` prints not_written/written/failed plus a safe secondary class,
never raw messages. Failure-file I/O preserves the original exception and computed
receipt; non-I/O secondary failure stays loud with receipt/context intact. Receipt
paths are attempted locators, not guaranteed usable; failed need not mean absent,
and no fsync/power-loss guarantee is implied. No support still publishes an
`insufficient_data` report with gaps/hashes. Default-off weekly automation and
confirmation/recovery are documented below; installed-host evaluation and scientific
sufficiency remain unverified. Publication currently requires Darwin's atomic
no-replace primitive; see [operation contract](../services/golden/feedback_analysis/README.md).
UX source: issue47 public [Laws of UX](https://lawsofux.com/). Occam subtracts dashboards,
export knobs and parallel command families; Tesler puts capture/publication/cleanup
in the system; Cognitive Load keeps this one command and one visible receipt.

Faithbench run IDs are single ASCII alphanumeric/underscore/hyphen names, not
paths. The common run/judge/report path resolver rejects traversal and symlink
escapes outside the selected project's `data/faithbench/runs/` directory.
Faithbench validates numeric and comma-separated options before Settings/handler
I/O. Builds need at least two papers and positive QA/trap counts; runs need positive
repetitions and optional QA limits, nonempty duplicate-free conditions/tracks from
the supported names. `--limit` requires the QA track. Parsed `RunOptions` are reused
by the handler with only provider concurrency applied afterward; invalid input
exits with argparse status 2, not a successful zero-work run. For builds,
`--max-builder-windows` accepts 1–256; precedence is explicit CLI option > shell
`ZS_FAITHBENCH_QA_MAX_BUILDER_WINDOWS` > the selected project's `.env` value for
that key > default (32). The effective per-paper budget is recorded in benchmark
metadata, and every selected paper is preflighted before the first builder inference
call. Each additional 6,000-character window adds a builder request and cost.
Run records its QA limit and binds the benchmark hash to the exact parsed bytes;
resume refuses a changed limit. Judge/report reject a missing/malformed or changed
benchmark SHA before judging/publication. Report owns artifact loading, not a second
CLI-supplied copy of the items/manifest/path. Run identity additionally binds
resolved model/decomposer profiles, full config, prompt/schema/source hashes,
timeout, semantic benchmark content and execution options before any provider
client is built. The runner independently repeats the guard before journal repair
or model work. Legacy unidentified runs remain intact but require a new run ID.

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

Research-feed uses the same pre-Settings validation point: shortlist 1–100,
cards 1–20 and review timeout 1–86,400 seconds, sharing the model's budget types
with direct service calls. Zero/negative/oversized overrides exit 2 before startup.
The handler reuses the dispatcher's Settings instead of loading it a second time.
`research-feed --cached-only` skips app startup: existing RSS/review data can be
projected without a goals file, classifier, provider setup or background work.
Generation uses existing `startup(background=False)`, so it does not prewarm,
recover server jobs, retrain or rescore unrelated papers. Normal configured model
prerequisites still apply to generation: an enabled classifier with an existing
golden dataset needs a compatible cached artifact, prepared explicitly beforehand.

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
Hosted setup saves routing before optional real inference (default No). Canonical
loopback locality, not the `hosted` name, invokes shared setup admission; UNKNOWN
cannot construct a client. `setup --mode no-llm` saves a valid ML-only choice that
can later change in Settings.

`migrate-verdicts-to-zotero` plans from the full verdict snapshot, without the old
5000-row cutoff. Dry-run and apply use the same list; library-only checks, connector
guard, idempotent tag comparison and the single batch backup remain unchanged.

### Local review attempt capture

`verify-deep-review --item-key KEY` uses the production strategy dispatcher,
including map/reduce and the original-source verifier, in read-only/no-Zotero-note
mode. Each explicit CLI attempt writes hash/count diagnostics to
`Settings.data_dir/deep_review_attempts/<random-id>/attempt.json`, including pipeline failures.
Provider/model names, effective config identity, strategy/budget and the selected
PDF fingerprint (when present) identify the new attempt, not a historical replay.
Add `--capture-local` only with explicit local consent to retain sensitive actual
prompts and decoded client-returned values. Originals, generated notes, correction
and verifier events are distinct. The schema labels values as decoded, not raw
HTTP transport. Captures use private directory/file modes, are not uploaded and
contain no serialized provider/client configuration or transport cookies/headers.
Default events contain hashes/counts only. No online accuracy claim follows from
these traces; stage counts are instrumentation, not review-success scores.

Diagnostic CLI source reads reuse the single-component/no-symlink paper-state guard. Admission happens before provider construction. Legacy full_text is explicitly labelled unverified legacy scope; empty sources retain local failure diagnostics. Diagnostic calls disable thinking without changing app routing.

`feeds serve` alone passes selected Settings to optional weekly feedback automation
on its main asyncio thread. It is default-disabled (`ZS_FEEDBACK_SCHEDULE_ENABLED`)
and adds no CLI flag/status command. `feeds run/tick`, server background work and
manual `goldenset analyze-feedback` keep their existing contracts. The daemon log
and Settings-resolved cursor/status/receipt pointers expose bounded catchup,
publication/reuse/failure/insufficient/no-new-data states. No cursor is committed
before confirmed receipts and the post-child cancellation yield. See the additive
[weekly contract](../services/golden/feedback_analysis/README.md#weekly-operation-additive-manual-and-analytic-contracts-unchanged).
