"""Library reading-queue route (Stage 2 of the two-stage reading flow)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library import (
    deep_review,
    fulltext,
    paper_render,
    qa,
    reading_queue,
    score_tags,
)
from zotero_summarizer.services.library.review_fleet import fleet as review_fleet
from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise
from zotero_summarizer.storage import repositories as triage_db


router = APIRouter()

# The Zotero tag applied by Deep Review "Remove" (hard-veto strong_negative).
_REJECT_TAG = "❌"


class DeepReviewRunRequest(BaseModel):
    top_k: int = Field(default=5, ge=1, le=20)
    # When set, deep-review this single item instead of the top-K queue slice
    # (the per-paper "Run deeper review" button).
    item_key: str | None = Field(default=None, min_length=1)
    # Optional reader focus — shapes which aspects the LLM emphasises.
    focus_prompt: str = Field(default="", max_length=1000)


class ReviewFleetRunRequest(BaseModel):
    # How many top Read-next picks to pre-decide in this run.
    top_k: int = Field(default=5, ge=1, le=20)


class RejectTagRequest(BaseModel):
    item_key: str = Field(..., min_length=1)


class RelTagSyncRequest(BaseModel):
    # Apply even if Zotero is running (writes back up first regardless).
    force: bool = Field(default=False)


class AskPaperRequest(BaseModel):
    item_key: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=2000)
    # comprehensive (default; metadata+notes+body) | retrieval (top-k chunks) | full_text (raw body)
    mode: Literal["comprehensive", "retrieval", "full_text"] = Field(default="comprehensive")


class PaperRenderBuildRequest(BaseModel):
    force: bool = Field(default=False)
    # Explicit consent gate for arXiv source tarball download. When false, the
    # builder still uses local TeX but falls back to PDF instead of downloading.
    allow_arxiv_source: bool = Field(default=False)


async def get_reading_queue(
    include_read: bool = False, limit: int = 30, refresh: bool = False,
    collection: str = "", tag: str = "", search: str = "", semantic: bool = False,
) -> dict[str, Any]:
    """Ranked queue over the WHOLE library, scored by the gate. Read items are
    hidden unless ``include_read=true``. ``collection``/``tag``/``search`` filter
    the rows. With ``semantic=true`` + a ``search`` query, the queue is ranked by
    HYBRID search (BM25 + dense embeddings + local cross-encoder rerank) instead of
    the substring filter; the response adds ``semantic`` / ``reranked`` /
    ``reranker_loading`` / ``semantic_unavailable`` flags. Returns ``status``
    ('ready'|'computing'), ``model_ready``, ``items`` (each with
    ``relevance_score`` + ``why_reason``), ``total_unread``, ``read_hidden``,
    ``scores_stale``. ``limit`` caps the returned list — the frontend requests the
    whole library and reveals it incrementally. ``refresh=true`` forces a
    background rescore (the only thing that recomputes — opening never rescans)."""
    if not (1 <= limit <= 10000):
        raise APIError(
            error="validation_error",
            message=f"limit must be between 1 and 10000 inclusive; got {limit}",
            status_code=422,
        )
    # Zotero not configured is an EXPECTED state (first run), not a server fault:
    # fail fast at the boundary with a clean 503 — matching the /api/zotero/*
    # routes — instead of letting build_reading_queue's reader-unavailable
    # RuntimeError surface as a 500 "Unexpected server error".
    get_zotero_reader_or_raise()
    return await asyncio.to_thread(
        reading_queue.build_reading_queue,
        include_read=include_read, limit=limit, refresh=refresh,
        collection=collection, tag=tag, search=search, semantic=semantic,
    )


async def get_reading_queue_status() -> dict[str, Any]:
    """In-memory scoring-job state ONLY — no Zotero read, no library scan, so it's
    cheap to poll. The frontend polls THIS while a Rescore is computing (instead of
    re-fetching the whole-library queue every few seconds) and fires one full
    reading-queue reload when ``running`` flips to false."""
    return {"running": reading_queue.is_running(), "error": reading_queue.last_error()}


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


async def get_paper_render(item_key: str) -> dict[str, Any]:
    """Paper-read artifact status/metadata. Use POST ``/build`` to generate the
    Markdown notes, HTML presentation, figures, and audit files."""
    return await asyncio.to_thread(paper_render.render_paper, item_key)


async def build_paper_render(item_key: str, req: PaperRenderBuildRequest) -> dict[str, Any]:
    """Start a background paper-render-compatible build for one item."""
    return await asyncio.to_thread(
        paper_render.start_build,
        item_key,
        force=req.force,
        allow_arxiv_source=req.allow_arxiv_source,
    )


async def get_paper_figure(item_key: str, name: str) -> FileResponse:
    """Serve one generated figure image from a paper-read artifact."""
    path = await asyncio.to_thread(paper_render.figure_path, item_key, name)
    media = "image/png" if path.suffix == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media)


async def get_paper_presentation(item_key: str) -> FileResponse:
    """Serve the generated single-file HTML "paper brief". Disposition is
    ``inline`` so the reader pane can embed it in an <iframe> (a ``filename``
    alone would force ``attachment`` → a download instead of rendering)."""
    path = await asyncio.to_thread(paper_render.presentation_path, item_key)
    return FileResponse(
        path, media_type="text/html", filename=path.name, content_disposition_type="inline"
    )


async def ask_paper(req: AskPaperRequest) -> dict[str, Any]:
    """Grounded Q&A about one paper using the local deep_review-stage model and
    the benchmark-validated abstention prompt. ``answer`` is null when the model
    abstains (the paper doesn't contain the answer)."""
    return await asyncio.to_thread(qa.ask_paper, req.item_key, req.question, mode=req.mode)


async def run_deep_review(req: DeepReviewRunRequest) -> dict[str, Any]:
    """Start an on-demand full-text deep review (quality + relevance). With
    ``item_key`` set, reviews that single paper (per-paper button); otherwise the
    top-``top_k`` unread picks. Single-flight: returns the in-flight status when
    a run is already going. Poll ``GET /api/library/deep-review/status``."""
    if req.item_key:
        return await asyncio.to_thread(
            deep_review.start, item_keys=[req.item_key], focus_prompt=req.focus_prompt
        )
    return await asyncio.to_thread(deep_review.start, req.top_k, focus_prompt=req.focus_prompt)


async def get_deep_review_status() -> dict[str, Any]:
    """Poll the deep-review job: ``{status, total, completed, error, started_at}``."""
    return deep_review.status()


async def run_review_fleet(req: ReviewFleetRunRequest) -> dict[str, Any]:
    """Start the review fleet: pre-decide a reading verdict for the top-``top_k``
    Read-next picks in the background (reusing cached deep reviews, serial for RAM
    safety). The proposals are SUGGESTIONS surfaced on the queue as
    ``proposed_verdict`` — never auto-applied labels. Single-flight: returns the
    in-flight status when a run is already going. Poll ``GET
    /api/library/review-fleet/status``."""
    return await asyncio.to_thread(review_fleet.start, req.top_k)


async def get_review_fleet_status() -> dict[str, Any]:
    """Poll the review-fleet job: ``{status, total, completed, error, started_at,
    progress}``."""
    return review_fleet.status()


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


async def sync_rel_tags(req: RelTagSyncRequest) -> dict[str, Any]:
    """Apply ``zs:rel/<band>`` relevance tags to scored library items so the user
    can filter by ML relevance in Zotero. Backs up first; mutually exclusive in
    the ``zs:rel/*`` namespace; never touches priority/manual tags."""
    return await asyncio.to_thread(score_tags.sync_rel_tags, force=req.force)


async def fetch_fulltext(req: RelTagSyncRequest) -> dict[str, Any]:
    """Bulk: download arXiv full-text PDFs for every library paper that has an
    arXiv link but no PDF, and attach them natively to Zotero (imported_url; the
    library syncs, so Zotero uploads them on its next sync). Runs as a background
    job; backup-first + connector-guarded. Returns ``{status:'started'|'running'}``
    or a ``{requires_force}`` notice when Zotero is running."""
    return await asyncio.to_thread(fulltext.start_bulk, force=req.force)


async def fetch_fulltext_status() -> dict[str, Any]:
    """Cheap in-memory bulk-fetch job state (no Zotero read): ``{running,
    progress:{done,total}, result}`` — polled by the Library page while running."""
    return fulltext.status()


async def sync_score_ranks(req: RelTagSyncRequest) -> dict[str, Any]:
    """Stamp a whole-library goal-blended rank into EVERY paper's Zotero Call
    Number (``zr0001``…) — scorable papers first, no-abstract last — so the user
    can SORT their entire library by relevance in Zotero (tags only filter).
    Reads the global score cache (Rescore first); backs up first; overwrites only
    the Call Number field."""
    return await asyncio.to_thread(score_tags.sync_score_ranks, force=req.force)


router.add_api_route("/api/library/reading-queue", get_reading_queue, methods=["GET"])
router.add_api_route("/api/library/reading-queue/status", get_reading_queue_status, methods=["GET"])
router.add_api_route("/api/library/pdf/{item_key}", get_item_pdf, methods=["GET"])
router.add_api_route("/api/library/render/{item_key}", get_paper_render, methods=["GET"])
router.add_api_route("/api/library/render/{item_key}/build", build_paper_render, methods=["POST"])
router.add_api_route("/api/library/render/{item_key}/presentation", get_paper_presentation, methods=["GET"])
router.add_api_route("/api/library/render/{item_key}/figures/{name}", get_paper_figure, methods=["GET"])
router.add_api_route("/api/library/ask", ask_paper, methods=["POST"])
router.add_api_route("/api/library/deep-review/run", run_deep_review, methods=["POST"])
router.add_api_route("/api/library/deep-review/status", get_deep_review_status, methods=["GET"])
router.add_api_route("/api/library/review-fleet/run", run_review_fleet, methods=["POST"])
router.add_api_route("/api/library/review-fleet/status", get_review_fleet_status, methods=["GET"])
router.add_api_route("/api/library/reject-tag", queue_reject_tag, methods=["POST"])
router.add_api_route("/api/library/fetch-fulltext", fetch_fulltext, methods=["POST"])
router.add_api_route("/api/library/fetch-fulltext/status", fetch_fulltext_status, methods=["GET"])
router.add_api_route("/api/library/sync-rel-tags", sync_rel_tags, methods=["POST"])
router.add_api_route("/api/library/sync-score-ranks", sync_score_ranks, methods=["POST"])
