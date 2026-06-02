"""HTTP request/response + app-state models for the API and write path."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.domain import normalize_reading_priority
from zotero_summarizer.models.config import GoalsConfig


def _normalized_priority_or_raise(value: str, field: str) -> str:
    """Strip + canonicalise a reading-priority string, or raise a ``field``-named error."""
    normalized = str(value or "").strip()
    coerced = normalize_reading_priority(normalized)
    if coerced != normalized:
        raise ValueError(f"{field} must be one of must_read, should_read, could_read, dont_read")
    return coerced


__all__ = [
    "ErrorResponse",
    "HealthResponse",
    "AppState",
    "ZoteroStatusResponse",
    "ZoteroCollectionsResponse",
    "ZoteroItemsResponse",
    "TriageRunRequest",
    "TriageRunResponse",
    "PendingChangesResponse",
    "PendingChangeMutationRequest",
    "PendingPriorityOverrideRequest",
    "PendingChangeUpdateRequest",
    "ZoteroItemPriorityUpdateRequest",
    "ZoteroItemTagUpdateRequest",
    "ZoteroCollectionRef",
    "ZoteroItemCollectionUpdateRequest",
]


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    config_loaded: bool
    draft_model: Optional[str] = None
    refine_model: Optional[str] = None
    api_base: Optional[str] = None


class AppState(BaseModel):
    config: GoalsConfig

    @model_validator(mode="after")
    def _validate_models(self) -> "AppState":
        if not self.config.llm.draft_model or not self.config.llm.refine_model:
            raise ValueError("both draft and refine models must be set")
        return self


class ZoteroStatusResponse(BaseModel):
    available: bool
    data_dir: str
    db_path: str
    stats: Dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ZoteroCollectionsResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class ZoteroItemsResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)


class TriageRunRequest(BaseModel):
    item_keys: List[str] = Field(..., min_length=1, max_length=500)
    queue_changes: bool = True

    @field_validator("item_keys")
    @classmethod
    def _normalize_item_keys(cls, value: List[str]) -> List[str]:
        normalized: List[str] = []
        seen: set[str] = set()
        for item_key in value:
            key = str(item_key or "").strip()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        if not normalized:
            raise ValueError("item_keys must contain at least one non-empty item key")
        return normalized


class TriageRunResponse(BaseModel):
    job_id: str
    status: Literal["running", "completed", "failed"]
    total: int = Field(default=0, ge=0)


class PendingChangesResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class PendingChangeMutationRequest(BaseModel):
    change_ids: List[int] = Field(..., min_length=1, max_length=1000)
    force: bool = False

    @field_validator("change_ids")
    @classmethod
    def _normalize_change_ids(cls, value: List[int]) -> List[int]:
        normalized: List[int] = []
        seen: set[int] = set()
        for change_id in value:
            numeric = int(change_id)
            if numeric <= 0:
                continue
            if numeric in seen:
                continue
            seen.add(numeric)
            normalized.append(numeric)
        if not normalized:
            raise ValueError("change_ids must contain at least one positive integer")
        return normalized


class PendingPriorityOverrideRequest(BaseModel):
    item_key: str = Field(..., min_length=1, max_length=64)
    item_title: str = Field(default="")
    new_priority: str

    @field_validator("item_key")
    @classmethod
    def _normalize_item_key(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("item_key must be a non-empty string")
        return normalized

    @field_validator("item_title")
    @classmethod
    def _normalize_item_title(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("new_priority")
    @classmethod
    def _normalize_new_priority(cls, value: str) -> str:
        return _normalized_priority_or_raise(value, "new_priority")


class PendingChangeUpdateRequest(BaseModel):
    payload: Dict[str, Any]

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("payload must be an object")
        return value


class ZoteroItemPriorityUpdateRequest(BaseModel):
    priority: str
    force: bool = False

    @field_validator("priority")
    @classmethod
    def _normalize_priority(cls, value: str) -> str:
        return _normalized_priority_or_raise(value, "priority")


class ZoteroItemTagUpdateRequest(BaseModel):
    add_tags: List[str] = Field(default_factory=list)
    remove_tags: List[str] = Field(default_factory=list)
    force: bool = False

    @staticmethod
    def _normalize_tags(value: List[str]) -> List[str]:
        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = str(raw or "").strip()
            if not tag:
                continue
            folded = tag.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            cleaned.append(tag)
        return cleaned

    @field_validator("add_tags", "remove_tags", mode="before")
    @classmethod
    def _coerce_tag_lists(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            return [part.strip() for part in text.split(",") if part.strip()]
        raise ValueError("tag fields must be a list of strings")

    @field_validator("add_tags", "remove_tags")
    @classmethod
    def _normalize_tag_lists(cls, value: List[str]) -> List[str]:
        return cls._normalize_tags(value)

    @model_validator(mode="after")
    def _ensure_non_empty_update(self) -> "ZoteroItemTagUpdateRequest":
        if not self.add_tags and not self.remove_tags:
            raise ValueError("at least one tag must be added or removed")
        return self


class ZoteroCollectionRef(BaseModel):
    collection_key: str = ""
    collection_path: str = ""

    @field_validator("collection_key", "collection_path")
    @classmethod
    def _normalize_collection_fields(cls, value: str) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _ensure_identifier(self) -> "ZoteroCollectionRef":
        if not self.collection_key and not self.collection_path:
            raise ValueError("collection_key or collection_path is required")
        return self

    def to_writer_payload(self) -> Dict[str, str]:
        payload: Dict[str, str] = {}
        if self.collection_key:
            payload["collection_key"] = self.collection_key
        if self.collection_path:
            payload["collection_path"] = self.collection_path
        return payload


class ZoteroItemCollectionUpdateRequest(BaseModel):
    add: List[ZoteroCollectionRef] = Field(default_factory=list)
    remove: List[ZoteroCollectionRef] = Field(default_factory=list)
    force: bool = False

    @model_validator(mode="after")
    def _ensure_any_collection_change(self) -> "ZoteroItemCollectionUpdateRequest":
        if not self.add and not self.remove:
            raise ValueError("at least one collection must be added or removed")
        return self
