"""Setup LLM admission is evidence, not a RAM estimate or a provider declaration."""
import json
import re

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.models.setup import EffectiveLoadEnvelope, SetupLoadApprovalPolicy
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.setup._load_policy import policy_file_signature, read_approval_policy
from zotero_summarizer.settings import Settings

ADMISSION_REVISION = "setup-verification-v4"


def policy_configuration_signature(configuration: Settings) -> str:
    return ADMISSION_REVISION + ":" + policy_file_signature(configuration)


def _observe_executing_runtime(provider: ProviderConfig, model: str) -> dict:
    """Private synchronous producer seam: fresh, identity-bound runtime evidence.

    No authoritative producer is integrated. Never substitute host RAM, num_ctx,
    inventory or the policy's envelope. A future producer must sample the actual
    executing runtime and available bytes in that exact envelope's memory domain.
    """
    return {"effective_envelope": None, "available_bytes": None}


def _matches_identity(observed: dict, expected: dict) -> bool:
    return json.dumps(observed, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)


def _runtime_decision(provider: ProviderConfig, model: str, policy: SetupLoadApprovalPolicy,
                      identity: dict) -> tuple[str, str]:
    try:
        observation = _observe_executing_runtime(provider, model)
        available = observation.get("available_bytes")
        if available is not None and (type(available) is not int or available < 0):
            return "error", "telemetry_failed"
        envelope = observation.get("effective_envelope")
        if envelope is None:
            return "unknown", "effective_envelope_unknown"
        effective = EffectiveLoadEnvelope.model_validate(envelope)
        if not _matches_identity(observation.get("identity"), identity):
            return "blocked", "runtime_identity_mismatch"
        if effective != policy.effective_envelope:
            return "blocked", "policy_envelope_mismatch"
        if available is None:
            return "unknown", "telemetry_unavailable"
        if available < policy.peak_working_set_bytes + policy.reserved_system_bytes:
            return "blocked", "available_memory_insufficient"
        return "admitted", "policy_satisfied"
    except Exception:  # noqa: BLE001 — producer failures refuse; never expose private evidence
        return "error", "telemetry_failed"


def _local_decision(configuration: Settings, provider: ProviderConfig, model: str,
                    identity: dict) -> tuple[str, str]:
    if configuration.setup_load_approval_policy_path is None:
        if configuration.setup_load_approved_policy_sha256 is not None:
            return "error", "policy_configuration_error"
        return "unknown", "effective_envelope_unknown"
    try:
        policy, digest = read_approval_policy(configuration)
    except (OSError, ValueError, TypeError, RecursionError):
        return "error", "policy_configuration_error"
    approval = configuration.setup_load_approved_policy_sha256
    if approval is None:
        return "blocked", "policy_unbound"
    if not isinstance(approval, str) or re.fullmatch(r"[0-9a-f]{64}", approval) is None:
        return "error", "policy_configuration_error"
    if approval != digest:
        return "blocked", "policy_binding_mismatch"
    if not _matches_identity(policy.identity.model_dump(mode="json"), identity):
        return "blocked", "policy_identity_mismatch"
    return _runtime_decision(provider, model, policy, identity)


def assess_setup_load(provider: ProviderConfig, model: str) -> dict:
    """Read explicit approval and fresh runtime evidence before any local client.

    Policy satisfaction is machine wiring, not a physical hardware certificate.
    With no authoritative executing-runtime producer, local loads remain UNKNOWN.
    """
    identity = {"provider": provider.model_dump(mode="json"), "model": model}
    status, reason = (_local_decision(settings(), provider, model, identity) if provider.is_local
                      else ("not_applicable", "hosted"))
    return {"status": status, "reason_code": reason, "identity": identity,
            "hardware_safe": "unknown" if provider.is_local else "not_applicable"}


def verification_row(results: list[dict]) -> dict:
    from zotero_summarizer.services.setup.doctor_environment import _row

    grouped = {}
    for result in results:
        key = (result["provider"], result["model"], result.get("detail") or result["status"])
        grouped.setdefault(key, []).append(result["stage"])
    detail = "; ".join(f"{p}/{m} ({', '.join(stages)}): {outcome}"
                       for (p, m, outcome), stages in grouped.items())
    failed = [row for row in results if row["status"] != "operational"]
    guarded = any(row.get("reason_code") for row in failed)
    row = _row("llm_inference", "needs_action" if failed else "ready",
               "Inference failed for a configured stage" if failed else "Every LLM stage returned a real response",
               detail, action="Review AI settings" if guarded else "Retry inference")
    if guarded:
        row["recovery"]["action"] = "open_ai_settings"
    row["load_receipts"] = results
    return row


def refused_local_load(provider: ProviderConfig, model: str) -> dict | None:
    admission = assess_setup_load(provider, model)
    if admission["status"] in {"admitted", "not_applicable"}:
        return None
    return {
        "status": "fail", "reason_code": admission["reason_code"],
        "detail": f"Local verification refused ({admission['reason_code']}). It needs bound policy and "
                  "effective runtime context, placement and memory-domain evidence (ResourceEnvelope). "
                  "Model listing/profile advice is not hardware safety. "
                  "Review AI settings; choose Hosted or No LLM to verify another mode. "
                  "No local inference was submitted and no model was downloaded.",
        "attempted": False, "ownership": "not_submitted", "hardware_safe": "unknown",
        "admission": admission,
    }
