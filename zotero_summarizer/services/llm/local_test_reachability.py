"""A local model's Test is not sent (load admission), but its server can still be asked
for its catalogue — metadata only, no inference, nothing loaded. The catalogue answer is
advisory and never alters the admission verdict's reason_code or receipt."""
from __future__ import annotations

import re

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.llm import model_list

_NEEDS_APPROVAL = {"provider_approval_required", "connection_conflict", "configuration_busy"}


def with_reachability(refusal: dict, provider, model: str) -> dict:
    """Add advisory ``reachable`` / ``model_listed`` to an already-decided refusal.

    The memory-safety verdict is final before this runs: it only asks the provider's
    catalogue (no inference, nothing loaded) and never touches ``reason_code`` or the
    rest of the receipt. Any failure here is advisory noise, so it degrades to
    ``reachable`` state and must never overwrite the refusal.
    """
    where = provider.base_url or provider.name
    try:
        listed = model in model_list.list_models_for_provider(provider)
    except APIError as error:
        if error.error in _NEEDS_APPROVAL:
            return refusal
        return {**refusal, "reachable": False,
                "detail": f"Cannot connect to {where} ({re.sub(r'^\[Errno \d+\] ', '', error.message.rsplit(': ', 1)[-1])})."}
    except Exception:  # noqa: BLE001 — reachability is advisory; it never changes the verdict
        return {**refusal, "reachable": None}
    if "evidence" in refusal.get("admission", {}):  # a memory verdict: its own detail has the numbers
        return {**refusal, "reachable": True, "model_listed": listed}
    return {**refusal, "reachable": True, "model_listed": listed,
            "detail": f"{where} answered and {'lists' if listed else 'does not list'} {model}. "
                      "No test prompt was sent: the app only sends test prompts to local models it started itself."}
