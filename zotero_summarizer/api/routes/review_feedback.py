from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from zotero_summarizer.services.library import review_feedback

router = APIRouter()
_PATH = "/api/library/review-feedback"


class ReviewFeedbackRequest(BaseModel):
    item_key: str = Field(..., min_length=1)
    chips: list[str] = Field(default_factory=list, max_length=len(review_feedback.CHIPS))
    note: str = Field(default="", max_length=review_feedback.NOTE_MAX)


class PartFeedbackRequest(BaseModel):
    reviewed_at: str = Field(..., min_length=1)  # the review version the page shows
    chips: list[str] = Field(default_factory=list, max_length=len(review_feedback.CHIPS))
    note: str = Field(default="", max_length=review_feedback.NOTE_MAX)


async def save_review_feedback(req: ReviewFeedbackRequest) -> dict[str, Any]:
    """Whole-review chips + note on the paper's current review; empty chips and note withdraw it."""
    return await asyncio.to_thread(review_feedback.save, req.item_key, req.chips, req.note)


async def get_review_feedback(item_key: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Feedback saved for this paper, one entry per review version."""
    return {"item_key": item_key, "feedback": await asyncio.to_thread(review_feedback.list_feedback, item_key)}


async def put_feedback_part(item_key: str, field: str, req: PartFeedbackRequest) -> dict[str, Any]:
    """Save one part's reasons and note (``field``: a review part or ``whole``); the server records what it showed."""
    return await asyncio.to_thread(review_feedback.put_part, item_key, field, req.reviewed_at, req.chips, req.note)


async def delete_feedback_part(item_key: str, field: str, reviewed_at: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Withdraw one part (idempotent): its record stays as ``withdrawn``."""
    return await asyncio.to_thread(review_feedback.delete_part, item_key, field, reviewed_at)


async def get_feedback_checks(item_key: str) -> dict[str, Any]:
    """What was concluded about the current review's parts (``check.json``, read-only)."""
    return await asyncio.to_thread(review_feedback.checks, item_key)


async def export_review_feedback() -> Response:
    """Every review-feedback row as a JSON download (user-study data); reads only."""
    rows = await asyncio.to_thread(review_feedback.export)
    return Response(json.dumps(rows, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="review-feedback.json"'})


router.add_api_route(_PATH, save_review_feedback, methods=["POST"])
router.add_api_route(_PATH, get_review_feedback, methods=["GET"])
router.add_api_route(f"{_PATH}/export", export_review_feedback, methods=["GET"])
router.add_api_route(f"{_PATH}/check/{{item_key}}", get_feedback_checks, methods=["GET"])
router.add_api_route(f"{_PATH}/{{item_key}}/parts/{{field}}", put_feedback_part, methods=["PUT"])
router.add_api_route(f"{_PATH}/{{item_key}}/parts/{{field}}", delete_feedback_part, methods=["DELETE"])
