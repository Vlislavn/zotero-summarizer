"""Targeted Search endpoints (spec §13.4 #11) — the query-driven pull surface.

Thin HTTP wrapper over :mod:`services.search.pipeline` + :mod:`services.search.session`:

  POST   /api/search/screen          topic → ranked, deduped candidates (fast)
  POST   /api/search/{id}/review     kick off the light+deep review (background)
  GET    /api/search/{id}            poll one session (status + candidates)
  GET    /api/search                 list saved sessions (sidebar)
  DELETE /api/search/{id}            drop a session

SCREEN blocks (a few seconds: one intent call + the local reranker) and returns the
ranked session. REVIEW is minutes-long (PDF fetch + LLM per paper), so it runs in a
background thread that updates the session JSON; the client polls GET until the
status is ``reviewed`` (or ``error``). This mirrors deep_review's job boundary — a
failure is recorded on the session, not swallowed.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.search import session as session_store
from zotero_summarizer.services.search.pipeline import default_deps, run_review, run_screen
from zotero_summarizer.services.search.refine import agentic_enabled, run_agentic_rounds

LOGGER = logging.getLogger(__name__)
router = APIRouter()


class ScreenRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language research topic")
    questions: list[str] = Field(default_factory=list, description="Optional specific questions to answer")


class MaterializeRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1, description="Candidate to file into the library")
    collection_key: str | None = Field(default=None, description="Zotero collection key (picker); None → Inbox")


async def screen(req: ScreenRequest) -> dict[str, Any]:
    """Phase 1 — parse, federate, rank, then AUTO-start the deep review of the top few
    (user asked for the top papers deep-reviewed immediately). Returns the session
    reloaded so its status reflects ``reviewing``; the client polls GET to watch the
    reviews fill in."""
    deps = await asyncio.to_thread(default_deps)
    sess = await asyncio.to_thread(run_screen, req.query, req.questions, deps=deps)
    await asyncio.to_thread(_kickoff_review, sess.id)
    fresh = await asyncio.to_thread(session_store.load, sess.id)
    return fresh.to_dict()


def _review_worker(session_id: str) -> None:
    """Background job: run the light+deep review, recording a failure on the session
    (the documented per-job boundary — the UI surfaces it instead of a silent hang).

    When agentic search is enabled, the PRF refinement rounds run FIRST so the deep
    set is chosen from the refined pool (plan Phase E — "E gates A's trigger")."""
    try:
        deps = default_deps()
        if agentic_enabled():
            run_agentic_rounds(session_id, deps=deps)
        run_review(session_id, deps=deps)
    except Exception as exc:  # noqa: BLE001 — surface the failure on the session, then log
        LOGGER.warning("search review %s failed: %s", session_id, exc)
        session_store.update(session_id, lambda s: setattr(s, "status", "error"))
        raise


def _kickoff_review(session_id: str) -> bool:
    """Single-flight the review worker: atomically claim ``screened`` → ``reviewing``
    and spawn the thread. Returns False if another worker already owns it (or the
    session is terminal), so auto-start + a manual click can't stack workers."""
    if not session_store.claim(session_id, expect="screened", to="reviewing"):
        return False
    threading.Thread(target=_review_worker, args=(session_id,), daemon=True).start()
    return True


async def start_review(session_id: str) -> dict[str, Any]:
    """Phase 2 — kick off the review in the background; poll GET for completion. Now
    usually a no-op since ``screen`` auto-starts it, but kept for an explicit re-trigger."""
    started = await asyncio.to_thread(_kickoff_review, session_id)
    if not started:
        sess = await asyncio.to_thread(session_store.load, session_id)
        return {"accepted": False, "status": sess.status}
    return {"accepted": True, "status": "reviewing"}


async def get_session(session_id: str) -> dict[str, Any]:
    try:
        sess = await asyncio.to_thread(session_store.load, session_id)
    except FileNotFoundError as exc:
        raise APIError(
            error="not_found",
            message=f"no such search session: {session_id}",
            status_code=404,
        ) from exc
    return sess.to_dict()


async def materialize(session_id: str, req: MaterializeRequest) -> dict[str, Any]:
    """Add one candidate to the library, into a chosen Zotero collection — an explicit
    user action (this domain writes to Zotero only here)."""
    from zotero_summarizer.services.search.materialize import materialize_candidate

    try:
        return await asyncio.to_thread(
            materialize_candidate, session_id, req.candidate_id, req.collection_key
        )
    except FileNotFoundError as exc:
        raise APIError(
            error="not_found", message=f"no such search session: {session_id}", status_code=404
        ) from exc


async def list_sessions() -> dict[str, Any]:
    return {"sessions": await asyncio.to_thread(session_store.list_sessions)}


async def delete_session(session_id: str) -> dict[str, Any]:
    await asyncio.to_thread(session_store.delete, session_id)
    return {"deleted": session_id}


router.add_api_route("/api/search/screen", screen, methods=["POST"])
router.add_api_route("/api/search", list_sessions, methods=["GET"])
router.add_api_route("/api/search/{session_id}", get_session, methods=["GET"])
router.add_api_route("/api/search/{session_id}", delete_session, methods=["DELETE"])
router.add_api_route("/api/search/{session_id}/review", start_review, methods=["POST"])
router.add_api_route("/api/search/{session_id}/materialize", materialize, methods=["POST"])
