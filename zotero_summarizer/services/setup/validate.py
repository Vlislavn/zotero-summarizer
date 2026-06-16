"""Validate a GoalsConfig DRAFT (and optionally probe its default provider).

Surfaced at ``POST /api/setup/validate-config``. This is a dry-run validator for
the onboarding/Settings editor: it tells the user whether their in-progress
config parses (with per-field errors when it doesn't) and — when asked — whether
the default provider+model is reachable.

Persists NOTHING and hot-swaps NOTHING — it is read-only with respect to app
state. The live config is only ever changed via ``PUT /api/config``.
"""
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
    """Map a pydantic ValidationError to the frozen ``field_errors`` shape:
    one ``{loc, msg}`` per error, with ``loc`` as the field path."""
    return [
        FieldError(loc=list(err.get("loc", ())), msg=str(err.get("msg", "")))
        for err in exc.errors()
    ]


def _probe_connection(config: GoalsConfig) -> ConnectionResult:
    """Probe the draft's DEFAULT stage: discover its model list + a tiny prompt.

    Both the model-listing and the prompt probe degrade to a recorded status
    rather than raising — this is a user-facing "is it reachable?" check, so an
    unreachable endpoint must come back as ``status="fail"`` with a detail, not a
    500. ``models_discovered`` is 0 when the listing failed (the probe detail
    still carries the reason)."""
    resolved = resolve_stage(config.llm_routing, "deep_review")
    provider, model = resolved.provider, resolved.model

    # Reuse the single shared probe mechanism (operational_check.probe_provider),
    # the same one POST /api/admin/llm-check uses — one probe, never a second
    # divergent implementation.
    probe = operational_check.probe_provider(provider, model)

    models_discovered = 0
    # The model listing is an independent, cheaper signal (GET /models). A failure
    # here must not mask the prompt-probe result, so it is its own boundary: count
    # discovered models when reachable, 0 when not. The probe's own detail already
    # surfaces an unreachable endpoint.
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
    """Validate ``req.config`` as a ``GoalsConfig`` and, when valid AND
    ``req.test_connection``, probe its default provider.

    ``connection`` is ``null`` when the config is invalid OR ``test_connection``
    is false (per the frozen contract). Persists nothing.
    """
    try:
        config = GoalsConfig.model_validate(req.config)
    except pydantic.ValidationError as exc:
        return ValidateConfigResponse(valid=False, field_errors=_flatten_errors(exc), connection=None)

    if not req.test_connection:
        return ValidateConfigResponse(valid=True, field_errors=[], connection=None)

    # The probe makes blocking network calls; run it off the event loop so the
    # endpoint stays responsive (same pattern as the operational check).
    connection = await asyncio.to_thread(_probe_connection, config)
    return ValidateConfigResponse(valid=True, field_errors=[], connection=connection)
