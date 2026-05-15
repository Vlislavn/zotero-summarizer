"""Phase 1.14 — review-mode endpoints.

Thin wrapper around :mod:`services.review`. The UI calls these to:
  * list items awaiting human verdict (`GET /api/feeds/review`)
  * approve / reject / relabel individual items (`POST .../{id}/...`)
  * batch-apply approvals to Zotero (`POST .../apply-all`)
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services import review

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class RelabelRequest(BaseModel):
    new_priority: str = Field(..., description="must_read | should_read | could_read | dont_read")


class RejectRequest(BaseModel):
    write_to_golden: bool = Field(
        default=True,
        description="Append a dont_read row to zotero-summarizer-golden.csv (triggers retrain).",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


_LIST_STATES = ("awaiting_review", "gate_rejected")


async def list_review_queue(
    state: str = "awaiting_review",
    since_hours: int = 720,
    limit: int = 1000,
    sort: str = "recent",
) -> dict[str, Any]:
    """List review-queue rows for one decision state.

    ``state`` must be in ``_LIST_STATES``. ``gate_rejected`` exposes the items
    the classifier dropped before LLM so the user can spot-check false
    negatives and relabel them; ``awaiting_review`` (the default) is the
    main triage queue.

    ``sort`` controls ordering:
      * ``recent`` (default) — by created_at DESC, the existing behaviour.
      * ``border`` — Sprint-3+ active learning: rank by abs(composite_score
        − nearest priority threshold). The most "uncertain" rows surface
        first so triaging them maximises model improvement per click.
    """
    if state not in _LIST_STATES:
        raise APIError(
            error="validation_error",
            message=f"state must be one of {_LIST_STATES}; got {state!r}",
            status_code=422,
        )
    if sort not in ("recent", "border"):
        raise APIError(
            error="validation_error",
            message=f"sort must be one of ('recent', 'border'); got {sort!r}",
            status_code=422,
        )
    items = await asyncio.to_thread(review.list_by_state, state, since_hours, limit)
    if sort == "border":
        from zotero_summarizer.domain import (
            PRIORITY_COULD_READ_THRESHOLD,
            PRIORITY_MUST_READ_THRESHOLD,
            PRIORITY_SHOULD_READ_THRESHOLD,
        )
        thresholds = (
            PRIORITY_COULD_READ_THRESHOLD,
            PRIORITY_SHOULD_READ_THRESHOLD,
            PRIORITY_MUST_READ_THRESHOLD,
        )

        def _border_dist(row: dict[str, Any]) -> float:
            score = row.get("composite_score")
            if score is None:
                return 1e9
            return float(min(abs(float(score) - t) for t in thresholds))

        items = sorted(items, key=_border_dist)
    return {"state": state, "items": items, "count": len(items), "sort": sort}


async def approve(processed_id: int) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(review.approve, processed_id)
    except KeyError as exc:
        raise APIError(error="not_found", message=str(exc), status_code=404) from exc
    except ValueError as exc:
        raise APIError(error="validation_error", message=str(exc), status_code=422) from exc


async def reject(processed_id: int, req: RejectRequest | None = None) -> dict[str, Any]:
    write_to_golden = True if req is None else bool(req.write_to_golden)
    try:
        return await asyncio.to_thread(review.reject, processed_id, write_to_golden=write_to_golden)
    except KeyError as exc:
        raise APIError(error="not_found", message=str(exc), status_code=404) from exc
    except ValueError as exc:
        raise APIError(error="validation_error", message=str(exc), status_code=422) from exc


async def relabel(processed_id: int, req: RelabelRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(review.relabel, processed_id, req.new_priority)
    except KeyError as exc:
        raise APIError(error="not_found", message=str(exc), status_code=404) from exc
    except ValueError as exc:
        raise APIError(error="validation_error", message=str(exc), status_code=422) from exc


async def apply_all() -> dict[str, Any]:
    """Materialize every user_approved row into Zotero (Inbox + tags + note).

    Calls :func:`review.apply_all_approved`, which uses the daemon-direct
    ``apply_feed_materialization`` path (NOT pending_changes). Returns per-row
    failure detail so the UI can show "applied N, M failed" + reasons.
    """
    return await asyncio.to_thread(review.apply_all_approved)


async def confirm_gate_rejected() -> dict[str, Any]:
    """Bulk-append dont_read rows to the golden CSV for every gate_rejected
    item the user hasn't already relabelled. UI semantics: "no click means
    I confirm the model was right." Triggers retrain via sha mismatch on the
    next ``feeds run`` startup.
    """
    return await asyncio.to_thread(review.confirm_remaining_gate_rejected)


router.add_api_route("/api/feeds/review", list_review_queue, methods=["GET"])
router.add_api_route("/api/feeds/review/apply-all", apply_all, methods=["POST"])
router.add_api_route("/api/feeds/review/confirm-gate-rejected", confirm_gate_rejected, methods=["POST"])
router.add_api_route("/api/feeds/review/{processed_id}/approve", approve, methods=["POST"])
router.add_api_route("/api/feeds/review/{processed_id}/reject", reject, methods=["POST"])
router.add_api_route("/api/feeds/review/{processed_id}/relabel", relabel, methods=["POST"])
