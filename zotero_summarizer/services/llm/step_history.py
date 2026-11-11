"""Per-step speed and last success from data the app already keeps (no new storage).

deep_review: ``timings`` on each cached review (stage calls/seconds/tokens + wall
             time + the model that ran it; ``median_tok_per_s`` = completion tokens per
             call-second over the recent reviews); reviews written before #132 fall back to
             ``[dr item=…] review done in Ns`` log lines, which are untagged by model.
backlog:     completed rows of ``triage_jobs`` (job wall time / papers).
feed:        no per-model timing is recorded (a feed tick mixes ranking and RSS work).
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from statistics import median

from zotero_summarizer.services._common import settings
from zotero_summarizer.storage import repositories as triage_db

_REVIEW_DONE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| INFO .*\[dr item=\S+\] review done in ([\d.]+)s")
_TAIL_BYTES = 4_000_000
_RECENT = 20


def _review_runs_from_log(log_path: Path) -> list[tuple[str, float, str]]:
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - _TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    runs = []
    for line in text.splitlines():
        found = _REVIEW_DONE.match(line)
        if found:  # log stamps are local wall-clock time
            runs.append((datetime.strptime(found[1], "%Y-%m-%d %H:%M:%S").astimezone().isoformat(),
                         float(found[2]), ""))
    return runs


def _review_runs() -> list[tuple[str, float, str]]:
    """``(finished_at, wall seconds, model)`` from the review cache, model-tagged."""
    from zotero_summarizer.services.library._review_cache import _read_all

    try:
        reviews = _read_all(readonly=True)
    except (OSError, ValueError):
        reviews = {}
    runs = []
    for entry in reviews.values():
        timings = entry.get("timings") if isinstance(entry, dict) else None
        if not isinstance(timings, dict) or not entry.get("reviewed_at"):
            continue
        seconds = timings.get("total_seconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
            runs.append((str(entry["reviewed_at"]), float(seconds), str(timings.get("model") or "")))
    runs.sort()
    return runs or _review_runs_from_log(settings().app_log_file)


def _review_tok_per_s() -> float | None:
    """Median completion tokens per call-second over the recent cached reviews (0.0 → None)."""
    from zotero_summarizer.services.library._review_cache import _read_all

    try:
        reviews = sorted(_read_all(readonly=True).values(), key=lambda e: str(e.get("reviewed_at") or ""))
    except (OSError, ValueError):
        return None
    rates = []
    for entry in reviews[-_RECENT:]:
        stages = ((entry.get("timings") or {}).get("stages") or {}) if isinstance(entry, dict) else {}
        tokens = sum(int(r.get("completion_tokens") or 0) for r in stages.values())
        seconds = sum(float(r.get("seconds") or 0) for r in stages.values())
        if tokens and seconds > 0:
            rates.append(tokens / seconds)
    return round(median(rates), 1) if rates else None


def _section_runs() -> list[tuple[str, float, str]]:
    """``(at, seconds, model)`` of the on-open Paper-map one-liner jobs (sidecar timing)."""
    runs = []
    for path in (settings().paper_render_dir / "_section_summaries").glob("*.json"):
        try:
            timing = json.loads(path.read_text(encoding="utf-8")).get("timing") or {}
            runs.append((str(timing["at"]), float(timing["seconds"]), str(timing.get("model") or "")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(runs)


def _backlog_runs() -> list[tuple[str, float, str]]:
    runs = []
    for job in reversed(triage_db.list_triage_jobs(limit=_RECENT, statuses=["completed"])):
        try:
            started, finished = (datetime.fromisoformat(job[key]) for key in ("started_at", "updated_at"))
        except (KeyError, ValueError):
            continue
        if job.get("completed"):
            runs.append((job["updated_at"], (finished - started).total_seconds() / job["completed"], ""))
    return runs


def _summary(runs: list[tuple[str, float, str]], unit: str) -> dict:
    recent = runs[-_RECENT:]
    return {"runs": len(recent), "unit": unit,
            "median_seconds": round(median(s for _, s, _ in recent), 1) if recent else None,
            "last_success_at": recent[-1][0] if recent else None,
            "last_model": recent[-1][2] if recent and recent[-1][2] else None}


def step_history() -> dict:
    """Median time and last success per step; ``runs: 0`` means nothing recorded."""
    return {"steps": {"feed": _summary([], "per item"),
                      "backlog": _summary(_backlog_runs(), "per paper"),
                      "deep_review": {**_summary(_review_runs(), "per review"),
                                      "median_tok_per_s": _review_tok_per_s()},
                      "section_summaries": _summary(_section_runs(), "per paper opened")}}
