"""Saved Weekly reads and explicit bounded Telegram Sources actions."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from zotero_summarizer.models.research_feed import TelegramSourceSettings

from zotero_summarizer.services._common import settings
from zotero_summarizer.services.research_feed import (
    reports, telegram_history, telegram_job, telegram_processing, telegram_reasons, telegram_today, telegram_workflow)

router = APIRouter(prefix="/api/research-feed")


class TelegramCollectConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    allow: bool


class TelegramReasonsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    chips: list[str] | None  # None undoes; [] = 'не подошло' without a reason


class TelegramFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    liked: list[str] = Field(max_length=20)
    note: str = Field("", max_length=5000)  # stored text is cut to telegram_reasons.NOTE_MAX


class TelegramDoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_ids: list[str] = Field(max_length=200)
    done: bool


class TelegramProcessingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str
    allow: bool


@router.post("/telegram/reasons")
def telegram_post_reasons(request: TelegramReasonsRequest):
    return telegram_reasons.save(settings(), request.source_id, request.chips)


@router.post("/telegram/feedback")
def telegram_post_feedback(request: TelegramFeedbackRequest):
    """Positive chips + note ('what I liked'), kept next to the 'не подошло' reasons."""
    return telegram_reasons.save_feedback(settings(), request.source_id, request.liked, request.note)


@router.get("/telegram/feedback/export")
def telegram_feedback_export():
    return Response(json.dumps(telegram_history.feedback_export(settings()), ensure_ascii=False, indent=1),
                    media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="telegram-feedback.json"'})


@router.post("/telegram/done")
def telegram_done(request: TelegramDoneRequest):
    return telegram_history.mark_done(settings(), request.source_ids, request.done)


@router.get("/telegram/history")
def telegram_history_list():
    return {"items": telegram_history.list_runs(settings())}


@router.get("/telegram/history/{run_id}")
def telegram_history_run(run_id: str):
    return telegram_history.read_run(settings(), run_id)


@router.post("/telegram/processing")
def telegram_processing_consent(request: TelegramProcessingRequest):
    """Allow/withdraw sending channel text to the saved non-local Telegram provider."""
    return telegram_processing.set_remote_consent(settings(), request.token, request.allow)


@router.get("/telegram/sources")
def telegram_sources():
    return telegram_workflow.sources(settings())


@router.put("/telegram/sources")
def save_telegram_sources(selection: TelegramSourceSettings):
    return telegram_workflow.save_sources(settings(), selection)


@router.get("/telegram/status")
def telegram_status():
    return telegram_workflow.status(settings())


@router.get("/telegram/today")
def telegram_today_digest():
    """Read-only: the latest 'Сегодня в каналах' posts, discussions and weekly trends."""
    return telegram_today.read_today(settings())


@router.get("/telegram/thread/{source_id}")
def telegram_thread(source_id: str):
    """Read-only: every stored comment of one discussion (loaded on expand)."""
    telegram_reasons.parse(source_id)  # 404 on a malformed id
    return {"thread": telegram_today.thread(settings(), source_id)}


@router.post("/telegram/collect-consent")
def telegram_collect_consent(request: TelegramCollectConsentRequest):
    """Remember (or revoke) the one-time OK to read the saved sources; asked once by the button."""
    return telegram_processing.set_collect_consent(settings(), request.allow)


@router.post("/telegram/runs", status_code=202)
def run_telegram():
    """Start «Проверить каналы» in the background; while one runs, return its running status."""
    return telegram_job.start(settings())


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
