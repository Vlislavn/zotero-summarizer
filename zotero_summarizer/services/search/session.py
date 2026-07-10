"""Research-session persistence — one JSON file per session (spec §10).

A targeted search is ephemeral but resumable: the user runs a topic, screens the
candidates over minutes, deep-reads a few. We persist each ``ResearchSession`` as a
single JSON under ``settings().search_dir`` (atomic write via a temp file + rename)
so a reload or a later visit restores the exact ranked list, plan, and any reviews.
Small scale (a handful of live sessions) → a flat directory, no DB. ponytail: flat
JSON dir; move to SQLite only if sessions ever number in the thousands.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from zotero_summarizer.services._common import now_iso_z, settings
from zotero_summarizer.services.search._models import (
    QueryPlan,
    ResearchSession,
    SearchIntent,
)


def _dir() -> Path:
    d = settings().search_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(session_id: str) -> Path:
    # Guard the id → a single path segment (no traversal from a client-supplied id).
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    if not safe or safe != session_id:
        raise ValueError(f"invalid session id: {session_id!r}")
    return _dir() / f"{safe}.json"


def new_session(*, raw_query: str, intent: SearchIntent, plan: QueryPlan, questions: list[str]) -> ResearchSession:
    """Build a fresh session with a unique id (not yet persisted — call ``save``)."""
    return ResearchSession(
        id=uuid.uuid4().hex[:12],
        created_at=now_iso_z(),
        raw_query=raw_query,
        intent=intent,
        plan=plan,
        questions=list(questions),
    )


def save(session: ResearchSession) -> None:
    """Persist atomically (temp + rename) so a crash mid-write never truncates."""
    path = _path(session.id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load(session_id: str) -> ResearchSession:
    """Load one session. Raises ``FileNotFoundError`` when it does not exist (the
    route maps that to a 404 — an unknown id is a client error, not a silent None)."""
    return ResearchSession.from_dict(json.loads(_path(session_id).read_text(encoding="utf-8")))


def list_sessions() -> list[dict[str, object]]:
    """Newest-first summaries (id, created_at, raw_query, counts) for the sidebar."""
    rows: list[dict[str, object]] = []
    for path in _dir().glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append({
            "id": data["id"],
            "created_at": data["created_at"],
            "raw_query": data["raw_query"],
            "status": data.get("status", "created"),
            "candidate_count": len(data.get("candidates") or []),
        })
    rows.sort(key=lambda r: str(r["created_at"]), reverse=True)
    return rows


def delete(session_id: str) -> None:
    _path(session_id).unlink(missing_ok=True)


__all__ = ["new_session", "save", "load", "list_sessions", "delete"]
