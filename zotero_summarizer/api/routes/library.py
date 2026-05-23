"""Library reading-queue route (Stage 2 of the two-stage reading flow)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import deep_review, reading_queue
from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise
from zotero_summarizer.storage import repositories as triage_db


router = APIRouter()

# The Zotero tag applied by Deep Review "Remove" (hard-veto strong_negative).
_REJECT_TAG = "❌"


class DeepReviewRunRequest(BaseModel):
    top_k: int = Field(default=5, ge=1, le=20)
    # When set, deep-review this single item instead of the top-K queue slice
    # (the per-paper "Run deeper разбор" button).
    item_key: str | None = Field(default=None, min_length=1)


class RejectTagRequest(BaseModel):
    item_key: str = Field(..., min_length=1)


async def get_reading_queue(
    include_read: bool = False, limit: int = 30, refresh: bool = False,
    collection: str = "", tag: str = "", search: str = "",
) -> dict[str, Any]:
    """Ranked 'what to read next' from the library, scored by the gate. Read
    items are hidden unless ``include_read=true``. ``collection``/``tag``/
    ``search`` filter the displayed rows. Returns ``status``
    ('ready'|'computing'), ``model_ready``, ``items`` (each with
    ``relevance_score`` + ``why_reason``), ``total_unread``, ``read_hidden``,
    ``scores_stale``. ``refresh=true`` forces a background rescore (the only
    thing that recomputes — opening never rescans)."""
    if not (1 <= limit <= 200):
        raise APIError(
            error="validation_error",
            message=f"limit must be between 1 and 200 inclusive; got {limit}",
            status_code=422,
        )
    return await asyncio.to_thread(
        reading_queue.build_reading_queue,
        include_read=include_read, limit=limit, refresh=refresh,
        collection=collection, tag=tag, search=search,
    )


async def get_item_pdf(item_key: str) -> FileResponse:
    """Stream the Zotero-stored PDF for a library item so the UI can link to the
    full text. 404 when the item or its local PDF is missing. The path comes
    from Zotero (not user input); we still verify it's a real file."""
    reader = get_zotero_reader_or_raise()
    detail = await asyncio.to_thread(reader.get_item_detail, item_key)
    if detail is None:
        raise APIError(error="not_found", message=f"Item {item_key} not found", status_code=404)
    pdf_path = str(detail.get("pdf_path") or "")
    if not pdf_path or not Path(pdf_path).is_file():
        raise APIError(error="not_found", message=f"No PDF on file for item {item_key}", status_code=404)
    return FileResponse(pdf_path, media_type="application/pdf", filename=Path(pdf_path).name)


async def run_deep_review(req: DeepReviewRunRequest) -> dict[str, Any]:
    """Start an on-demand full-text deep review (quality + relevance). With
    ``item_key`` set, reviews that single paper (per-paper button); otherwise the
    top-``top_k`` unread picks. Single-flight: returns the in-flight status when
    a run is already going. Poll ``GET /api/library/deep-review/status``."""
    if req.item_key:
        return await asyncio.to_thread(deep_review.start, item_keys=[req.item_key])
    return await asyncio.to_thread(deep_review.start, req.top_k)


async def get_deep_review_status() -> dict[str, Any]:
    """Poll the deep-review job: ``{status, total, completed, error, started_at}``."""
    return deep_review.status()


async def queue_reject_tag(req: RejectTagRequest) -> dict[str, Any]:
    """Queue a Pending ❌ tag for an item (Deep Review 'Remove'). The drop +
    training is the separate ``POST /api/golden/verdict`` (dont_read) call; this
    only stages the Zotero tag, applied via the existing Pending → apply flow.
    Idempotent: a no-op when ❌ is already on the item."""
    reader = get_zotero_reader_or_raise()
    detail = await asyncio.to_thread(reader.get_item_detail, req.item_key)
    if detail is None:
        raise APIError(error="not_found", message=f"Item {req.item_key} not found", status_code=404)

    current_tags = [str(tag or "") for tag in (detail.get("tags") or [])]
    if any(_REJECT_TAG in tag for tag in current_tags):
        return {"queued": 0, "item_key": req.item_key, "message": "Already tagged ❌"}

    item_title = str(detail.get("title") or req.item_key)
    queued = await asyncio.to_thread(
        triage_db.insert_pending_changes,
        req.item_key,
        item_title,
        [{"change_type": "tag_changes", "payload": {"add_tags": [_REJECT_TAG], "remove_tags": []}}],
    )
    return {"queued": queued, "item_key": req.item_key, "add_tags": [_REJECT_TAG]}


router.add_api_route("/api/library/reading-queue", get_reading_queue, methods=["GET"])
router.add_api_route("/api/library/pdf/{item_key}", get_item_pdf, methods=["GET"])
router.add_api_route("/api/library/deep-review/run", run_deep_review, methods=["POST"])
router.add_api_route("/api/library/deep-review/status", get_deep_review_status, methods=["GET"])
router.add_api_route("/api/library/reject-tag", queue_reject_tag, methods=["POST"])
