"""Past Telegram digests and the owner's read state.

Every finished run is archived as one append-only file ``history/<UTC stamp>.json`` next to
``telegram-today.json``: the today payload (summary points + cited ids only; the comments live in
``research_feed_conversations`` and load through ``GET /telegram/thread/{source_id}``). ``done.json``
maps ``source_id`` -> ISO time for discussions the owner marked done; it is global, so a post that
shows up in two digests is done in both.
"""
from __future__ import annotations

import json
import re
import threading

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import now_iso_z, write_json_atomic
from zotero_summarizer.services.research_feed import telegram_reasons
from zotero_summarizer.services.research_feed.telegram_selection import output_dir

_RUN_ID = re.compile(r"^\d{8}T\d{6}Z(?:-\d{1,2})?$")
_DONE = "done.json"
_LOCK = threading.Lock()  # mark_done is read-modify-write of one file


def _dir(settings):
    return output_dir(settings) / "history"


def _run_id(generated_at: str) -> str:
    return re.sub(r"[^0-9TZ]", "", generated_at.split(".")[0].rstrip("Z")) + "Z"


def archive(settings, payload: dict) -> str | None:
    """Write the run's snapshot once (never overwrite); returns the run id, None for an empty run.

    The same ``generated_at`` is already archived (idempotent); a different run in the same second gets ``-2``, ``-3``...
    """
    if not payload.get("posts") or not payload.get("generated_at"):
        return None
    base = _run_id(payload["generated_at"])
    for number in range(1, 100):
        run_id = base if number == 1 else f"{base}-{number}"
        path = _dir(settings) / f"{run_id}.json"
        if not path.exists():
            write_json_atomic(path, payload)
            return run_id
        if (_read(path) or {}).get("generated_at") == payload["generated_at"]:
            return run_id
    return None


def _done(settings) -> dict[str, str]:
    path = _dir(settings) / _DONE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def overlay(settings, posts: list[dict]) -> None:
    """Add the owner's state (done, negative reasons, liked chips, note) to each post in place."""
    done, feedback = _done(settings), telegram_reasons.read_all(settings)
    for post in posts:
        source_id = post.get("source_id")
        post.update(feedback.get(source_id) or {"reasons": None, "liked": [], "note": ""})
        post["done"] = source_id in done


def mark_done(settings, source_ids: list[str], done: bool) -> dict:
    for source_id in source_ids:
        telegram_reasons.parse(source_id)  # 404 on a malformed id
    with _LOCK:
        marks = _done(settings)
        for source_id in source_ids:
            if done:
                marks.setdefault(source_id, now_iso_z())
            else:
                marks.pop(source_id, None)
        write_json_atomic(_dir(settings) / _DONE, marks)
    return {"done": sorted(marks)}


def feedback_export(settings) -> list[dict]:
    """Every post with feedback (see ``telegram_reasons.export``) plus when the owner marked it done."""
    done = _done(settings)
    return [{**row, "done_at": done.get(row["source_id"])} for row in telegram_reasons.export(settings)]


def _read(path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("posts"), list) else None


def read_run(settings, run_id: str) -> dict:
    path = _dir(settings) / f"{run_id}.json"
    value = _read(path) if _RUN_ID.match(run_id) and path.is_file() and not path.is_symlink() else None
    if value is None:
        raise APIError("telegram_history_unknown", "Unknown past digest", status_code=404)
    overlay(settings, value["posts"])
    return {**value, "run_id": run_id}


def _lead(value: dict) -> str:
    """The first trend label, else the busiest channel: one line to tell digests apart."""
    topics = value.get("trends", {}).get("topics", [])
    if topics and topics[0].get("label"):
        return str(topics[0]["label"])
    channels = [post.get("channel") for post in value["posts"] if post.get("channel")]
    return f"@{max(set(channels), key=channels.count)}" if channels else ""


def list_runs(settings) -> list[dict]:
    """Newest first: run id, time, discussions, how many are not done yet, a one-line lead."""
    done, runs = _done(settings), []
    for path in sorted(_dir(settings).glob("*.json"), key=lambda p: p.stem, reverse=True):
        value = _read(path) if _RUN_ID.match(path.stem) and not path.is_symlink() else None
        if value is not None:
            ids = [post.get("source_id") for post in value["posts"]]
            runs.append({"run_id": path.stem, "generated_at": value.get("generated_at"), "discussions": len(ids),
                         "unread": sum(1 for source_id in ids if source_id not in done),
                         "trends": len(value.get("trends", {}).get("topics", [])), "lead": _lead(value)})
    return runs
