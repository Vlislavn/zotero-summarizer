"""Request models for the paper-question routes (``/api/library/ask`` and ``/questions``)."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.models.paper_keys import validate_paper_item_key


class AskPaperHistoryTurn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str | None = Field(default=None, max_length=5000)
    quote: str | None = Field(default=None, max_length=5000)
    evidence_handle: dict[str, Any] | None = None


class QuestionSubmissionRequest(BaseModel):
    item_key: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=2000)
    origin: Literal["typed", "preset"] = "typed"
    preset_id: str | None = Field(default=None, min_length=1)

    @field_validator("item_key")
    @classmethod
    def valid_item_key(cls, value: str) -> str:
        return validate_paper_item_key(value)

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value

    @field_validator("preset_id")
    @classmethod
    def nonblank_preset_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("preset_id must not be blank")
        return value

    @model_validator(mode="after")
    def require_preset_id(self):
        if self.origin == "preset" and self.preset_id is None:
            raise ValueError("preset_id is required for preset questions")
        return self


class AskPaperRequest(QuestionSubmissionRequest):
    submission_id: UUID | None = None
    # comprehensive (default; metadata+notes+body) | retrieval (top-k chunks) | full_text (raw body)
    mode: Literal["comprehensive", "retrieval", "full_text"] = Field(default="comprehensive")
    history: list[AskPaperHistoryTurn] = Field(default_factory=list, max_length=20)


class SavePaperQuestionRequest(QuestionSubmissionRequest):
    submission_id: UUID
