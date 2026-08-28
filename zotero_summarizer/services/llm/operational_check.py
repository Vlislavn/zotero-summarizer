"""Bounded real-inference and cheap reachability probes for configured stages."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from zotero_summarizer.models.providers import STAGES, ProviderConfig, resolve_stage
from zotero_summarizer.services._common import state
from zotero_summarizer.services.llm.factory import build_client_for_provider

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
    }


def probe_provider(provider: ProviderConfig, model: str) -> dict[str, Any]:
    """Probe one provider/model; failures are data so sibling stages still run."""
    try:
        client = build_client_for_provider(provider, model)
        client.prompt(_PROBE_PROMPT)
        return {"status": "operational", "detail": ""}
    except Exception as exc:  # noqa: BLE001 — probe status boundary (see docstring)
        LOGGER.warning("LLM probe failed for provider=%s model=%s: %s", provider.name, model, exc)
        return {"status": "fail", "detail": f"{type(exc).__name__}: {exc}"}


def _probe_stage(routing: Any, stage: str) -> dict[str, Any]:
    resolved, row = _stage_skeleton(routing, stage)
    row.update(probe_provider(resolved.provider, resolved.model))
    return row


async def _probe_stage_bounded(routing: Any, stage: str) -> dict[str, Any]:
    """Bound one blocking provider call; its orphaned worker exits independently."""
    resolved, _row = _stage_skeleton(routing, stage)
    timeout = _LOCAL_PROBE_TIMEOUT_SECS if getattr(resolved, "provider", None) and resolved.provider.is_local else _PROBE_TIMEOUT_SECS
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_probe_stage, routing, stage),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _resolved, row = _stage_skeleton(routing, stage)
        row["status"] = "fail"
        row["detail"] = f"timeout after {timeout:.0f}s — provider slow or unreachable"
        LOGGER.warning(
            "LLM operational check timed out for stage=%s (>%.0fs)",
            stage, timeout,
        )
        return row


async def check_routing_stages(routing: Any) -> dict[str, Any]:
    """Run bounded real inference for every stage in an explicit routing config."""
    stages, cached = [], {}
    # ponytail: unique probes run serially to protect one local runtime; parallelize
    # only if mixed-provider health-check latency becomes a measured problem.
    for stage in STAGES:
        resolved, _row = _stage_skeleton(routing, stage)
        key = (resolved.provider.name, resolved.model)
        if key not in cached:
            cached[key] = await _probe_stage_bounded(routing, stage)
        stages.append({**cached[key], "stage": stage})
    all_ok = all(row["status"] == "operational" for row in stages)
    return {"status": "ok" if all_ok else "degraded", "stages": stages}


async def check_stages() -> dict[str, Any]:
    """Probe every live stage; the setup doctor reuses the explicit-routing seam."""
    return await check_routing_stages(state().app_state.config.llm_routing)


_REACH_TIMEOUT_SECS = 4.0


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
        row["detail"] = f"timeout after {_REACH_TIMEOUT_SECS:.0f}s — endpoint slow or unreachable"
        return row


async def check_reachability() -> dict[str, Any]:
    """Cheap per-stage ``GET /models`` reachability, with no token spend."""
    routing = state().app_state.config.llm_routing
    stages = list(
        await asyncio.gather(
            *(_reach_stage_bounded(routing, stage) for stage in STAGES)
        )
    )
    all_ok = all(row["reachable"] for row in stages)
    return {"status": "ok" if all_ok else "degraded", "stages": stages}
