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

LOGGER = logging.getLogger(__name__)
router = APIRouter()


class ScreenRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language research topic")
    questions: list[str] = Field(default_factory=list, description="Optional specific questions to answer")


async def screen(req: ScreenRequest) -> dict[str, Any]:
    """Phase 1 — parse, federate, rank. Returns the saved session with candidates."""
    deps = await asyncio.to_thread(default_deps)
    sess = await asyncio.to_thread(run_screen, req.query, req.questions, deps=deps)
    return sess.to_dict()


def _review_worker(session_id: str) -> None:
    """Background job: run the light+deep review, recording a failure on the session
    (the documented per-job boundary — the UI surfaces it instead of a silent hang)."""
    try:
        run_review(session_id, deps=default_deps())
    except Exception as exc:  # noqa: BLE001 — surface the failure on the session, then log
        LOGGER.warning("search review %s failed: %s", session_id, exc)
        sess = session_store.load(session_id)
        sess.status = "error"
        session_store.save(sess)
        raise


async def start_review(session_id: str) -> dict[str, Any]:
    """Phase 2 — kick off the review in the background; poll GET for completion."""
    sess = await asyncio.to_thread(session_store.load, session_id)
    if sess.status == "reviewing":
        return {"accepted": False, "status": sess.status}
    sess.status = "reviewing"
    await asyncio.to_thread(session_store.save, sess)
    threading.Thread(target=_review_worker, args=(session_id,), daemon=True).start()
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
