from __future__ import annotations

import argparse
import json
from pathlib import Path

from zotero_summarizer.settings import Settings
from zotero_summarizer.cli._helpers import _utc_iso_now, progress_printer
from zotero_summarizer.cli._goldenset_classify import register_goldenset_classify
from zotero_summarizer.cli._goldenset_predict import register_goldenset_predict


def _goldenset_export(args: argparse.Namespace) -> int:
    """Export the user's existing Zotero engagement signals as a golden dataset."""
    from zotero_summarizer.services.golden import goldenset

    settings = Settings.load(project_root=args.project_root)
    output_dir = Path(args.output_dir) if args.output_dir else settings.data_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "zotero-summarizer-golden.csv"
    jsonl_path = output_dir / "zotero-summarizer-golden.jsonl"

    result = goldenset.export_golden_dataset(
        zotero_data_dir=settings.zotero_data_dir,
        output_csv=csv_path,
        output_jsonl=jsonl_path,
        abstract_chars=args.abstract_chars,
        triage_db_path=settings.triage_db_path,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _goldenset_train_classifier(args: argparse.Namespace) -> int:
    """Train a classifier on the golden CSV and persist it for the daemon gate.

    Phase 1.13: writes ``~/.cache/zotero-summarizer/models/{name}.{joblib,json}``
    plus a ``classifier-runs.jsonl`` entry. The daemon reads these at startup
    when ``classifier_gate.enabled: true`` in goals.yaml.
    """
    from zotero_summarizer.services.model import classifier_persistence
    from zotero_summarizer.services import run_log
    from zotero_summarizer.services._common import read_config

    settings = Settings.load(project_root=args.project_root)
    config = read_config(settings.config_path)

    golden_csv = Path(args.input or settings.golden_csv_path)
    if not golden_csv.exists():
        raise FileNotFoundError(
            f"Golden CSV not found at {golden_csv}; run `goldenset export` first."
        )

    output_dir = Path(args.output_dir) if args.output_dir else classifier_persistence.DEFAULT_MODEL_DIR

    if args.force:
        trained = classifier_persistence.train_and_save(
            golden_csv,
            classifier_name=args.classifier,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            output_dir=output_dir,
            n_folds=args.folds,
            pca_dim=args.pca_dim,
            progress_cb=progress_printer("featurising"),
        )
    else:
        trained = classifier_persistence.load_or_train(
            golden_csv,
            classifier_name=args.classifier,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            output_dir=output_dir,
            force_retrain=False,
            n_folds=args.folds,
            pca_dim=args.pca_dim,
        )

    # Append run-log entry so the FAIR audit trail is complete.
    log_path = settings.data_dir / "classifier-runs.jsonl"
    entry = {
        "run_id": run_log.make_run_id(f"train_{trained.classifier_name}"),
        "timestamp": _utc_iso_now(),
        "git_commit": run_log.short_git_commit(settings.project_root),
        "classifier": trained.classifier_name,
        "type": "train_artifact",
        "model_path": str(output_dir / f"{trained.classifier_name}.joblib"),
        "golden_csv": str(golden_csv),
        "golden_csv_sha256_prefix": trained.golden_csv_sha256[:12],
        "thresholds": {
            "keep": round(trained.t_keep, 4),
            "must": round(trained.t_must, 4),
            "could": round(trained.t_could, 4),
        },
        "training_metadata": trained.training_metadata,
        "config": {
            "n_folds": args.folds,
            "pca_dim": args.pca_dim,
        },
    }
    run_log.append_run(log_path, entry)
    entry["run_log_path"] = str(log_path)

    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def _goldenset_eval_baseline(args: argparse.Namespace) -> int:
    """Phase 1.16 Step 0 — baseline + learning-curve measurement."""
    from zotero_summarizer.services.model import eval_baseline
    from zotero_summarizer.services._common import read_config

    settings = Settings.load(project_root=args.project_root)
    config = read_config(settings.config_path)

    golden_csv = Path(args.input or settings.golden_csv_path)
    rows = eval_baseline.load_golden_rows(golden_csv)

    timestamp = _utc_iso_now().replace(":", "").replace("-", "")[:15]

    if args.learning_curve:
        if args.learning_curve_fractions:
            fractions = tuple(
                float(s.strip()) for s in args.learning_curve_fractions.split(",")
            )
        else:
            fractions = eval_baseline.DEFAULT_LEARNING_CURVE_FRACTIONS
        report = eval_baseline.run_learning_curve(
            rows,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            classifier_name=args.classifier,
            fractions=fractions,
            n_folds=args.n_folds,
            n_bootstrap=args.n_bootstrap,
            pca_dim=args.pca_dim,
            seed=args.seed,
            progress_cb=progress_printer("featurising"),
        )
        out_path = Path(args.output or settings.data_dir / f"learning-curve-{timestamp}.json")
        payload = eval_baseline.learning_curve_to_dict(report)
    else:
        report = eval_baseline.run_baseline(
            rows,
            corpus_db_path=settings.corpus_db_path,
            goals_config=config,
            classifier_name=args.classifier,
            n_repeats=args.n_repeats,
            n_folds=args.n_folds,
            n_bootstrap=args.n_bootstrap,
            pca_dim=args.pca_dim,
            seed=args.seed,
            progress_cb=progress_printer("featurising"),
        )
        out_path = Path(args.output or settings.data_dir / f"eval-baseline-{timestamp}.json")
        payload = eval_baseline.report_to_dict(report)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["output_path"] = str(out_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _goldenset_tune(args: argparse.Namespace) -> int:
    """Sprint-3c — Optuna hyperparameter sweep over the LightGBM regressor."""
    import csv as _csv

    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.model.tune import (
        DEFAULT_TUNED_PARAMS_PATH,
        tune_lightgbm,
    )

    settings = Settings.load(project_root=args.project_root)
    input_csv = Path(args.input or settings.golden_csv_path)
    if not input_csv.exists():
        raise FileNotFoundError(f"Golden CSV not found at {input_csv}")

    with input_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(_csv.DictReader(f))

    goals_config = read_config(settings.config_path)
    output_path = Path(args.output) if args.output else DEFAULT_TUNED_PARAMS_PATH

    result = tune_lightgbm(
        rows,
        corpus_db_path=settings.corpus_db_path,
        goals_config=goals_config,
        n_trials=args.n_trials,
        n_folds=args.n_folds,
        seed=args.seed,
        output_path=output_path,
    )
    print(json.dumps({
        "best_value_spearman_median": result.best_value,
        "n_trials": result.n_trials_completed,
        "best_pca_specter_dim": result.best_pca_specter_dim,
        "best_lgbm_params": result.best_params,
        "output_path": str(output_path),
    }, indent=2))
    return 0


def _goldenset_suggest_labels(args: argparse.Namespace) -> int:
    """Print library rows whose re-labelling would help the model most.

    See :mod:`services.active_learning` for the ranking criterion.
    """
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.model.active_learning import (
        format_suggestions_markdown,
        load_rows,
        suggest_border_labels,
    )

    settings = Settings.load(project_root=args.project_root)
    input_csv = Path(args.input or settings.golden_csv_path)
    rows = load_rows(input_csv)
    goals_config = read_config(settings.config_path)
    suggestions = suggest_border_labels(
        rows,
        corpus_db_path=settings.corpus_db_path,
        goals_config=goals_config,
        classifier_name=args.classifier,
        top_k=args.top_k,
    )
    print(format_suggestions_markdown(suggestions))
    print()
    print(f"Listed {len(suggestions)} border-case library rows. Open each in")
    print("Zotero and tag (🧠 must / 👀 should / 🥱 don't), then run:")
    print("  zotero-summarizer goldenset export      # re-export from Zotero")
    print("  zotero-summarizer goldenset train-classifier --force --classifier lightgbm")
    return 0



def register_goldenset(subparsers) -> None:
    # --- goldenset export -------------------------------------------------
    goldenset = subparsers.add_parser(
        "goldenset",
        help="Export a golden-label dataset from existing Zotero engagement signals",
    )
    gs_sub = goldenset.add_subparsers(dest="goldenset_command", required=True)
    gs_export = gs_sub.add_parser(
        "export",
        help=(
            "Pull every library item with an engagement signal (🧠/👀/👎/notes/"
            "annotations/trash) and write CSV + JSONL with inferred gold labels."
        ),
    )
    gs_export.add_argument(
        "--output-dir",
        default=None,
        help="Directory for golden CSV/JSONL. Default: project root.",
    )
    gs_export.add_argument(
        "--abstract-chars",
        type=int,
        default=1000,
        help="Truncate abstract to this many characters (0 = full).",
    )
    gs_export.add_argument("--project-root", default=None)
    gs_export.set_defaults(func=_goldenset_export)

    gs_train = gs_sub.add_parser(
        "train-classifier",
        help=(
            "Train a classifier on the golden CSV and persist it to "
            "~/.cache/zotero-summarizer/models/. The daemon's hybrid gate "
            "loads this artifact at startup. Pure model output: no CV-row "
            "writeback to the golden CSV."
        ),
    )
    gs_train.add_argument(
        "--classifier",
        choices=["tabpfn", "lightgbm", "logreg"],
        default="tabpfn",
        help="Which classifier to train. Default: tabpfn (best F1).",
    )
    gs_train.add_argument(
        "--folds",
        type=int,
        default=5,
        help="K-fold count for OOF calibration + threshold tuning. Default: 5.",
    )
    gs_train.add_argument(
        "--pca-dim",
        type=int,
        default=100,
        help="PCA target for SPECTER2 embedding when classifier=tabpfn. Default: 100.",
    )
    gs_train.add_argument(
        "--input",
        default=None,
        help="Path to golden CSV. Default: <project-root>/data/zotero-summarizer-golden.csv",
    )
    gs_train.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory for model artefact. Default: ~/.cache/zotero-summarizer/models/. "
            "Two files written: <classifier>.joblib + <classifier>.json (metadata)."
        ),
    )
    gs_train.add_argument(
        "--force",
        action="store_true",
        help="Retrain even when the cached model's golden sha matches the current CSV.",
    )
    gs_train.add_argument("--project-root", default=None)
    gs_train.set_defaults(func=_goldenset_train_classifier)

    gs_eval_baseline = gs_sub.add_parser(
        "eval-baseline",
        help=(
            "Phase 1.16 Step 0 — measure the current model's true performance "
            "with proper uncertainty (5x5 repeated stratified K-fold CV + BCa "
            "bootstrap CIs). Computes Spearman, AUC, NDCG@10, MAE, Cohen's "
            "kappa. No model changes. Writes eval-baseline-<ts>.json."
        ),
    )
    gs_eval_baseline.add_argument(
        "--classifier",
        choices=["tabpfn", "lightgbm", "logreg"],
        default="lightgbm",
        help="Which classifier to evaluate. Default: lightgbm (current default).",
    )
    gs_eval_baseline.add_argument(
        "--input",
        default=None,
        help="Path to golden CSV. Default: <project-root>/data/zotero-summarizer-golden.csv",
    )
    gs_eval_baseline.add_argument(
        "--output",
        default=None,
        help="JSON output path. Default: eval-baseline-<timestamp>.json in project root.",
    )
    gs_eval_baseline.add_argument(
        "--n-repeats", type=int, default=5,
        help="Number of CV repeats (each does n-folds folds). Default: 5.",
    )
    gs_eval_baseline.add_argument(
        "--n-folds", type=int, default=5,
        help="Folds per repeat. Default: 5.",
    )
    gs_eval_baseline.add_argument(
        "--n-bootstrap", type=int, default=2000,
        help="Bootstrap iterations for BCa CIs. Default: 2000.",
    )
    gs_eval_baseline.add_argument(
        "--pca-dim", type=int, default=100,
        help="PCA target for SPECTER2 (TabPFN only). Default: 100.",
    )
    gs_eval_baseline.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility. Default: 42.",
    )
    gs_eval_baseline.add_argument(
        "--learning-curve", action="store_true",
        help=(
            "Run the learning-curve sweep instead of full repeated CV. "
            "Subsamples training set at 15/30/60/85/100 percent and reports "
            "Spearman + NDCG@10 with BCa CIs at each. Writes "
            "learning-curve-<ts>.json."
        ),
    )
    gs_eval_baseline.add_argument(
        "--learning-curve-fractions",
        default=None,
        help=(
            "Comma-separated ascending fractions in (0, 1]. Default: "
            "0.15,0.30,0.60,0.85,1.00. Only meaningful with --learning-curve."
        ),
    )
    gs_eval_baseline.add_argument("--project-root", default=None)
    gs_eval_baseline.set_defaults(func=_goldenset_eval_baseline)

    gs_tune = gs_sub.add_parser(
        "tune",
        help=(
            "Sprint-3c (May 2026) — Optuna sweep over LightGBM hyperparameters "
            "and PCA dim. Maximises median per-fold Spearman ρ over a "
            "5-fold CV. Writes the winning params to "
            "~/.cache/zotero-summarizer/optuna-best-params.json so the next "
            "`train-classifier --force` retrain picks them up automatically."
        ),
    )
    gs_tune.add_argument(
        "--input", default=None,
        help="Path to golden CSV. Default: <project-root>/data/zotero-summarizer-golden.csv",
    )
    gs_tune.add_argument(
        "--n-trials", type=int, default=50,
        help="Number of Optuna trials. Default: 50.",
    )
    gs_tune.add_argument(
        "--n-folds", type=int, default=5,
        help="CV folds per trial. Default: 5.",
    )
    gs_tune.add_argument(
        "--seed", type=int, default=42,
        help="Random seed.",
    )
    gs_tune.add_argument(
        "--output", default=None,
        help="Where to write best params. Default: ~/.cache/zotero-summarizer/optuna-best-params.json",
    )
    gs_tune.add_argument("--project-root", default=None)
    gs_tune.set_defaults(func=_goldenset_tune)

    gs_suggest = gs_sub.add_parser(
        "suggest-labels",
        help=(
            "Active-learning: print library rows where the regressor's "
            "predicted score sits closest to a priority border (4.5 / "
            "3.6 / 2.6). Re-labelling these in Zotero gives the highest "
            "marginal AUC lift per label."
        ),
    )
    gs_suggest.add_argument(
        "--input", default=None,
        help="Path to golden CSV. Default: <project-root>/data/zotero-summarizer-golden.csv",
    )
    gs_suggest.add_argument(
        "--classifier", default="lightgbm",
        choices=["lightgbm", "tabpfn", "logreg"],
        help="Model to use for the predictions. Default: lightgbm.",
    )
    gs_suggest.add_argument(
        "--top-k", type=int, default=20,
        help="How many border-case rows to print. Default: 20.",
    )
    gs_suggest.add_argument("--project-root", default=None)
    gs_suggest.set_defaults(func=_goldenset_suggest_labels)

    register_goldenset_classify(gs_sub)
    register_goldenset_predict(gs_sub)
