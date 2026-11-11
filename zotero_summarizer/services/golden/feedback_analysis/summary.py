"""Plain-English view of a published feedback report, cached next to (never inside) the immutable run.

``result.json`` is large (tens of MB of per-event evidence); the app only needs the
period metrics, coverage and gaps, so they are distilled once into a small
``summaries/<run_id>.json`` under ``Settings.feedback_analysis_dir``.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from zotero_summarizer.services._common import atomic_write

from .provenance import strict_json

__all__ = ["latest_run", "list_runs", "summary_for"]

PERIODS = (("last_7d", "Last 7 days"), ("last_30d", "Last 30 days"), ("all_time", "All time"))
CHANNELS = (("interaction_log", "Decision log"), ("journal", "Label history"),
            ("current_feedback", "Approve / reject feedback"))
CLASSES = (("must_read", "Must read"), ("should_read", "Should read"), ("could_read", "Could read"),
           ("dont_read", "Don't read"), ("approve", "Approved"), ("reject", "Rejected"))
CLASS_NAMES = dict(CLASSES) | {"retracted": "Removed", "unknown": "New"}
GAP_TEXT = {
    "unproven_timestamp_origin": "Records whose time cannot be trusted are left out of the weekly numbers",
    "item_time_unproven": "Items with an unproven time are left out",
    "ambiguous_identity": "Papers that could not be matched to one identity are left out",
    "missing_classifier_runs_log": "No classifier training log was found",
    "interaction_schema1_has_no_durable_event_identity": "Older log entries cannot be told apart from repeats",
    "current_alias_mapping_not_historical_identity_availability": "Paper identities reflect today's mapping, not the past",
    "current_snapshots_not_complete_trajectory": "Current labels show where a paper is now, not how it got there",
    "unknown_revision_field": "Some label-history rows have an unknown field",
}


def _percent(value):
    return None if value is None else round(value * 100)


def _transitions(raw):
    rows = [{"from": CLASS_NAMES.get(a, a), "to": CLASS_NAMES.get(b, b), "count": n, "new": a == "unknown"}
            for a, targets in raw.items() for b, n in targets.items()]
    return sorted(rows, key=lambda r: (-r["count"], r["from"], r["to"]))


def _agreement(data):
    counts, ci = data["agreement"], data["agreement_ci"]
    if not counts["with_prediction_count"]:
        return None
    return {"matched": counts["agreement_count"], "compared": counts["with_prediction_count"],
            "low": _percent(ci["low"]), "high": _percent(ci["high"])}


def _classes(data):
    rows = []
    for name, label in CLASSES:
        item = data["class_support"].get(name)
        if item and item["unique_papers"]:
            ci = item["observed_prevalence_ci"]
            rows.append({"name": label, "papers": item["unique_papers"],
                         "low": _percent(ci["low"]), "high": _percent(ci["high"])})
    return rows


def _channel(data, label):
    if not data["raw_observations"]:
        return None
    return {"label": label, "papers": data["unique_papers"], "observations": data["raw_observations"],
            "transitions": _transitions(data["transitions"]), "agreement": _agreement(data),
            "disagreements": len(data["disagreements"]), "classes": _classes(data)}


def _period(result, key, label):
    channels = [c for name, text in CHANNELS if (c := _channel(result["periods"][key][name], text))]
    return {"id": key, "label": label, "channels": channels}


def _gaps(result):
    counts = Counter(g["reason"] for g in result["gaps"])
    return [{"text": GAP_TEXT.get(reason, reason.replace("_", " ").capitalize()), "count": n}
            for reason, n in counts.most_common()]


def build_summary(run_dir: Path, run_id: str, status: str) -> dict:
    with open(run_dir / "result.json", "rb") as stream:
        result = strict_json(stream.read())
    coverage = result["coverage"]
    return {"run_id": run_id, "status": status, "cutoff": result["cutoff"],
            "papers": coverage["unique_current_paper_groups"], "dates": coverage["date_coverage"],
            "periods": [_period(result, key, label) for key, label in PERIODS],
            "gaps": _gaps(result), "ranking_scorable": result["ranking"]["status"] != "unscorable"}


def _status(run_dir: Path) -> str:
    return strict_json((run_dir / "output-manifest.json").read_bytes())["status"]


def list_runs(settings) -> list[dict]:
    root = settings.feedback_analysis_dir / "runs"
    if not root.is_dir():
        return []
    names = sorted((n for n in os.listdir(root) if n.endswith("_feedback_run") and (root / n / "result.json").exists()),
                   reverse=True)
    return [{"run_id": n, "started": f"{n[:4]}-{n[4:6]}-{n[6:8]}T{n[9:11]}:{n[11:13]}:{n[13:15]}Z",
             "status": _status(root / n)} for n in names]


def latest_run(settings) -> dict | None:
    runs = list_runs(settings)
    return runs[0] if runs else None


def summary_for(settings, run_id: str) -> dict:
    """Cached distilled summary of one published run; builds it on first read."""
    if run_id not in {r["run_id"] for r in list_runs(settings)}:
        raise FileNotFoundError(run_id)
    cache = settings.feedback_analysis_dir / "summaries" / f"{run_id}.json"
    if cache.exists():
        return json.loads(cache.read_text("utf-8"))
    run_dir = settings.feedback_analysis_dir / "runs" / run_id
    summary = build_summary(run_dir, run_id, _status(run_dir))
    cache.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(cache, lambda tmp: tmp.write_text(json.dumps(summary, ensure_ascii=False), "utf-8"))
    return summary
