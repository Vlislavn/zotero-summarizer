"""Job-level error summarisation for the deep-review background job.

Extracted from ``deep_review`` (LOC budget) — pure string helpers, no module
state. ``summarize_errors`` builds the message shown when EVERY item in a run
raised; it names the endpoint as a likely cause ONLY for connectivity-looking
errors, so a parse/validation error (the endpoint responded with a bad body)
isn't mislabelled "unreachable" — which once sent debugging down the wrong path.
"""
from __future__ import annotations

from typing import Any

# ponytail: substring heuristic, not exception-type inspection — the per-item
# error is already flattened to a string by the job. Upgrade to typed checks if
# onprem ever surfaces structured connection errors here.
_CONN_HINTS = (
    "connection", "connect ", "timeout", "timed out", "refused",
    "unreachable", "errno", "name or service", "ssl", "max retries",
)


def _looks_like_connectivity(msg: str) -> bool:
    m = msg.lower()
    return any(hint in m for hint in _CONN_HINTS)


def summarize_errors(errors: list[str], provider: Any) -> str:
    """Job-level message for a run where EVERY item raised. Dedups identical
    per-item errors and appends the "endpoint … may be unreachable" hint ONLY
    when the error actually looks like a connection failure."""
    uniq = sorted(set(errors))
    body = uniq[0] if len(uniq) == 1 else f"{errors[0]} (+{len(errors) - 1} more)"
    endpoint = getattr(provider, "base_url", None)
    suffix = (
        f" — deep_review LLM endpoint {endpoint} may be unreachable"
        if endpoint and _looks_like_connectivity(body)
        else ""
    )
    return f"All {len(errors)} item(s) failed: {body}{suffix}"


__all__ = ["summarize_errors"]
