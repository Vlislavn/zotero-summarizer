"""Weekly RSS digest consumption; generation here uses cached reviews only."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from zotero_summarizer.services._common import settings
from zotero_summarizer.services.research_feed import reports

router = APIRouter(prefix="/api/research-feed")


class CachedRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date
    venue: str = Field(default="", max_length=200)
    card_budget: int = Field(default=10, strict=True, ge=1, le=10)


@router.get("/runs")
def list_runs(limit: int = Query(default=50, ge=1, le=100)):
    return reports.list_reports(settings(), limit=limit)


@router.post("/run")
def run_cached(body: CachedRunRequest):
    return reports.run_cached(settings(), **body.model_dump())


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return reports.get_report(settings(), run_id)


@router.get("/runs/{run_id}/download")
def download_run(run_id: str, format: Literal["json", "md"] = "json"):
    path = reports.report_path(settings(), run_id, format)
    return FileResponse(path, filename=path.name,
                        media_type="application/json" if format == "json" else "text/markdown")
