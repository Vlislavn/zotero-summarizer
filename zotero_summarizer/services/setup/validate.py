"""Read-only setup-draft validation and optional provider probe."""
from __future__ import annotations

import asyncio

import pydantic

from zotero_summarizer.models.config import GoalsConfig
from zotero_summarizer.models.providers import LLMCapability, resolve_stage
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
    if not config.llm_enabled:
        return ConnectionResult(tested_provider="", tested_model="", status="disabled",
                                detail="AI features are disabled by choice", hardware_safe="not_applicable")
    resolved = resolve_stage(config.llm_routing, "deep_review")
    provider, model = resolved.provider, resolved.model

    probe = operational_check.probe_provider(provider, model)

    models_discovered = 0
    try:
        models = model_list.list_models_for_provider(provider)
        models_discovered = len(models)
    except Exception:  # noqa: BLE001 — advisory count only; the probe detail is authoritative
        models_discovered = 0
        models = None

    return ConnectionResult(
        tested_provider=provider.name,
        tested_model=model,
        status=str(probe["status"]),
        detail=str(probe["detail"]),
        models_discovered=models_discovered,
        listed=model in models if models is not None else None,
        capable=LLMCapability.text in provider.available_capabilities,
        observed_operational=probe["status"] == "operational",
        hardware_safe=probe.get("hardware_safe", "unknown"),
        reason_code=probe.get("reason_code"),
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
