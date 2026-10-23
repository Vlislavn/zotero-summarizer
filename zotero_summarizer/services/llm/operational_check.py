"""Bounded real-inference and cheap reachability probes for configured stages."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from zotero_summarizer.models.providers import STAGES, LLMCapability, ProviderConfig, resolve_stage
from zotero_summarizer.services._common import state
from zotero_summarizer.services.llm.factory import build_client_for_provider
from zotero_summarizer.services.setup.load_admission import refused_local_load

LOGGER = logging.getLogger("zotero_summarizer")

_PROBE_PROMPT = "Reply with the single word: ok"
_PROBE_TIMEOUT_SECS = 30.0
_LOCAL_PROBE_TIMEOUT_SECS = 60.0


def _stage_skeleton(routing: Any, stage: str) -> tuple[Any, dict[str, Any]]:
    """Resolved identity shared by success and timeout rows."""
    resolved = resolve_stage(routing, stage)
    return resolved, {
        "stage": stage,
        "provider": resolved.provider.name,
        "type": resolved.provider.type.value,
        "model": resolved.model,
        "configured": True,
        "capable": LLMCapability.text in resolved.provider.available_capabilities,
    }


def probe_provider(provider: ProviderConfig, model: str) -> dict[str, Any]:
    """Probe one provider/model; failures are data so sibling stages still run."""
    attempted = False
    try:
        if LLMCapability.text not in provider.available_capabilities:
            return {"status": "fail", "reason_code": "unsupported_capabilities", "attempted": False,
                    "detail": "Selected provider does not declare text support"}
        refusal = refused_local_load(provider, model)
        if refusal:
            return refusal
        client = build_client_for_provider(provider, model, verification=True)
        try:
            attempted = True
            output = client.prompt(_PROBE_PROMPT)
            if not isinstance(output, str) or not output.strip():
                raise ValueError("Inference returned an empty response")
            return {"status": "operational", "detail": "", "observed_operational": True,
                    "attempted": True, "ownership": "observed_terminal", "hardware_safe": "not_applicable"}
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 — probe status boundary (see docstring)
        LOGGER.warning(
            "LLM probe failed for provider=%s model=%s: %s", provider.name, model, exc
        )
        return {"status": "fail", "detail": f"{type(exc).__name__}: {exc}",
                "reason_code": "inference_failed", "attempted": attempted,
                "ownership": "unresolved" if attempted else "not_submitted"}


def _probe_stage(routing: Any, stage: str) -> dict[str, Any]:
    resolved, row = _stage_skeleton(routing, stage)
    row.update(probe_provider(resolved.provider, resolved.model))
    return row


async def _probe_stage_bounded(routing: Any, stage: str) -> dict[str, Any]:
    """Bound one blocking provider call; its orphaned worker exits independently."""
    from zotero_summarizer.services.setup.load_attempt import current_attempt

    if current_attempt() is not None:
        # Doctor already owns a bounded waiter; keep the real request in its callback.
        return _probe_stage(routing, stage)
    resolved, _row = _stage_skeleton(routing, stage)
    timeout = (
        _LOCAL_PROBE_TIMEOUT_SECS
        if getattr(resolved, "provider", None) and resolved.provider.is_local
        else _PROBE_TIMEOUT_SECS
    )
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_probe_stage, routing, stage),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _resolved, row = _stage_skeleton(routing, stage)
        row["status"] = "fail"
        row["reason_code"] = "waiter_timeout"
        row["ownership"] = "unresolved"
        row["detail"] = f"timeout after {timeout:.0f}s — remote termination is not established"
        LOGGER.warning(
            "LLM operational check timed out for stage=%s (>%.0fs)",
            stage,
            timeout,
        )
        return row


async def check_routing_stages(routing: Any, *, should_stop=None) -> dict[str, Any]:
    """Run bounded real inference for every stage in an explicit routing config."""
    routing = routing.model_copy(deep=True)
    stages, cached = [], {}
    abandoned = False
    # ponytail: unique probes run serially to protect one local runtime; parallelize
    # only if mixed-provider health-check latency becomes a measured problem.
    for stage in STAGES:
        resolved, _row = _stage_skeleton(routing, stage)
        abandoned = abandoned or bool(should_stop and should_stop())
        key = (resolved.provider.name, resolved.model)
        if key not in cached:
            cached[key] = ({**_row, "status": "fail", "reason_code": "batch_abandoned",
                            "attempted": False, "detail": "Batch abandoned; not submitted"}
                           if abandoned else await _probe_stage_bounded(routing, stage))
            abandoned = abandoned or cached[key].get("reason_code") == "waiter_timeout"
        stages.append({**cached[key], "stage": stage})
    all_ok = all(row["status"] == "operational" for row in stages)
    return {"status": "ok" if all_ok else "degraded", "stages": stages}


async def check_stages() -> dict[str, Any]:
    """Probe every live stage; the setup doctor reuses the explicit-routing seam."""
    config = state().app_state.config
    if not getattr(config, "llm_enabled", True):
        return _disabled_stages(config.llm_routing, inference=True)
    return await check_routing_stages(config.llm_routing)


_REACH_TIMEOUT_SECS = 4.0


def _disabled_stages(routing: Any, *, inference: bool) -> dict[str, Any]:
    rows = []
    for stage in STAGES:
        resolved, row = _stage_skeleton(routing, stage)
        row.update(
            {
                "enabled": False,
                "base_url": resolved.provider.base_url or "",
                "detail": "AI features are disabled by choice",
            }
        )
        row["status" if inference else "reachable"] = "disabled" if inference else False
        rows.append(row)
    return {"status": "disabled", "stages": rows}


def _reach_stage(routing: Any, stage: str) -> dict[str, Any]:
    from zotero_summarizer.services.llm import model_list

    resolved, row = _stage_skeleton(routing, stage)
    row["base_url"] = resolved.provider.base_url or ""
    try:
        model_list.list_models_for_provider(resolved.provider)
        row["reachable"] = True
        row["detail"] = ""
    except Exception as exc:  # noqa: BLE001 — per-stage status boundary (see _probe_stage)
        LOGGER.warning("LLM reachability check failed for stage=%s: %s", stage, exc)
        row["reachable"] = False
        row["detail"] = f"{type(exc).__name__}: {exc}"
    return row


async def _reach_stage_bounded(routing: Any, stage: str) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_reach_stage, routing, stage),
            timeout=_REACH_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        _resolved, row = _stage_skeleton(routing, stage)
        row["base_url"] = _resolved.provider.base_url or ""
        row["reachable"] = False
        row["detail"] = (
            f"timeout after {_REACH_TIMEOUT_SECS:.0f}s — endpoint slow or unreachable"
        )
        return row


async def check_reachability() -> dict[str, Any]:
    """Cheap per-stage ``GET /models`` reachability, with no token spend."""
    config = state().app_state.config
    routing = config.llm_routing
    if not getattr(config, "llm_enabled", True):
        return _disabled_stages(routing, inference=False)
    stages = list(
        await asyncio.gather(
            *(_reach_stage_bounded(routing, stage) for stage in STAGES)
        )
    )
    all_ok = all(row["reachable"] for row in stages)
    return {"status": "ok" if all_ok else "degraded", "stages": stages}
