"""Browser-reported Today exposure events (viewport, dwell, link opens), local log only."""
import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.services.triage import today_exposure

router = APIRouter()


class TodayEvent(BaseModel):
    item_key: str = Field(..., min_length=1, max_length=512)
    item_id: Annotated[int, Field(strict=True, gt=0, lt=2**63)] | None = None
    kind: Literal["card_shown", "card_dwell", "open_review", "open_link"]
    surface: Literal["today", "review"] = "today"
    target: Literal["title", "abstract", "doi", "pdf", "code"] | None = None
    ms: int | None = Field(default=None, ge=0, le=6 * 3600 * 1000)
    client_ts: str = Field(..., max_length=40)


class TodayEventsRequest(BaseModel):
    slate_id: str = Field(..., min_length=1, max_length=64)
    events: list[TodayEvent] = Field(..., min_length=1, max_length=50)


@router.post("/api/daily/events")
async def post_events(body: TodayEventsRequest) -> dict[str, int]:
    events = [event.model_dump() for event in body.events]
    return {"logged": await asyncio.to_thread(today_exposure.record_events, body.slate_id, events)}
