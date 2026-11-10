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
    """Only the current attempt's owned native runtime can produce live evidence.

    Ordinary/external daemons remain UNKNOWN. Inventory, requested options and
    host RAM alone are never an execution envelope.
    """
    from zotero_summarizer.services.setup.load_attempt import current_attempt

    attempt = current_attempt()
    return (attempt.observe_runtime(provider, model) if attempt is not None
            else {"effective_envelope": None, "available_bytes": None})


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


def assess_setup_load(provider: ProviderConfig, model: str, *, memory_probe: bool = False) -> dict:
    """Read explicit approval and fresh runtime evidence before any local client.

    Policy satisfaction is machine wiring, not a physical hardware certificate.
    Without the attempt's owned executing-runtime evidence, local loads remain UNKNOWN,
    except a single short Test (``memory_probe``) with no policy configured, which a
    fresh memory reading may admit (``local_memory_admission``). A configured policy wins.
    """
    identity = {"provider": provider.model_dump(mode="json"), "model": model}
    hardware = "unknown" if provider.is_local else "not_applicable"
    configuration = settings()
    if (memory_probe and provider.is_local and configuration.setup_load_approval_policy_path is None
            and configuration.setup_load_approved_policy_sha256 is None):
        from zotero_summarizer.services.setup import local_memory_admission

        return {**local_memory_admission.assess(provider, model), "identity": identity, "hardware_safe": hardware}
    status, reason = (_local_decision(configuration, provider, model, identity) if provider.is_local
                      else ("not_applicable", "hosted"))
    return {"status": status, "reason_code": reason, "identity": identity, "hardware_safe": hardware}


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
    needs_approval = any(result.get('status') == 'needs_approval' for result in failed)
    message = ('Server approval required before verification' if needs_approval else
               'Inference failed for a configured stage' if failed else 'Every LLM stage returned a real response')
    row = _row("llm_inference", "needs_action" if failed else "ready",
               message,
               detail, action="Review AI settings" if guarded else "Retry inference")
    if guarded:
        row["recovery"]["action"] = "open_ai_settings"
    row["load_receipts"] = results
    return row


def refused_local_load(provider: ProviderConfig, model: str, *, memory_probe: bool = False) -> dict | None:
    return local_refusal(assess_setup_load(provider, model, memory_probe=memory_probe))


def local_refusal(admission: dict) -> dict | None:
    """The refusal receipt for an assessed admission, or None when it may proceed."""
    if admission["status"] in {"admitted", "not_applicable"}:
        return None
    detail = (f"Local test not sent ({admission['reason_code']}): {admission['message']}. "
              "No prompt was sent and no model was loaded." if "message" in admission else
              f"Local test not sent ({admission['reason_code']}): the app only sends test prompts to local "
              "models it started itself, because loading a model can exhaust memory. "
              "No prompt was sent and no model was loaded or downloaded.")
    return {
        "status": "fail", "reason_code": admission["reason_code"], "detail": detail,
        "attempted": False, "ownership": "not_submitted", "hardware_safe": "unknown",
        "admission": admission,
    }
