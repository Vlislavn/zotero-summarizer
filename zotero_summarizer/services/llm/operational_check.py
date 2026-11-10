"""Bounded real-inference and cheap reachability probes for configured stages."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import STAGES, LLMCapability, ProviderConfig, resolve_stage
from zotero_summarizer.services._common import state
from zotero_summarizer.services.llm.factory import build_client_for_provider
from zotero_summarizer.services.llm.local_test_reachability import with_reachability
from zotero_summarizer.services.setup.load_admission import assess_setup_load, local_refusal
from zotero_summarizer.services.setup.local_memory_admission import REFUSALS, release_after_probe

LOGGER = logging.getLogger("zotero_summarizer")

_PROBE_PROMPT = "Reply with the single word: ok"
_PROBE_TIMEOUT_SECS = 30.0
_LOCAL_PROBE_TIMEOUT_SECS = 60.0


def probe_timeout(provider):
    return _LOCAL_PROBE_TIMEOUT_SECS if provider.is_local else _PROBE_TIMEOUT_SECS


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


def probe_provider(provider: ProviderConfig, model: str, *, memory_probe: bool = False) -> dict[str, Any]:
    """Probe one provider/model; expected requests belong to this explicit scope.

    ``memory_probe`` lets a fresh memory reading admit a local model when no approval
    policy is set. Only the serialized Settings Test (``connection_test``) passes it.
    """
    from zotero_summarizer.services.setup.load_attempt import SetupAttempt, current_attempt

    attempt = current_attempt() or SetupAttempt()
    with attempt.scope():
        return _probe_provider(provider, model, attempt, memory_probe)


def _memory_receipt(admission):
    """A memory-checked local Test keeps its reading (RAM/swap/footprint/thresholds) in the receipt."""
    return {"admission": admission} if "evidence" in admission else {}


def _probe_provider(provider, model, attempt, memory_probe=False):
    before = attempt.requests.summary()["expected"]
    admission = {}
    try:
        if LLMCapability.text not in provider.available_capabilities:
            return {"status": "fail", "reason_code": "unsupported_capabilities", "attempted": False,
                    "detail": "Selected provider does not declare text support"}
        admission = assess_setup_load(provider, model, memory_probe=memory_probe)
        refusal = local_refusal(admission)
        if refusal:
            return with_reachability(refusal, provider, model)
        client = build_client_for_provider(provider, model, verification=True,
                                            **({"memory_probe": True} if "evidence" in admission else {}))
        try:
            started = perf_counter()
            output = client.prompt(_PROBE_PROMPT)
            elapsed_ms = round((perf_counter() - started) * 1000)
            if not isinstance(output, str) or not output.strip():
                hint = (' The model thinks too long for 16 tokens.' if "evidence" in admission else '')
                raise APIError('inference_empty_response',
                               'The model returned no final text. Review the selected model and output settings; request completion is observed separately.' + hint, 502)
            completion = attempt.requests.summary(start=before)
            if not completion["successful"]:
                raise APIError('request_inventory_incomplete',
                               'No coherent native completion was observed for this request. Review the saved model and protocol.', 502)
            return {"status": "operational", "detail": "the model answered" if "evidence" in admission else "", "response": output[:200], "observed_operational": True, "elapsed_ms": elapsed_ms,
                    "attempted": True, "ownership": "completed", "request_completion": completion,
                    "hardware_safe": "unknown" if provider.is_local else "not_applicable",
                    **_memory_receipt(admission)}
        finally:
            try:
                client.close()
            finally:
                if "evidence" in admission:
                    release_after_probe(provider, model, admission)
    except Exception as exc:  # noqa: BLE001 — probe status boundary (see docstring)
        if isinstance(exc, APIError) and exc.error in {'provider_approval_required', 'connection_conflict', 'configuration_busy'}:
            return {'status': 'needs_approval', 'detail': exc.message, 'reason_code': exc.error,
                    'attempted': False, 'observed_operational': None, 'hardware_safe': 'unknown',
                    'recovery': 'open_ai_settings'}
        if isinstance(exc, APIError) and exc.error in REFUSALS:  # memory re-read just before the request
            return {'status': 'fail', 'reason_code': exc.error, 'detail': exc.message, 'attempted': False,
                    'ownership': 'not_submitted', 'hardware_safe': 'unknown'}
        LOGGER.warning(
            "LLM probe failed for provider=%s model=%s: %s", provider.name, model, type(exc).__name__
        )
        completion = attempt.requests.summary(start=before)
        from zotero_summarizer.services.llm.probe_failure import failure_observation

        return {"status": "fail", **failure_observation(exc, completion),
                "attempted": bool(completion["started"]), "request_completion": completion,
                "ownership": completion["ownership"], "recovery": "open_ai_settings", **_memory_receipt(admission)}


def _probe_stage(routing: Any, stage: str, memory_probe: bool = False) -> dict[str, Any]:
    resolved, row = _stage_skeleton(routing, stage)
    row.update(probe_provider(resolved.provider, resolved.model, **({"memory_probe": True} if memory_probe else {})))
    return row


async def _probe_stage_bounded(routing: Any, stage: str, memory_probe: bool = False) -> dict[str, Any]:
    """Bound one blocking provider call; its orphaned worker exits independently."""
    from zotero_summarizer.services.setup.load_attempt import current_attempt

    if current_attempt() is not None:
        # Doctor already owns a bounded waiter; keep the real request in its callback.
        return _probe_stage(routing, stage, memory_probe)
    resolved, _row = _stage_skeleton(routing, stage)
    timeout = probe_timeout(resolved.provider)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_probe_stage, routing, stage, memory_probe),
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


async def check_routing_stages(routing: Any, *, should_stop=None, stages=STAGES,
                               memory_probe: bool = False) -> dict[str, Any]:
    """Run bounded real inference for every stage in an explicit routing config."""
    routing = routing.model_copy(deep=True)
    selected = tuple(stages)
    rows, cached = [], {}
    abandoned = False
    # ponytail: unique probes run serially to protect one local runtime; parallelize
    # only if mixed-provider health-check latency becomes a measured problem.
    for stage in selected:
        resolved, _row = _stage_skeleton(routing, stage)
        abandoned = abandoned or bool(should_stop and should_stop())
        key = (resolved.provider.name, resolved.model)
        if key not in cached:
            cached[key] = ({**_row, "status": "fail", "reason_code": "batch_abandoned",
                            "attempted": False, "detail": "Batch abandoned; not submitted"}
                           if abandoned else await _probe_stage_bounded(routing, stage, memory_probe))
            abandoned = abandoned or (cached[key].get("reason_code") == "waiter_timeout"
                                       and (should_stop is not None or resolved.provider.is_local))
        rows.append({**cached[key], "stage": stage})
    all_ok = bool(rows) and all(row["status"] == "operational" for row in rows)
    return {"status": "ok" if all_ok else "degraded", "stages": rows}


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
        if isinstance(exc, APIError) and exc.error in {'provider_approval_required', 'connection_conflict', 'configuration_busy'}:
            row.update(reachable=None, status='needs_approval', reason_code=exc.error,
                       detail=exc.message, recovery='open_ai_settings')
            return row
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
    stages, providers = [], {}
    # Metadata belongs to the provider, not the model. Serial admission avoids
    # competing with this same check's nonwaiting configuration lock.
    for stage in STAGES:
        resolved, identity = _stage_skeleton(routing, stage)
        name = resolved.provider.name
        if name not in providers:
            providers[name] = await _reach_stage_bounded(routing, stage)
        stages.append({**providers[name], **identity})
    all_ok = all(row["reachable"] for row in stages)
    return {"status": "ok" if all_ok else "degraded", "stages": stages}
