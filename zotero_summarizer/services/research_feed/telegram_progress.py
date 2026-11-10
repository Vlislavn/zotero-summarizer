"""Stage/counts of the running «Проверить каналы» collection: child writes, server reads.

The collection runs in a dedicated child process, so progress travels through one small
JSON file next to ``operation.json``. ``report`` is a no-op until the child calls ``init``
(CLI/tests never write it); a write failure never affects the run.
"""
from __future__ import annotations

import json

from zotero_summarizer.services._common import write_json_atomic
from zotero_summarizer.services.research_feed.telegram_selection import output_dir as _output_dir

STAGES = ("channels", "comments", "summaries")
_path = None


def init(settings) -> None:
    global _path
    _path = _output_dir(settings) / "progress.json"


def report(stage: str, done: int = 0, total: int = 0) -> None:
    if _path is None:
        return
    try:
        write_json_atomic(_path, {"stage": stage, "done": done, "total": total})
    except Exception:
        pass


def read(settings) -> dict | None:
    path = _output_dir(settings) / "progress.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if not path.is_symlink() else None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("stage") in STAGES else None


def clear(settings) -> None:
    (_output_dir(settings) / "progress.json").unlink(missing_ok=True)
