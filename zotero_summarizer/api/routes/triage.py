from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.models import TriageRunResponse
from zotero_summarizer.services.triage import triage_jobs

router = APIRouter()
router.add_api_route("/api/triage/run", triage_jobs.run_triage_job, methods=["POST"], response_model=TriageRunResponse)
router.add_api_route("/api/triage/jobs", triage_jobs.list_triage_jobs, methods=["GET"])
router.add_api_route("/api/triage/jobs/{job_id}", triage_jobs.get_triage_job, methods=["GET"])
router.add_api_route("/api/triage/feedback/latest", triage_jobs.get_latest_triage_feedback, methods=["GET"])
router.add_api_route("/api/triage/jobs/{job_id}/cancel", triage_jobs.cancel_triage_job, methods=["POST"])
