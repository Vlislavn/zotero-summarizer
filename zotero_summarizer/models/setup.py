"""Redacted request/response contracts for ``/api/setup/*``."""
from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "ClassifierStatus", "ConfigStatus", "ConnectionResult", "DetectedZoteroDir",
    "DetectZoteroResponse", "FieldError", "LlmStatus", "PathEntry", "PathStatus",
    "SetupStatusResponse", "SubsystemStatus", "UpdatePathsRequest",
    "UpdatePathsResponse", "UpdatePathsValidation", "ValidateConfigRequest",
    "ValidateConfigResponse", "ZoteroStatus",
]


class ConfigStatus(BaseModel):
    present: bool
    valid: bool
    research_goals_count: int = Field(ge=0)
    error: str | None = None


class LlmStatus(BaseModel):
    default_provider: str | None = None
    default_model: str | None = None
    api_key_env: str | None = None  # name only; never the secret
    api_key_present: bool
    reachable: bool
    detail: str = ""


class PathEntry(BaseModel):
    value: str
    set: bool
    exists: bool


class PathStatus(BaseModel):
    pdf_root: PathEntry
    zotero_data_dir: PathEntry


class ZoteroStatus(BaseModel):
    db_found: bool
    data_dir: str
    db_path: str
    library_item_count: int = Field(ge=0)
    feed_count: int = Field(ge=0)
    error: str = ""


class ClassifierStatus(BaseModel):
    trained: bool
    classifier_name: str | None = None
    trained_at: str | None = None


class SubsystemStatus(BaseModel):
    name: str
    ready: bool
    detail: str = ""


class SetupStatusResponse(BaseModel):
    configured: bool
    ready: bool
    config: ConfigStatus
    llm: LlmStatus
    paths: PathStatus
    zotero: ZoteroStatus
    classifier: ClassifierStatus
    subsystems: list[SubsystemStatus] = Field(default_factory=list)


class DetectedZoteroDir(BaseModel):
    data_dir: str
    db_path: str
    db_exists: bool
    storage_exists: bool
    source: str


class DetectZoteroResponse(BaseModel):
    candidates: list[DetectedZoteroDir] = Field(default_factory=list)


class UpdatePathsRequest(BaseModel):
    pdf_root: str | None = None
    zotero_data_dir: str | None = None


class UpdatePathsValidation(BaseModel):
    pdf_root_exists: bool
    zotero_db_found: bool


class UpdatePathsResponse(BaseModel):
    written: list[str] = Field(default_factory=list)
    restart_required: bool = True
    validated: UpdatePathsValidation


class FieldError(BaseModel):
    loc: list[str | int] = Field(default_factory=list)
    msg: str


class ConnectionResult(BaseModel):
    tested_provider: str
    tested_model: str
    status: str
    detail: str = ""
    models_discovered: int = Field(default=0, ge=0)


class ValidateConfigRequest(BaseModel):
    config: dict
    test_connection: bool = False


class ValidateConfigResponse(BaseModel):
    valid: bool
    field_errors: list[FieldError] = Field(default_factory=list)
    connection: ConnectionResult | None = None
