from __future__ import annotations

import argparse
import json
from pathlib import Path

from zotero_summarizer.settings import Settings
from zotero_summarizer.cli._helpers import (
    _persist_run_log,
    _slugify_model,
    _utc_iso_now,
)


def _goldenset_classify(args: argparse.Namespace) -> int:
    """Train SPECTER2 + classifier on the golden labels and score every row via CV.

    Writes per-classifier columns to the golden CSV (never overwrites another
    classifier's data) and appends a JSONL line to ``classifier-runs.jsonl``
    with the full config + metrics snapshot.
    """
    import csv as _csv

    from zotero_summarizer.services.model import classifier
    from zotero_summarizer.services.golden import hybrid_gt
    from zotero_summarizer.services import run_log
    from zotero_summarizer.services._common import read_config

    settings = Settings.load(project_root=args.project_root)
    input_csv = Path(args.input or settings.golden_csv_path)
    if not input_csv.exists():
        raise FileNotFoundError(f"Golden CSV not found at {input_csv}; run `goldenset export` first.")

    with input_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(_csv.DictReader(f))

    # Phase 1.18 Step 2: overlay user verdicts from label_verdicts. This
    # closes the loop on the Annotate UI — labels typed in the React tool
    # now act as ground truth for the next train.
    rows = hybrid_gt.apply_hybrid(rows, settings.triage_db_path)
    n_user = sum(1 for r in rows if r.get("_hybrid_source") == hybrid_gt.SOURCE_USER)
    if n_user:
        print(f"  hybrid GT: {n_user} user-overridden labels applied")

    def _progress(done: int, total: int) -> None:
        print(f"  embedded {done}/{total}", flush=True)

    goals_config = read_config(settings.config_path)
    report = classifier.cross_validate(
        rows,
        corpus_db_path=settings.corpus_db_path,
        n_folds=args.folds,
        classifier_name=args.classifier,
        pca_dim=args.pca_dim,
        holdout_fraction=args.holdout_fraction,
        calibration=args.calibration,
        threshold_strategy=args.threshold_strategy,
        goals_config=goals_config,
        progress_cb=_progress,
    )
    updated = classifier.write_predictions_to_csv(
        input_csv, report, classifier_name=args.classifier,
    )

    strength_filter = (
        {s.strip() for s in args.strength.split(",") if s.strip()}
        if args.strength
        else None
    )
    priority_col = f"cls_{args.classifier}_priority"
    metrics_cv = classifier.compute_metrics_against_gold(
        input_csv, strength_filter=strength_filter, split="cv",
        priority_column=priority_col,
    )
    metrics_holdout = classifier.compute_metrics_against_gold(
        input_csv, strength_filter=strength_filter, split="holdout",
        priority_column=priority_col,
    )

    output = {
        "run_id": run_log.make_run_id(args.classifier),
        "timestamp": _utc_iso_now(),
        "git_commit": run_log.short_git_commit(settings.project_root),
        "classifier": args.classifier,
        "calibration": args.calibration,
        "threshold_strategy": args.threshold_strategy,
        "config": {
            "n_folds": args.folds,
            "pca_dim": args.pca_dim,
            "holdout_fraction": args.holdout_fraction,
            "strength_filter": sorted(strength_filter) if strength_filter else None,
        },
        "thresholds": {
            "keep": round(report.optimal_threshold, 4),
            "must": round(report.must_threshold, 4),
            "could": round(report.could_threshold, 4),
        },
        "cv": {
            "n_rows": report.n_rows,
            "n_positive": report.n_positive,
            "auc": round(report.auc, 4),
            "metrics_vs_gold": metrics_cv,
        },
        "holdout": {
            "n_rows": report.holdout_n_rows,
            "n_positive": report.holdout_n_positive,
            "auc": round(report.holdout_auc, 4),
            "metrics_vs_gold": metrics_holdout,
        },
        "embeddings_computed": report.embeddings_computed,
        "embeddings_cached": report.embeddings_cached,
        "elapsed_seconds": round(report.elapsed_seconds, 1),
        "csv_updated_rows": updated,
        "input_csv": str(input_csv),
        "input_csv_sha256_prefix": run_log.file_sha256(input_csv),
    }
    _persist_run_log(settings, output)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def _goldenset_classify_llm(args: argparse.Namespace) -> int:
    """4th classifier in the comparison: LLM reads title+abstract and classifies directly."""
    import csv as _csv
    import os
    import time

    from zotero_summarizer.services.model import classifier, llm_classifier
    from zotero_summarizer.services._adapters import build_llm
    from zotero_summarizer.services._common import read_config, setup_logging

    settings = Settings.load(project_root=args.project_root)
    setup_logging()
    config = read_config(settings.config_path)

    api_base = (args.api_base or config.llm.api_base).rstrip()
    api_key_env = (args.api_key_env or config.llm.api_key_env).strip()
    model_name = (args.model or config.llm.refine_model).strip()
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"environment variable {api_key_env} is not set; "
            "add it to .env or export it before running."
        )
    # Drop OnPrem-specific extra_body when the caller pointed at a different
    # backend — OpenAI / OpenRouter / custom endpoints reject unknown args.
    extra_body = config.llm.extra_body if not args.api_base else None
    llm = build_llm(api_base, model_name, api_key, max_tokens=2048, extra_body=extra_body)

    input_csv = Path(args.input or settings.golden_csv_path)
    if not input_csv.exists():
        raise FileNotFoundError(f"golden CSV not found at {input_csv}")
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(_csv.DictReader(f))

    rows = all_rows
    if args.strength:
        wanted = {s.strip() for s in args.strength.split(",") if s.strip()}
        rows = [r for r in rows if (r.get("gold_signal_strength") or "").strip() in wanted]
    if args.limit is not None and args.limit > 0:
        rows = rows[: args.limit]

    print(
        f"classifying {len(rows)} rows via {model_name!r} at {api_base} "
        f"(workers={args.workers}) …",
        flush=True,
    )
    start = time.perf_counter()

    def _progress(done: int, total: int) -> None:
        print(f"  classified {done}/{total}", flush=True)

    classifications = llm_classifier.classify_papers_with_llm(
        rows,
        llm,
        research_goals=list(config.research_goals or []),
        workers=args.workers,
        progress_cb=_progress,
    )
    classifier_slug = (args.classifier_name or _slugify_model(model_name)).strip()
    updated = llm_classifier.write_predictions_to_csv(
        input_csv, classifications, classifier_name=classifier_slug,
    )
    elapsed = time.perf_counter() - start

    strength_filter = (
        {s.strip() for s in args.strength.split(",") if s.strip()}
        if args.strength
        else None
    )
    priority_col = f"cls_{classifier_slug}_priority"
    metrics = classifier.compute_metrics_against_gold(
        input_csv,
        strength_filter=strength_filter,
        priority_column=priority_col,
    )

    from zotero_summarizer.services import run_log
    summary = {
        "run_id": run_log.make_run_id(classifier_slug),
        "timestamp": _utc_iso_now(),
        "git_commit": run_log.short_git_commit(settings.project_root),
        "classifier": classifier_slug,
        "type": "llm_judge",
        "model": model_name,
        "api_base": api_base,
        "config": {
            "workers": args.workers,
            "strength_filter": sorted(strength_filter) if strength_filter else None,
            "limit": args.limit,
        },
        "rows_processed": len(rows),
        "rows_with_priority": sum(1 for c in classifications if c.priority),
        "rows_failed": sum(1 for c in classifications if c.error),
        "csv_updated_rows": updated,
        "elapsed_seconds": round(elapsed, 1),
        "cv": {
            "n_rows": metrics.get("total", 0),
            "n_positive": metrics.get("binary", {}).get("support", 0),
            "auc": None,
            "metrics_vs_gold": metrics,
        },
        "holdout": {},
        "input_csv": str(input_csv),
        "input_csv_sha256_prefix": run_log.file_sha256(input_csv),
    }
    _persist_run_log(settings, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0



def register_goldenset_classify(gs_sub) -> None:
    gs_classify = gs_sub.add_parser(
        "classify",
        help=(
            "SPECTER2 + logistic-regression classifier on the golden labels "
            "(5-fold CV). Replaces LLM-from-abstract scoring for ranking."
        ),
    )
    gs_classify.add_argument(
        "--input",
        default=None,
        help="Path to golden CSV. Default: <project-root>/data/zotero-summarizer-golden.csv",
    )
    gs_classify.add_argument(
        "--folds",
        type=int,
        default=5,
        help="Stratified k-fold count. Default: 5.",
    )
    gs_classify.add_argument(
        "--classifier",
        choices=["logreg", "tabpfn", "lightgbm"],
        default="logreg",
        help=(
            "Which classifier to train per fold. 'logreg' = sklearn "
            "LogisticRegression on full 773-d features (fast, default). "
            "'lightgbm' = LightGBM gradient-boosted trees on same features. "
            "'tabpfn' = TabPFN-v2 transformer (~600MB first call, requires "
            "TABPFN_TOKEN); PCA-reduces embedding to --pca-dim first."
        ),
    )
    gs_classify.add_argument(
        "--pca-dim",
        type=int,
        default=100,
        help="PCA target dim for the SPECTER2 embedding when --classifier=tabpfn. Default: 100.",
    )
    gs_classify.add_argument(
        "--holdout-fraction",
        type=float,
        default=0.20,
        help=(
            "Stratified fraction held out of CV for the final test set. "
            "Held-out rows are NEVER seen during training/calibration/threshold "
            "tuning — they give an honest estimate of deployment performance. "
            "0.0 disables. Default: 0.20."
        ),
    )
    gs_classify.add_argument(
        "--calibration",
        choices=["isotonic", "sigmoid", "none"],
        default="isotonic",
        help=(
            "Probability calibration applied per fold. 'isotonic' (default) "
            "is non-parametric. 'sigmoid' is Platt scaling. 'none' uses raw."
        ),
    )
    gs_classify.add_argument(
        "--threshold-strategy",
        choices=["youden", "f1"],
        default="youden",
        help=(
            "How to pick the binary keep/skip threshold from OOF probabilities. "
            "'youden' maximises TPR-FPR (balanced). 'f1' maximises F1 (recall-leaning)."
        ),
    )
    gs_classify.add_argument(
        "--strength",
        default=None,
        help=(
            "Comma-separated gold_signal_strength filter applied to metrics "
            "(does NOT change the training set). Default: all."
        ),
    )
    gs_classify.add_argument("--project-root", default=None)
    gs_classify.set_defaults(func=_goldenset_classify)

    gs_cls_llm = gs_sub.add_parser(
        "classify-llm",
        help=(
            "4th classifier in the lineup: LLM reads title+abstract and "
            "classifies directly (single prompt per paper, parallel batch). "
            "Comparable to LogReg / LightGBM / TabPFN. Override the endpoint "
            "with --api-base + --api-key-env + --model to test any "
            "OpenAI-compatible model."
        ),
    )
    gs_cls_llm.add_argument(
        "--input",
        default=None,
        help="Golden CSV. Default: <project-root>/data/zotero-summarizer-golden.csv",
    )
    gs_cls_llm.add_argument(
        "--strength",
        default=None,
        help="Comma-separated gold_signal_strength filter (high|medium|low). Default: all.",
    )
    gs_cls_llm.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on rows processed (post-strength filter). Default: all.",
    )
    gs_cls_llm.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel LLM-call workers (ThreadPoolExecutor). Default: 4.",
    )
    gs_cls_llm.add_argument(
        "--model",
        default=None,
        help=(
            "Override LLM model. Default: goals.yaml refine_model. "
            "Pass the model id exposed by your endpoint."
        ),
    )
    gs_cls_llm.add_argument(
        "--api-base",
        default=None,
        help=(
            "Override OpenAI-compatible endpoint. Default: goals.yaml api_base. "
            "e.g. 'https://your-provider.example/v1'."
        ),
    )
    gs_cls_llm.add_argument(
        "--api-key-env",
        default="CUSTOM_API_KEY",
        help=(
            "Env var holding the API key. Default: 'CUSTOM_API_KEY' "
            "(add it to .env first). Use 'OPENROUTER_API_KEY' for OpenRouter, "
            "or 'OPENAI_API_KEY' for the default goals.yaml provider."
        ),
    )
    gs_cls_llm.add_argument(
        "--classifier-name",
        default=None,
        help=(
            "Short slug used as the CSV column prefix and run_log identifier. "
            "Default: auto-derived from the model name (e.g. 'llm_nvidia_nemotron_3_super')."
        ),
    )
    gs_cls_llm.add_argument("--project-root", default=None)
    gs_cls_llm.set_defaults(func=_goldenset_classify_llm)

