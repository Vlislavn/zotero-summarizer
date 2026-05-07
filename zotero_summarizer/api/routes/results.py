from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.models import TriageFeedbackResponse
from zotero_summarizer.services import results

router = APIRouter()
router.add_api_route("/api/results", results.dashboard_results, methods=["GET"])
router.add_api_route("/api/batches", results.dashboard_batches, methods=["GET"])
router.add_api_route("/api/results/{item_id}", results.dashboard_result_detail, methods=["GET"])
router.add_api_route(
    "/api/triage/results/{item_key}/feedback",
    results.submit_triage_feedback,
    methods=["POST"],
    response_model=TriageFeedbackResponse,
)
router.add_api_route(
    "/api/triage/results/{item_key}/override-dimensions",
    results.override_triage_dimensions,
    methods=["POST"],
)
