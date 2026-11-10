"""Local record of what Today shows and what the owner does with it.

Owner-approved (2026-11-07), local only: each assembled slate, the browser's card
events and note saves are appended to ``Settings.today_exposure_log_dir /
<local day>.jsonl`` (``data/today-exposure/``; ``run_log`` NDJSON, envelope
``ts/event/schema/git_commit/ranker``), so the per-request exploration scan reads one day. Add / Trash / verdicts stay in the interaction log and join by
``item_key`` + ``ts``; this file is kept apart because the feedback-analysis
reader treats every interaction-log line as a decision. Ids, scores, enums and
durations only (shelf rows of unreviewed papers carry ``role="unreviewed"``) — never titles, abstracts or note text. ``ZS_TODAY_EXPOSURE_LOG=0``
writes nothing (the slate id is still issued). Event shape is validated by the
route's request model. Write failures are logged and dropped: logging never breaks
Today or a note save. Malformed lines are skipped on read.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.settings import today_exploration_slots, today_exposure_log_enabled, today_ranker

SCHEMA_VERSION = 2  # v2: envelope ``ranker`` (``settings.today_ranker``)
LOGGER = logging.getLogger(__name__)
_git_commit = functools.cache(run_log.short_git_commit)


def _day_path(day: str) -> Path:
    return settings().today_exposure_log_dir / f"{day}.jsonl"


def _emit(entries: list[tuple[str, dict[str, Any]]]) -> None:
    """Append ``(event, fields)`` pairs to today's file; failures are logged, never raised."""
    try:
        path = _day_path(datetime.now().astimezone().date().isoformat())
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        lines = [json.dumps({"ts": now, "event": event, "schema": SCHEMA_VERSION,
                             "git_commit": _git_commit(), "ranker": today_ranker(), **fields}, ensure_ascii=False)
                 for event, fields in entries]
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning("today exposure log write dropped: %s", exc)


def _member(position: int, paper: dict[str, Any]) -> dict[str, Any]:
    recommendation = paper.get("recommendation") or {}
    exploration = paper["role"] == "exploration"
    return {
        "pos": position, "item_id": paper["item_id"], "item_key": paper["item_key"],
        "stable_feed_key": paper["stable_feed_key"], "role": paper["role"],
        "exploration": exploration, "pool": recommendation.get("reason") if exploration else None,
        "priority": recommendation.get("priority"), "reason": recommendation.get("reason"),
        "composite_score": paper["composite_score"], "surprise_score": paper["surprise_score"],
        "corpus_affinity": paper["corpus_affinity"], "goal_sim": paper["goal_sim"],
    }


def record_slate(payload: dict[str, Any], *, k: int) -> str:
    """Log the assembled ``/api/daily`` payload; return its slate id."""
    slate_id = str(uuid.uuid4())
    if today_exposure_log_enabled():
        try:
            gate = state().classifier_gate
            fields = {
                "slate_id": slate_id, "day": datetime.now().astimezone().date().isoformat(),
                "policy": payload["selection_policy"],
                "gate_sha": gate.model_sha256 if gate is not None else "",
                "goals_hash": hashlib.sha256(settings().config_path.read_bytes()).hexdigest()[:12],
                "k": k, "pool_size": payload["pool_size"], "exploration_limit": today_exploration_slots(),
                "members": [_member(i, paper) for i, paper in enumerate([*payload["papers"], *payload.get("unreviewed", [])])],
            }
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.warning("today slate log skipped: %s", exc)
        else:
            _emit([("today_slate", fields)])
    return slate_id


def record_events(slate_id: str, events: list[dict[str, Any]]) -> int:
    """Append browser events; returns the number written (0 when logging is off)."""
    if not today_exposure_log_enabled():
        return 0
    _emit([("today_event", {"slate_id": slate_id, **event}) for event in events])
    return len(events)


def record_note_saved(item_key: str, note: str) -> None:
    """Log that a review note was saved (length only, never the text)."""
    if today_exposure_log_enabled():
        _emit([("note_saved", {"item_key": item_key, "chars": len(note)})])


def explored_today(day: str) -> dict[int, str]:
    """``{item_id: pool}`` of exploration cards already shown on local ``day``."""
    path = _day_path(day)
    found: dict[int, str] = {}
    try:
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                if '"today_slate"' not in line:
                    continue
                try:
                    event = json.loads(line)
                    if event["event"] == "today_slate" and event["day"] == day:
                        found.update({m["item_id"]: m["pool"] for m in event["members"] if m["exploration"]})
                except (ValueError, KeyError, TypeError):
                    LOGGER.warning("today exposure: skipping malformed line in %s", path.name)
    except FileNotFoundError:
        pass
    except OSError as exc:
        LOGGER.warning("today exposure: unreadable %s: %s", path.name, exc)
    return found
