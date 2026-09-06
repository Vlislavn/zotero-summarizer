"""Read-only setup-draft validation and optional provider probe."""
from __future__ import annotations

import asyncio

import pydantic

from zotero_summarizer.models.config import GoalsConfig
from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.models.setup import (
    ConnectionResult,
    FieldError,
    ValidateConfigRequest,
    ValidateConfigResponse,
)
from zotero_summarizer.services.llm import model_list, operational_check


def _flatten_errors(exc: pydantic.ValidationError) -> list[FieldError]:
    return [
        FieldError(loc=list(err.get("loc", ())), msg=str(err.get("msg", "")))
        for err in exc.errors()
    ]


def has_personal_goals(config: GoalsConfig) -> bool:
    goals = [str(goal).strip() for goal in config.research_goals if str(goal).strip()]
    return bool(goals) and not any(goal.startswith("Replace with your ") for goal in goals)


def _probe_connection(config: GoalsConfig) -> ConnectionResult:
    """Probe the default model; failures remain user-facing data."""
    resolved = resolve_stage(config.llm_routing, "deep_review")
    provider, model = resolved.provider, resolved.model

    probe = operational_check.probe_provider(provider, model)

    models_discovered = 0
    try:
        models_discovered = len(model_list.list_models_for_provider(provider))
    except Exception:  # noqa: BLE001 — advisory count only; the probe detail is authoritative
        models_discovered = 0

    return ConnectionResult(
        tested_provider=provider.name,
        tested_model=model,
        status=str(probe["status"]),
        detail=str(probe["detail"]),
        models_discovered=models_discovered,
    )


async def validate_config_draft(req: ValidateConfigRequest) -> ValidateConfigResponse:
    """Validate a draft and optionally probe its default provider."""
    try:
        config = GoalsConfig.model_validate(req.config)
    except pydantic.ValidationError as exc:
        return ValidateConfigResponse(valid=False, field_errors=_flatten_errors(exc), connection=None)
    if not has_personal_goals(config):
        error = FieldError(loc=["research_goals"], msg="Replace the example goals with your research")
        return ValidateConfigResponse(valid=False, field_errors=[error], connection=None)

    if not req.test_connection:
        return ValidateConfigResponse(valid=True, field_errors=[], connection=None)

    connection = await asyncio.to_thread(_probe_connection, config)
    return ValidateConfigResponse(valid=True, field_errors=[], connection=connection)
