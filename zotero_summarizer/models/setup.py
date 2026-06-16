"""Pydantic contract for the first-run setup surface (``/api/setup/*``).

These shapes are the FROZEN endpoint contract the Settings/onboarding UI is built
against. Field names here are load-bearing — do not rename without updating the
frontend in lockstep.

SECURITY: nothing in these models ever carries an API-key *value*. ``api_key_env``
is only ever the NAME of an env var; key presence is a BOOL (``api_key_present``).
"""
from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, Field


__all__ = [
    "ConfigStatus",
    "LlmStatus",
    "PathEntry",
    "PathStatus",
    "ZoteroStatus",
    "ClassifierStatus",
    "SetupStatusResponse",
    "DetectedZoteroDir",
    "DetectZoteroResponse",
    "UpdatePathsRequest",
    "UpdatePathsValidation",
    "UpdatePathsResponse",
    "FieldError",
    "ConnectionResult",
    "ValidateConfigRequest",
    "ValidateConfigResponse",
]


# --- GET /api/setup/status -------------------------------------------------


class ConfigStatus(BaseModel):
    present: bool
    valid: bool
    research_goals_count: int = Field(ge=0)
    error: Optional[str] = None


class LlmStatus(BaseModel):
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    # The NAME of the env var that holds the key — never the key value.
    api_key_env: Optional[str] = None
    api_key_present: bool
    reachable: bool
    detail: str = ""


class PathEntry(BaseModel):
    value: str
    # ``set`` = the env var is literally present (os.getenv(...) is not None),
    # distinct from a defaulted value.
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
    classifier_name: Optional[str] = None
    trained_at: Optional[str] = None


class SetupStatusResponse(BaseModel):
    # ``ready`` = config.valid AND research_goals_count>0 AND llm.api_key_present
    # AND zotero.db_found. reachable/classifier are advisory, NOT part of ready.
    ready: bool
    config: ConfigStatus
    llm: LlmStatus
    paths: PathStatus
    zotero: ZoteroStatus
    classifier: ClassifierStatus


# --- GET /api/setup/detect-zotero ------------------------------------------


class DetectedZoteroDir(BaseModel):
    data_dir: str
    db_path: str
    db_exists: bool
    storage_exists: bool
    # Where this candidate came from: "env" (current settings) or "probe".
    source: str


class DetectZoteroResponse(BaseModel):
    candidates: List[DetectedZoteroDir] = Field(default_factory=list)


# --- PUT /api/setup/paths --------------------------------------------------


class UpdatePathsRequest(BaseModel):
    pdf_root: Optional[str] = None
    zotero_data_dir: Optional[str] = None


class UpdatePathsValidation(BaseModel):
    pdf_root_exists: bool
    zotero_db_found: bool


class UpdatePathsResponse(BaseModel):
    written: List[str] = Field(default_factory=list)
    # A path change requires a restart: Settings is frozen and loaded once at
    # startup, so the live process keeps the old values until relaunch.
    restart_required: bool = True
    validated: UpdatePathsValidation


# --- POST /api/setup/validate-config ---------------------------------------


class FieldError(BaseModel):
    loc: List[Union[str, int]] = Field(default_factory=list)
    msg: str


class ConnectionResult(BaseModel):
    tested_provider: str
    tested_model: str
    status: str  # "operational" | "fail"
    detail: str = ""
    models_discovered: int = Field(default=0, ge=0)


class ValidateConfigRequest(BaseModel):
    # The candidate GoalsConfig draft, as a raw mapping (validated by the service,
    # NOT by this request model — the whole point is to surface field errors
    # instead of 422-ing the request).
    config: dict
    test_connection: bool = False


class ValidateConfigResponse(BaseModel):
    valid: bool
    field_errors: List[FieldError] = Field(default_factory=list)
    # null when test_connection=false OR the config was invalid.
    connection: Optional[ConnectionResult] = None
