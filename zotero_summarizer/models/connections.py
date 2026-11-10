"""Closed, non-secret connection task commands. IDs are server-owned or UUID receipts."""
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .providers import ProviderType


class RegisterConnection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    revision: str
    type: ProviderType = ProviderType.openai
    base_url: str | None = None
    auth: Literal['none', 'existing', 'new'] = 'none'
    authorization: str | None = None  # existing provider reference, never env slot input
    source: str | None = None  # copy technical options; never mutate this provider
    approved: StrictBool = False


class ConnectionSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID
    revision: str
    model: str = Field(min_length=1, max_length=1024)
    preview: str | None = None
    targets: list[Literal['default', 'feed', 'backlog', 'deep_review']] = Field(default_factory=lambda: ['default'], min_length=1, max_length=4)
    corrections: dict[Literal['feed', 'backlog', 'deep_review'], Literal['previous', 'default']] = Field(default_factory=dict)

    @field_validator('targets')
    @classmethod
    def unique_targets(cls, value):
        return _unique_scope(value)

    @field_validator('model')
    @classmethod
    def substantive_model(cls, value):
        if not value.strip():
            raise ValueError('Enter an explicit model ID')
        return value


class ModelTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    model: str = Field(min_length=1, max_length=1024)
    operation_id: UUID | None = None


def _unique_scope(value):
    if len(set(value)) != len(value):
        raise ValueError('Choose each task only once')
    return value


class TaskModelsTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    stages: list[Literal['feed', 'backlog', 'deep_review']] = Field(min_length=1, max_length=3)

    @field_validator('stages')
    @classmethod
    def unique_stages(cls, value):
        return _unique_scope(value)


class ReplaceConnectionCredential(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: UUID = Field(default_factory=uuid4)
    provider: str
    connection_id: UUID | None = None
    revision: str
    preview: str | None = None
    secret: str | None = Field(default=None, min_length=1, max_length=16384)


class ChecklistEngineTest(BaseModel):
    """Settings 'Checklist engine' Test: try the chosen engine; a Cloud key, when given, is saved to the OS keyring first."""
    model_config = ConfigDict(extra='forbid')
    engine: Literal['off', 'local', 'cloud']
    url: str = Field(default='', max_length=2048)
    secret: str | None = Field(default=None, min_length=1, max_length=16384)
