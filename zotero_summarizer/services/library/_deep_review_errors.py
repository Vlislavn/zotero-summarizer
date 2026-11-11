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


def _provider_recovery(reason_code: str, status: int | None) -> str | None:
    """The headline for a provider-side failure, in plain words, from its reason code."""
    if reason_code == 'inference_timeout':
        return 'The AI model did not answer in time. Retry.'
    if reason_code == 'inference_transport_error':
        return 'The AI provider dropped the connection. Retry.'
    if reason_code == 'inference_http_error' and status is not None:
        if status in {401, 403}:
            return f'The AI provider rejected the request (HTTP {status}). Check the AI connection in Settings.'
        return f'The AI provider returned an error (HTTP {status}). Retry later.'
    return None


def _unwrapped_provider_failure(exc: BaseException) -> dict[str, Any] | None:
    """Timeout / dropped connection / HTTP error that reached the job without passing the observed
    client (which types them itself), found along the exception's cause chain."""
    from zotero_summarizer.services.llm.probe_failure import failure_observation

    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            return None
        observation = failure_observation(seen, {'invalid_events': False, 'coherent': False})
        recovery = _provider_recovery(observation['reason_code'], observation.get('http_status'))
        if recovery is not None:
            return {'code': 'review_provider_unavailable', 'stage': str(getattr(exc, 'review_stage', 'review')),
                    'reason_code': observation['reason_code'], 'recovery': recovery,
                    **({'http_status': observation['http_status']} if 'http_status' in observation else {})}
        seen = seen.__cause__ or seen.__context__
    return None


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
        if exc.error == 'review_provider_unavailable' and isinstance(exc.details.get('reason_code'), str):
            diagnostic['reason_code'] = exc.details['reason_code']
            diagnostic['recovery'] = _provider_recovery(
                diagnostic['reason_code'], diagnostic.get('http_status')) or diagnostic['recovery']
        return diagnostic
    from zotero_summarizer.integrations.llm_callbacks import LLMOutputTruncated

    if isinstance(exc, LLMOutputTruncated):
        retried = getattr(exc, 'output_budget_retried', False)
        return {
            'code': 'review_output_truncated', 'stage': str(getattr(exc, 'review_stage', 'generation')),
            'reason_code': 'request_output_truncated',
            'recovery': ("The AI model's answer was cut off, even after a retry with a larger limit. "
                         "Retry; if it keeps happening, use a lower thinking effort for this model."
                         if retried else
                         "The AI model's answer was cut off and its output limit cannot be raised here. "
                         "Use a lower thinking effort or a larger output budget for this model, then retry."),
        }
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
            "reason_code": exc.reason_code,
            "recovery": "The model returned an invalid digest after bounded recovery; inspect the attempt and retry",
        }
    if isinstance(exc, ValueError):
        return {
            "code": "review_validation_failed", "stage": "review",
            "recovery": "Inspect source verification and retry the review",
        }
    if (provider := _unwrapped_provider_failure(exc)) is not None:
        return provider
    return {
        "code": "review_failed", "stage": str(getattr(exc, 'review_stage', 'review')),
        "recovery": "The review stopped unexpectedly. Retry; the cause is under Technical details.",
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
