"""Offline PWA pull, push, and status endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.sync import service

router = APIRouter(prefix="/api/sync", tags=["sync"])


class _Mutation(BaseModel):
    mutation_id: UUID
    device_id: str = Field(min_length=1, max_length=100)
    item_key: str = Field(min_length=1, max_length=500)
    field: Literal["verdict", "review_note"]
    operation: Literal["set", "delete"]
    value: str | None = Field(default=None, max_length=50_000)
    comment: str | None = Field(default=None, max_length=10_000)
    model_priority: str | None = Field(default=None, max_length=50)
    base_revision: int = Field(ge=0)
    resolves_mutation_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class _PushRequest(BaseModel):
    protocol: Literal[1]
    mutations: list[_Mutation] = Field(max_length=100)


def _db_path():
    return settings().triage_db_path


@router.get("/pull")
def pull(protocol: int = Query(ge=1, le=1), since: int = Query(default=0, ge=0)):
    assert protocol == 1
    return service.pull(_db_path(), since)


@router.post("/push")
def push(req: _PushRequest):
    try:
        mutations = [row.model_dump(mode="json") for row in req.mutations]
        return service.push(_db_path(), mutations)
    except ValueError as exc:
        raise APIError("validation_error", str(exc), 422) from exc


@router.get("/status")
def sync_status():
    return service.status(_db_path())
