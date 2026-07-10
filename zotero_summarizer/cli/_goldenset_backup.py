"""CLI for the gate snapshot/rollback safety (Part 1).

Splits the ``model-history`` + ``restore-model`` subcommands out of ``_goldenset.py``
to respect the ≤500 LOC rule. Mirrors the ``register_goldenset_*`` pattern
(``_goldenset_classify.py`` / ``_goldenset_migrate.py`` …): handler + subparser
registration in one focused module.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def _goldenset_model_history(args: argparse.Namespace) -> int:
    """List versioned snapshots of the trained gate (rollback targets)."""
    from zotero_summarizer.services.model.classifier_artifact import DEFAULT_MODEL_DIR
    from zotero_summarizer.services.model.classifier_backup import list_snapshots

    model_dir = Path(args.output_dir) if args.output_dir else DEFAULT_MODEL_DIR
    snaps = list_snapshots(model_dir, args.classifier)
    if not snaps:
        print(f"No snapshots for {args.classifier} in {model_dir}/history/")
        return 0
    for s in snaps:
        print(
            f"{s['name']}  trained={s['trained_at']}  oof={s['oof_spearman']}  "
            f"temporal={s['temporal_spearman']}  n_train={s['n_train']}  git={s['git_commit']}"
        )
    print(f"\nRestore with: goldenset restore-model --classifier {args.classifier} "
          f"--snapshot <name>")
    return 0


def _goldenset_restore_model(args: argparse.Namespace) -> int:
    """Restore a snapshot as the live model (backs up the current live model first)."""
    from zotero_summarizer.services.model.classifier_artifact import DEFAULT_MODEL_DIR
    from zotero_summarizer.services.model.classifier_backup import restore_snapshot

    model_dir = Path(args.output_dir) if args.output_dir else DEFAULT_MODEL_DIR
    joblib_path = restore_snapshot(model_dir, args.classifier, args.snapshot)
    print(f"Restored {args.snapshot} -> {joblib_path}")
    print("(the prior live model was snapshotted first — restore is reversible)")
    return 0


def register_goldenset_backup(gs_sub) -> None:
    gs_history = gs_sub.add_parser(
        "model-history",
        help=(
            "List versioned snapshots of the trained gate (rollback targets). "
            "Every `train-classifier --force` snapshots the prior model first."
        ),
    )
    gs_history.add_argument(
        "--classifier", default="lightgbm",
        choices=["lightgbm", "tabpfn", "logreg"],
        help="Which classifier's history to list. Default: lightgbm.",
    )
    gs_history.add_argument(
        "--output-dir", default=None,
        help="Model dir. Default: ~/.cache/zotero-summarizer/models/.",
    )
    gs_history.set_defaults(func=_goldenset_model_history)

    gs_restore = gs_sub.add_parser(
        "restore-model",
        help=(
            "Restore a snapshot as the live model. The current live model is "
            "snapshotted first, so the restore is reversible. Restart the daemon "
            "for the swapped gate to take effect."
        ),
    )
    gs_restore.add_argument(
        "--classifier", default="lightgbm",
        choices=["lightgbm", "tabpfn", "logreg"],
        help="Which classifier to restore. Default: lightgbm.",
    )
    gs_restore.add_argument(
        "--snapshot", required=True,
        help="Snapshot name from `model-history`.",
    )
    gs_restore.add_argument(
        "--output-dir", default=None,
        help="Model dir. Default: ~/.cache/zotero-summarizer/models/.",
    )
    gs_restore.set_defaults(func=_goldenset_restore_model)
