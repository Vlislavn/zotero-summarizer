"""Saved Weekly reads and explicit bounded Telegram Sources actions."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from zotero_summarizer.models.research_feed import TelegramSourceSettings

from zotero_summarizer.services._common import settings
from zotero_summarizer.services.research_feed import reports, telegram_workflow

router = APIRouter(prefix="/api/research-feed")


class TelegramRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    consent: bool


@router.get("/telegram/sources")
def telegram_sources():
    return telegram_workflow.sources(settings())


@router.put("/telegram/sources")
def save_telegram_sources(selection: TelegramSourceSettings):
    return telegram_workflow.save_sources(settings(), selection)


@router.get("/telegram/status")
def telegram_status():
    return telegram_workflow.status(settings())


@router.post("/telegram/runs")
def run_telegram(request: TelegramRunRequest):
    return telegram_workflow.run(settings(), consent=request.consent)


@router.get("/runs")
def list_runs(limit: int = Query(default=50, ge=1, le=100)):
    return reports.list_reports(settings(), limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return reports.get_report(settings(), run_id)


@router.get("/runs/{run_id}/download")
def download_run(run_id: str, format: Literal["json", "md"] = "json"):
    content = reports.download_bytes(settings(), run_id, format)
    filename = f"{run_id}.{format}"
    return Response(content=content, media_type="application/json" if format == "json" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
