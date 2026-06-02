"""Append-only run log for classifier experiments.

Every ``goldenset classify`` and ``classify-llm`` invocation appends one JSONL
line to ``classifier-runs.jsonl`` in the project root. Each line is a
self-contained snapshot of:

* **What was run** — classifier name, model, CLI args.
* **What it was run on** — golden CSV path + SHA-256 prefix.
* **What the code looked like** — best-effort short git commit.
* **When** — UTC ISO timestamp.
* **How it performed** — full metrics block (CV, held-out, thresholds).

Append-only means: re-running the same classifier never overwrites the prior
run's record. Findable (unique ``run_id``), Accessible (plain JSONL),
Interoperable (UTF-8 text), Reusable (full config snapshot for replay).
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


def make_run_id(classifier_name: str, *, now: datetime | None = None) -> str:
    """Compact unique identifier: ``YYYYMMDD_HHMMSS_<classifier>``."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{classifier_name}"


def short_git_commit(repo_dir: Path | None = None) -> str:
    """Best-effort short HEAD commit. Empty string outside a repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_dir) if repo_dir else None,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        return out
    except Exception:
        return ""


def file_sha256(path: Path, *, prefix_len: int = 12) -> str:
    """Hash of the file's bytes — proves which golden CSV version was used."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:prefix_len]


def append_run(log_path: Path, entry: dict[str, Any]) -> None:
    """Append a single run record (one JSON object per line)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=False) + "\n")


def load_runs(log_path: Path) -> list[dict[str, Any]]:
    """Parse the JSONL log. Skips empty / malformed lines with a warning."""
    if not log_path.exists():
        return []
    out: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                LOGGER.warning("run_log: skipping malformed line %d (%s)", line_no, exc)
    return out


def latest_per_classifier(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pick the most recent run for each classifier name (by run_id sort)."""
    out: dict[str, dict[str, Any]] = {}
    for r in sorted(runs, key=lambda x: x.get("run_id", "")):
        name = r.get("classifier", "unknown")
        out[name] = r
    return out
