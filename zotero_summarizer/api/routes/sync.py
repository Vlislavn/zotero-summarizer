"""Offline PWA pull, push, and status endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.sync import service

router = APIRouter(prefix="/api/sync", tags=["sync"])


class _Mutation(BaseModel):
    mutation_id: UUID
    device_id: str = Field(min_length=1, max_length=100)
    item_key: str = Field(min_length=1)
    field: Literal["verdict", "review_note", "review_question"]
    operation: Literal["set", "delete", "create"]
    value: str | dict | None = None
    comment: str | None = Field(default=None, max_length=10_000)
    model_priority: str | None = Field(default=None, max_length=50)
    base_revision: int = Field(ge=0)
    resolves_mutation_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("device_id")
    @classmethod
    def require_nonblank_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must contain non-whitespace characters")
        return value

    @model_validator(mode="after")
    def validate_field_payload(self):
        if self.field == "review_question":
            parsed = service.validate_question_mutation(self.model_dump(mode="json"))
            self.value = parsed["value"]
        else:
            if self.operation not in {"set", "delete"}:
                raise ValueError("manual field operation must be set or delete")
            if len(self.item_key) > 500:
                raise ValueError("item_key must not exceed 500 characters")
            self.item_key = self.require_nonblank_identity(self.item_key)
            if self.value is not None and (not isinstance(self.value, str) or len(self.value) > 50_000):
                raise ValueError("manual field value must be text of at most 50000 characters")
        return self


class _PushRequest(BaseModel):
    protocol: Literal[1]
    mutations: list[_Mutation] = Field(max_length=100)
    predecessors: list[UUID] = Field(default_factory=list, max_length=100)


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
        return service.push(_db_path(), mutations, [str(value) for value in req.predecessors])
    except ValueError as exc:
        raise APIError("validation_error", str(exc), 422) from exc


@router.get("/status")
def sync_status():
    return service.status(_db_path())
