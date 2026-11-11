"""Analysis of the owner's accumulated decisions: run now, read the report, weekly switch.

Thin HTTP layer over ``services.golden.feedback_analysis`` (see its README for the
report contract). The analysis never changes labels or models.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.golden.feedback_analysis import manual_run, summary, weekly_switch

router = APIRouter(prefix="/api/feedback-analysis")


class WeeklySwitch(BaseModel):
    enabled: bool


def _weekly_enabled() -> bool:
    settings = get_settings()
    return settings.feedback_schedule_enabled and weekly_switch.is_enabled(settings)


@router.get("/status")
async def get_status() -> dict:
    return {"job": manual_run.status(), "latest": summary.latest_run(get_settings()), "weekly_enabled": _weekly_enabled()}


@router.put("/weekly")
async def set_weekly(body: WeeklySwitch) -> dict:
    settings = get_settings()
    if not settings.feedback_schedule_enabled:
        raise APIError("schedule_disabled_by_env", "ZS_FEEDBACK_SCHEDULE_ENABLED=false turns the weekly analysis off.", status_code=409)
    weekly_switch.set_enabled(settings, body.enabled)
    return {"weekly_enabled": body.enabled}


@router.post("/run", status_code=202)
async def run_now() -> dict:
    if not manual_run.start(get_settings()):
        raise APIError("analysis_running", "An analysis is already running.", status_code=409)
    return {"job": manual_run.status()}


@router.get("/runs")
async def list_runs() -> dict:
    return {"items": summary.list_runs(get_settings())}


@router.get("/latest")
async def latest() -> dict:
    settings = get_settings()
    run = summary.latest_run(settings)
    if run is None:
        raise APIError("no_report", "No analysis has been published yet.", status_code=404)
    return await asyncio.to_thread(summary.summary_for, settings, run["run_id"])
