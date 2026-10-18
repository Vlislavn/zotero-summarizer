"""Browse and download saved weekly digests; never start generation."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from zotero_summarizer.services._common import settings
from zotero_summarizer.services.research_feed import reports

router = APIRouter(prefix="/api/research-feed")


@router.get("/runs")
def list_runs(limit: int = Query(default=50, ge=1, le=100)):
    return reports.list_reports(settings(), limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return reports.get_report(settings(), run_id)


@router.get("/runs/{run_id}/download")
def download_run(run_id: str, format: Literal["json", "md"] = "json"):
    current = settings()
    reports.get_report(current, run_id)
    path = reports.report_path(current, run_id, format)
    return FileResponse(path, filename=path.name,
                        media_type="application/json" if format == "json" else "text/markdown")
