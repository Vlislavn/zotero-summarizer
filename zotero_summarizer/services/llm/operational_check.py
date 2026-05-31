"""Manual operational check: probe each pipeline stage's configured provider.

This is the user-triggered "initialize / is it operational" action — the app
always starts regardless of provider availability, and this endpoint is how the
user confirms each stage can actually reach its model. Each stage is probed
independently; a failure is captured and reported as that stage's status, never
raised — so one unreachable provider does NOT stop the app or the other stages.

Returns one row per stage: ``{stage, provider, type, model, status, detail}``
where ``status`` is ``operational`` or ``fail``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from zotero_summarizer.models.providers import STAGES, resolve_stage
from zotero_summarizer.services._common import state
from zotero_summarizer.services.llm.factory import build_client_for_stage

LOGGER = logging.getLogger("zotero_summarizer")

# Smallest useful probe: a one-word reply confirms auth + connectivity + that
# the model id is served, without spending real tokens on a paper.
_PROBE_PROMPT = "Reply with the single word: ok"

# Hard ceiling on how long a SINGLE stage's probe may keep the user waiting. A
# local model that's mid-load or an unreachable endpoint must not hang the
# "Check operational" button (the bug: one slow provider made the whole check
# take 15s+ with no feedback). On timeout the stage reports "fail: timeout" and
# the others still return — the button always answers in ~this many seconds.
_PROBE_TIMEOUT_SECS = 8.0


def _stage_skeleton(routing: Any, stage: str) -> tuple[Any, dict[str, Any]]:
    """``(resolved_stage, base_row)`` — the provider/model identity for a stage,
    shared by the probe and its timeout path so a timed-out stage still names
    which provider/model was slow."""
    resolved = resolve_stage(routing, stage)
    return resolved, {
        "stage": stage,
        "provider": resolved.provider.name,
        "type": resolved.provider.type.value,
        "model": resolved.model,
    }


def _probe_stage(routing: Any, stage: str) -> dict[str, Any]:
    resolved, row = _stage_skeleton(routing, stage)
    # Broad except is the documented per-stage boundary: the user asked for the
    # check to report a per-stage pass/fail instead of stopping the run, so every
    # failure mode (missing key, 401, connection refused, bad model) becomes a
    # "fail" row rather than propagating.
    try:
        client = build_client_for_stage(resolved)
        client.prompt(_PROBE_PROMPT)
        row["status"] = "operational"
        row["detail"] = ""
    except Exception as exc:  # noqa: BLE001 — per-stage status boundary (see above)
        LOGGER.warning("LLM operational check failed for stage=%s: %s", stage, exc)
        row["status"] = "fail"
        row["detail"] = f"{type(exc).__name__}: {exc}"
    return row


async def _probe_stage_bounded(routing: Any, stage: str) -> dict[str, Any]:
    """Run one probe in a worker thread, bounded by ``_PROBE_TIMEOUT_SECS``.

    On timeout the user-visible call returns a ``fail: timeout`` row immediately;
    the orphaned worker thread finishes on its own (the blocking ``client.prompt``
    eventually returns/errors) — acceptable for this low-frequency manual check.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_probe_stage, routing, stage),
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        _resolved, row = _stage_skeleton(routing, stage)
        row["status"] = "fail"
        row["detail"] = (
            f"timeout after {_PROBE_TIMEOUT_SECS:.0f}s — provider slow or unreachable"
        )
        LOGGER.warning(
            "LLM operational check timed out for stage=%s (>%.0fs)",
            stage, _PROBE_TIMEOUT_SECS,
        )
        return row


async def check_stages() -> dict[str, Any]:
    """Probe every stage's provider and return per-stage operational status.

    Probes run concurrently in worker threads, each bounded by
    ``_PROBE_TIMEOUT_SECS`` so one slow/unreachable provider can neither block the
    event loop nor stall the button — every stage returns a status within the
    timeout. Each probe captures its own failures, so no probe can fault the gather.
    """
    routing = state().app_state.config.llm_routing
    stages = list(
        await asyncio.gather(
            *(_probe_stage_bounded(routing, stage) for stage in STAGES)
        )
    )
    all_ok = all(row["status"] == "operational" for row in stages)
    return {"status": "ok" if all_ok else "degraded", "stages": stages}
