"""One manual, local-only feedback operation under the existing goldenset group."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _print_receipt(receipt):
    fields = ("schema_version", "attempt_id", "run_id", "status", "published", "cutoff", "phase", "error_class",
              "run_path", "receipt_path", "receipt_persistence", "publication_state", "publication_observation_error", "gap_reasons")
    summary = {key: receipt[key] for key in fields if key in receipt}
    summary["report_path"] = str(Path(receipt["run_path"]) / "result.md") if receipt["status"] in {
        "published", "insufficient_data"} else None
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _cutoff(raw):
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("cutoff must include a timezone")
        return value.astimezone(timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("cutoff must be an aware ISO timestamp") from exc


def _analyze_feedback(args):
    from zotero_summarizer.runtime import get_context
    from zotero_summarizer.services.golden.feedback_analysis.operation import run_feedback_analysis

    try:
        receipt = run_feedback_analysis(get_context().settings, cutoff=args.cutoff or datetime.now(timezone.utc),
                                        provenance={})
    except BaseException as exc:
        receipt = getattr(exc, "feedback_receipt", None)
        if receipt is not None:
            _print_receipt(receipt)
        raise
    _print_receipt(receipt)
    return 0


def register_goldenset_feedback(subparsers):
    parser = subparsers.add_parser("analyze-feedback", help="Publish one private feedback coverage/support report; no models or startup")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--cutoff", type=_cutoff, default=None, help="Aware ISO cutoff; default current UTC")
    parser.set_defaults(func=_analyze_feedback)
