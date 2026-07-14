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
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import now_iso_z, settings
from zotero_summarizer.services.search._models import (
    Candidate,
    QueryPlan,
    ResearchSession,
    SearchIntent,
)

# Per-session-id locks so the background review worker (whole-session saves) and a
# concurrent "Add to library" (stamps one candidate's materialized key) never
# lost-update each other. In-process only — sessions are served by one app process.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(session_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(session_id)
        if lk is None:
            lk = threading.Lock()
            _locks[session_id] = lk
        return lk


def _dir() -> Path:
    d = settings().search_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(session_id: str) -> Path:
    # Guard the id → a single path segment (no traversal from a client-supplied id).
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    if not safe or safe != session_id:
        raise APIError(error="invalid_session", message=f"invalid session id: {session_id!r}", status_code=400)
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


def save_merge(session: ResearchSession) -> ResearchSession:
    """Persist under the session lock, preserving any ``materialized_zotero_key`` that
    a concurrent "Add to library" stamped since this in-memory copy was loaded. The
    key is add-only/monotonic, so the review worker's whole-session saves must never
    drop it. Everything else (order, quality, reviews) is owned by the worker."""
    with _lock_for(session.id):
        persisted = load(session.id)
        keyed = {
            c.candidate_id: c.materialized_zotero_key
            for c in persisted.candidates
            if c.materialized_zotero_key
        }
        for cand in session.candidates:
            if not cand.materialized_zotero_key and keyed.get(cand.candidate_id):
                cand.materialized_zotero_key = keyed[cand.candidate_id]
        save(session)
    return session


def update(session_id: str, mutate: Callable[[ResearchSession], None]) -> ResearchSession:
    """Load → ``mutate`` in place → save, all under the session lock (read-modify-write
    safe against the concurrent review worker). Used by the "Add to library" path to
    stamp one candidate's materialized key without clobbering in-flight reviews."""
    with _lock_for(session_id):
        sess = load(session_id)
        mutate(sess)
        save(sess)
        return sess


def materialize_once(
    session_id: str,
    candidate_id: str,
    do_write: Callable[[Candidate, ResearchSession], str],
) -> tuple[bool, str] | None:
    """File one candidate into Zotero exactly once, under the session lock.

    Closes the check-then-write race: two concurrent "Add to library" clicks on the
    same candidate must not both write to Zotero. Returns ``None`` if the candidate id
    is unknown, ``(False, key)`` if it was already materialized (no write), or
    ``(True, key)`` after calling ``do_write(cand, session)`` to create the Zotero item
    and stamping the returned key. The lock is held across ``do_write`` — materialize is
    a rare explicit action, so serializing it per session is simpler than a reservation
    marker. ponytail: whole-session lock; only split if Add ever gets hot.
    Do NOT call the locked helpers (``update``/``save_merge``/``claim``) from
    ``do_write`` — the lock is a plain (non-reentrant) ``threading.Lock``."""
    with _lock_for(session_id):
        sess = load(session_id)
        cand = next((c for c in sess.candidates if c.candidate_id == candidate_id), None)
        if cand is None:
            return None
        if cand.materialized_zotero_key:
            return (False, cand.materialized_zotero_key)
        key = do_write(cand, sess)
        cand.materialized_zotero_key = key
        save(sess)
        return (True, key)


def claim(session_id: str, *, expect: str, to: str) -> bool:
    """Atomic status compare-and-set under the session lock: flip ``expect`` → ``to``
    and return True, or return False if the current status differs (already claimed /
    terminal). Single-flights the review worker so auto-start + a manual click, or two
    screens, can't stack workers."""
    with _lock_for(session_id):
        sess = load(session_id)
        if sess.status != expect:
            return False
        sess.status = to
        save(sess)
    return True


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


__all__ = ["new_session", "save", "save_merge", "update", "materialize_once", "claim", "load", "list_sessions", "delete"]
