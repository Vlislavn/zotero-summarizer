"""Job-level error summarisation for the deep-review background job.

Extracted from ``deep_review`` (LOC budget) — pure string helpers, no module
state. ``summarize_errors`` builds the message shown when EVERY item in a run
raised; it names the endpoint as a likely cause ONLY for connectivity-looking
errors, so a parse/validation error (the endpoint responded with a bad body)
isn't mislabelled "unreachable" — which once sent debugging down the wrong path.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library._digest_verification import DigestSourceRejected, DigestVerifierUnavailable


def diagnostic_for_exception(exc: Exception) -> dict[str, Any]:
    """Project exception identity, never model prose or arbitrary sensitive details."""
    if isinstance(exc, APIError):
        diagnostic = {
            "code": exc.error,
            "stage": str(exc.details.get("stage") or "review"),
            "recovery": str(exc.details.get("recovery") or "Inspect the source and retry"),
        }
        status = exc.details.get('http_status')
        if type(status) is int and 100 <= status <= 599:
            diagnostic['http_status'] = status
        return diagnostic
    if isinstance(exc, DigestVerifierUnavailable):
        return {
            "code": "digest_verifier_unavailable", "stage": "verification",
            "recovery": "Restore the verifier and retry; no digest was published",
        }
    if isinstance(exc, DigestSourceRejected):
        return {
            "code": "digest_source_rejected", "stage": "verification",
            "recovery": "The bounded correction failed; inspect the supplied source and retry",
        }
    from zotero_summarizer.services.library.quality_review import DigestGenerationFailed

    if isinstance(exc, DigestGenerationFailed):
        return {
            "code": "digest_generation_failed", "stage": "generation",
            "recovery": "The model returned an invalid digest after bounded recovery; inspect the attempt and retry",
        }
    if isinstance(exc, ValueError):
        return {
            "code": "review_validation_failed", "stage": "review",
            "recovery": "Inspect source verification and retry the review",
        }
    return {
        "code": "review_failed", "stage": str(getattr(exc, 'review_stage', 'review')),
        "recovery": "Inspect the attempt error and retry",
    }

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


def failure_fields(exc: Exception, provider: Any, *, phase: str | None = None) -> dict[str, Any]:
    from zotero_summarizer.services.library._review_attempt import record

    diagnostic = diagnostic_for_exception(exc)
    if phase and diagnostic['stage'] == 'review' and not isinstance(exc, APIError):
        diagnostic['stage'] = phase
        if phase == 'extract':
            diagnostic.update(code='extraction_failed', recovery='Inspect source extraction and retry')
    record('terminal', diagnostic, kind='failure_identity')
    return {
        'error': summarize_errors([f'{type(exc).__name__}: {exc}'], provider),
        'diagnostic': diagnostic,
    }


__all__ = ["summarize_errors", "diagnostic_for_exception", "failure_fields"]
