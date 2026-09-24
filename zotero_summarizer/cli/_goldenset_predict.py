from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from zotero_summarizer.settings import Settings
from zotero_summarizer.cli._helpers import _resolve_feed_ids, progress_printer


def _goldenset_predict_feed(args: argparse.Namespace) -> int:
    """Pull unread items from a feed, predict 4-class priority, save for review."""
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services._common import load_golden_rows, read_config
    from zotero_summarizer.services.golden.hybrid_gt import apply_hybrid
    from zotero_summarizer.services.model import classifier

    settings = Settings.load(project_root=args.project_root)
    feed_filter: list[int] | None = None
    if args.feeds:
        feed_filter = _resolve_feed_ids(args.feeds, settings)

    input_csv = Path(args.golden_csv or settings.golden_csv_path)
    training_rows = apply_hybrid(load_golden_rows(input_csv), settings.triage_db_path)

    # Pull unread items from the requested feed.
    reader = ZoteroReader(settings.zotero_data_dir)
    feed_library_ids = feed_filter or [int(f["library_id"]) for f in reader.get_feed_groups()]

    new_items: list[dict] = []
    for lib_id in feed_library_ids:
        items = reader.get_feed_items(
            feed_library_id=lib_id,
            unread_only=True,
            order="newest_first",
            limit=args.limit,
        )
        for it in items:
            new_items.append(it)
            if len(new_items) >= args.limit:
                break
        if len(new_items) >= args.limit:
            break
    new_items = new_items[: args.limit]

    if not new_items:
        print(json.dumps({"error": "no unread items found", "feeds": feed_library_ids}, indent=2))
        return 1

    goals_config = read_config(settings.config_path)

    predictions, thresholds = classifier.predict_new_items(
        training_rows=training_rows,
        new_items=new_items,
        corpus_db_path=settings.corpus_db_path,
        classifier_name=args.classifier,
        pca_dim=args.pca_dim,
        n_folds=args.folds,
        goals_config=goals_config,
        progress_cb=progress_printer("embedding training set:"),
    )

    output_csv = Path(args.output or settings.data_dir / f"feed-predictions-{args.feeds or 'all'}.csv")
    classifier.write_feed_predictions_csv(predictions, output_csv)

    md = classifier.format_feed_predictions_markdown(predictions, thresholds)
    print(md)
    print()
    print(f"saved annotation CSV: {output_csv}")
    return 0


def _goldenset_analyze_notes(args: argparse.Namespace) -> int:
    """Classify user-written Zotero notes via the LLM and write a review CSV.

    Uses goals.yaml's ``llm`` block by default; ``--api-base``, ``--api-key-env``
    and ``--model`` let the caller route this single command through a
    different OpenAI-compatible endpoint (e.g. OpenRouter free models) without
    touching ``goals.yaml``.
    """
    import os

    from zotero_summarizer.services.zotero import note_analyzer
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
    # OpenRouter (and other non-Ollama backends) reject the OnPrem-specific
    # `chat_template_kwargs` extra_body, so only forward it when the caller
    # didn't override the api_base.
    extra_body = config.llm.extra_body if not args.api_base else None
    llm = build_llm(api_base, model_name, api_key, max_tokens=4096, extra_body=extra_body)

    notes = note_analyzer.pull_candidate_notes(
        settings.zotero_data_dir,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        limit=args.limit,
    )
    if not notes:
        print(json.dumps({"error": "no candidate notes found"}, indent=2))
        return 1

    print(
        f"classifying {len(notes)} candidate notes via {model_name!r} at "
        f"{api_base} …",
        flush=True,
    )

    analyses = note_analyzer.classify_notes(notes, llm, progress_cb=progress_printer("classified"))
    output_csv = Path(args.output or settings.data_dir / "note-analyses.csv")
    note_analyzer.write_analyses_csv(analyses, output_csv)

    summary = {
        "model": model_name,
        "api_base": api_base,
        "candidates": len(notes),
        "classified": sum(1 for a in analyses if a.llm_priority),
        "skipped": sum(1 for a in analyses if not a.llm_priority),
        "by_priority": note_analyzer.distribution(analyses),
        "output_csv": str(output_csv),
    }
    print()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _goldenset_compare(args: argparse.Namespace) -> int:
    """Print every classifier's latest run side-by-side from the run log."""
    from zotero_summarizer.services import run_log

    settings = Settings.load(project_root=args.project_root)
    log_path = Path(args.log or settings.data_dir / "classifier-runs.jsonl")
    runs = run_log.load_runs(log_path)
    if not runs:
        print(json.dumps({"error": f"no runs found in {log_path}"}, indent=2))
        return 1

    latest = run_log.latest_per_classifier(runs)
    split = args.split

    header = (
        f"{'classifier':<22} {'auc':>6} {'binary_F1':>10} {'binary_P':>9} "
        f"{'binary_R':>9} {'must_F1':>8} {'must_P':>7} {'must_R':>7} "
        f"{'n':>4} {'run_id':<30}"
    )
    print(header)
    print("-" * len(header))

    def _f(v: Any, w: int = 6, places: int = 3) -> str:
        if v is None or v == "":
            return "  —".rjust(w)
        try:
            return f"{float(v):.{places}f}".rjust(w)
        except (TypeError, ValueError):
            return str(v).rjust(w)

    rows_out = []
    for name, run in latest.items():
        block = run.get(split) or {}
        m = block.get("metrics_vs_gold") or {}
        b = m.get("binary") or {}
        mr = (m.get("per_class") or {}).get("must_read", {}) or {}
        rows_out.append(
            f"{name[:22]:<22} {_f(block.get('auc'), 6, 3)} "
            f"{_f(b.get('f1'), 10, 3)} {_f(b.get('precision'), 9, 3)} "
            f"{_f(b.get('recall'), 9, 3)} "
            f"{_f(mr.get('f1'), 8, 3)} {_f(mr.get('precision'), 7, 3)} "
            f"{_f(mr.get('recall'), 7, 3)} "
            f"{m.get('total', 0):>4} {run.get('run_id', '')[:30]:<30}"
        )
    # Sort by binary F1 descending so the best run is at the top.
    def _f1(line: str) -> float:
        try:
            return -float(line.split()[2])
        except (IndexError, ValueError):
            return 0.0
    rows_out.sort(key=_f1)
    for line in rows_out:
        print(line)
    return 0



def register_goldenset_predict(gs_sub) -> None:
    gs_pred = gs_sub.add_parser(
        "predict-feed",
        help=(
            "Train the classifier on the golden CSV and predict 4-class priority "
            "for unread items from a specified feed. Writes an annotation CSV "
            "with an empty `your_label` column for human review."
        ),
    )
    gs_pred.add_argument(
        "--feeds",
        default=None,
        help="Feed name substring or numeric ID. Comma-separated. Default: all feeds.",
    )
    gs_pred.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max number of unread items to predict on. Default: 10.",
    )
    gs_pred.add_argument(
        "--classifier",
        choices=["logreg", "tabpfn", "lightgbm"],
        default="tabpfn",
        help="Classifier to train on the golden CSV. Default: tabpfn (best AUC).",
    )
    gs_pred.add_argument(
        "--folds",
        type=int,
        default=5,
        help="Paper-group fold count for the OOF diagnostic (at least 2). Default: 5.",
    )
    gs_pred.add_argument(
        "--pca-dim",
        type=int,
        default=100,
        help="PCA target dim for SPECTER2 embedding when --classifier=tabpfn. Default: 100.",
    )
    gs_pred.add_argument(
        "--golden-csv",
        default=None,
        help="Path to training CSV. Default: <project-root>/data/zotero-summarizer-golden.csv",
    )
    gs_pred.add_argument(
        "--output",
        default=None,
        help="Annotation CSV destination. Default: <project-root>/feed-predictions-<feed>.csv",
    )
    gs_pred.add_argument("--project-root", default=None)
    gs_pred.set_defaults(func=_goldenset_predict_feed)

    gs_notes = gs_sub.add_parser(
        "analyze-notes",
        help=(
            "Pull user-written Zotero notes, send each to the LLM for a "
            "must/should/could/dont verdict, write a review CSV with empty "
            "`your_label` defaulted to the LLM's choice. LLM-generated and "
            "compilation notes get SKIP."
        ),
    )
    gs_notes.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N candidate notes. Default: all surviving the filter.",
    )
    gs_notes.add_argument(
        "--min-chars",
        type=int,
        default=100,
        help="Skip notes shorter than this. Default: 100.",
    )
    gs_notes.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Skip notes longer than this (compilations / imported essays). Default: 4000.",
    )
    gs_notes.add_argument(
        "--model",
        default=None,
        help=(
            "Override LLM model for this run. Default: goals.yaml refine_model. "
            "Example for OpenRouter Nemotron Super: "
            "'nvidia/llama-3.3-nemotron-super-49b-v1.5:free'."
        ),
    )
    gs_notes.add_argument(
        "--api-base",
        default=None,
        help=(
            "Override OpenAI-compatible endpoint for this run. Default: "
            "goals.yaml llm.api_base. Example: 'https://openrouter.ai/api/v1'."
        ),
    )
    gs_notes.add_argument(
        "--api-key-env",
        default=None,
        help=(
            "Env var name holding the API key. Default: goals.yaml "
            "llm.api_key_env. Use 'OPENROUTER_API_KEY' to point at OpenRouter."
        ),
    )
    gs_notes.add_argument(
        "--output",
        default=None,
        help="Review CSV destination. Default: <project-root>/data/note-analyses.csv",
    )
    gs_notes.add_argument("--project-root", default=None)
    gs_notes.set_defaults(func=_goldenset_analyze_notes)

    gs_compare = gs_sub.add_parser(
        "compare",
        help=(
            "Print a side-by-side table of every classifier's latest run from "
            "classifier-runs.jsonl. No re-training — pure replay from disk."
        ),
    )
    gs_compare.add_argument(
        "--log",
        default=None,
        help="Path to run log. Default: <project-root>/classifier-runs.jsonl",
    )
    gs_compare.add_argument(
        "--split",
        choices=["cv", "holdout"],
        default="cv",
        help="Which metric block to display. Default: cv.",
    )
    gs_compare.add_argument("--project-root", default=None)
    gs_compare.set_defaults(func=_goldenset_compare)
