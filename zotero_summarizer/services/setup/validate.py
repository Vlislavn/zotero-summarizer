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
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.setup.load_attempt import SetupAttempt, check_setup_stages


def _flatten_errors(exc: pydantic.ValidationError) -> list[FieldError]:
    return [
        FieldError(loc=list(err.get("loc", ())), msg=str(err.get("msg", "")))
        for err in exc.errors()
    ]


def has_personal_goals(config: GoalsConfig) -> bool:
    goals = [str(goal).strip() for goal in config.research_goals if str(goal).strip()]
    return bool(goals) and not any(goal.startswith("Replace with your ") for goal in goals)


async def _probe_connection(config: GoalsConfig, settings, attempt: SetupAttempt) -> ConnectionResult:
    """Probe the default model; failures remain user-facing data."""
    resolved = resolve_stage(config.llm_routing, "deep_review")
    provider, model = resolved.provider, resolved.model

    result = await check_setup_stages(settings, config.llm_routing,
                                      stages=("deep_review",), attempt=attempt)
    probe = result["stages"][0]

    models_discovered = 0
    try:
        models_discovered = len(await asyncio.to_thread(model_list.list_models_for_provider, provider))
    except Exception:  # noqa: BLE001 — advisory count only; the probe detail is authoritative
        models_discovered = 0

    return ConnectionResult(
        tested_provider=provider.name,
        tested_model=model,
        status=str(probe["status"]),
        detail=str(probe["detail"]),
        models_discovered=models_discovered,
        load_receipt={key: probe[key] for key in (
            "reason_code", "admission", "attempted", "accepted", "callback_state", "ownership"
        ) if key in probe},
    )


async def validate_config_draft(req: ValidateConfigRequest, *, settings=None) -> ValidateConfigResponse:
    """Validate a draft and optionally probe its default provider."""
    try:
        config = GoalsConfig.model_validate(req.config)
    except pydantic.ValidationError as exc:
        return ValidateConfigResponse(valid=False, field_errors=_flatten_errors(exc), connection=None)
    if not has_personal_goals(config):
        error = FieldError(loc=["research_goals"], msg="Replace the example goals with your research")
        return ValidateConfigResponse(valid=False, field_errors=[error], connection=None)

    if not req.test_connection or not config.llm_enabled:
        return ValidateConfigResponse(valid=True, field_errors=[], connection=None)

    attempt = SetupAttempt()
    try:
        connection = await _probe_connection(config, settings or get_settings(), attempt)
    except asyncio.CancelledError:
        attempt.abandon("cancelled")
        raise
    return ValidateConfigResponse(valid=True, field_errors=[], connection=connection)
