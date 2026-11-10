"""Processing-boundary consent for the Today AI card (Todo #92).

Reuses the EXISTING per-stage processing-boundary token (``ResolvedStage.
fingerprint()`` — the same mechanism ``deep_review``/related assessment use):
consent is the owner accepting the CURRENT resolved ``card`` stage (provider +
model + options). The accepted token is persisted so background generation and
the read endpoint agree without a request in hand. Any change to the resolved
stage invalidates the stored token (``has_consent`` compares against the live
boundary), exactly like ``processing_boundary_changed`` at call time.

These are operational knobs: the card model is deliberately OUTSIDE the deep-
review identity allowlist (#88) — a card-model change must never stale a review.
"""
from __future__ import annotations

import json
import time
from typing import Any

from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.services._common import LOGGER, settings, state
from zotero_summarizer.settings import offline_requested

STAGE = "card"


def card_boundary() -> dict[str, Any]:
    """The live resolved ``card`` processing boundary + its consent token. Reading
    the config/token never touches a credential value (``fingerprint`` is content-
    only)."""
    config = state().app_state.config
    resolved = resolve_stage(config.llm_routing, STAGE).model_copy(deep=True)
    enabled = bool(config.llm_enabled) and not offline_requested()
    return {
        "provider": resolved.provider.name, "model": resolved.model,
        "endpoint": resolved.provider.base_url, "local": resolved.provider.is_local,
        "token": resolved.fingerprint(), "enabled": enabled,
    }


def _store_path():
    return settings().card_dir / "consent.json"


def record_consent(token: str) -> dict[str, Any]:
    """Persist the owner's acceptance of the CURRENT card boundary. Rejects a token
    that does not match the live boundary (the UI reloaded a stale model)."""
    boundary = card_boundary()
    if not token or token != boundary["token"]:
        from zotero_summarizer.api.errors import APIError

        raise APIError("processing_boundary_changed",
                       "Card model changed; reload before consenting", status_code=409)
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"token": token, "recorded_at": int(time.time())}))
    except OSError as exc:
        LOGGER.info("card consent: write failed: %s", exc)
    return boundary


def stored_token() -> str | None:
    try:
        return str(json.loads(_store_path().read_text()).get("token") or "") or None
    except (OSError, json.JSONDecodeError):
        return None


def has_consent(boundary: dict[str, Any] | None = None) -> bool:
    """True only when the stored token matches the live boundary AND the boundary is
    usable (AI enabled, online)."""
    boundary = boundary or card_boundary()
    return bool(boundary["enabled"]) and stored_token() == boundary["token"]


def unavailable_reason(boundary: dict[str, Any] | None = None) -> str:
    """Why an AI card cannot be produced right now (facts-only reason string)."""
    config = state().app_state.config
    if not bool(config.llm_enabled):
        return "ai_disabled"
    if offline_requested():
        return "offline"
    if not has_consent(boundary):
        return "consent_required"
    return "ok"


__all__ = ["card_boundary", "has_consent", "record_consent", "stored_token", "unavailable_reason"]
