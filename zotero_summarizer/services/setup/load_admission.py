"""Setup LLM admission is evidence, not a RAM estimate or a provider declaration."""
from zotero_summarizer.models.providers import ProviderConfig

ADMISSION_REVISION = "setup-verification-v3"


def assess_setup_load(provider: ProviderConfig, model: str) -> dict:
    """No current adapter produces an authoritative effective ResourceEnvelope.

    Context, placement and memory domain must come from the executing runtime;
    neither free host RAM, num_ctx nor a profile recommendation establishes them.
    Keep safe metadata/listing separate from requests that can load local weights.
    """
    local = provider.is_local
    return {
        "status": "unknown" if local else "not_applicable",
        "hardware_safe": "unknown" if local else "not_applicable",
        "reason_code": "effective_envelope_unknown" if local else "hosted",
        "identity": {"provider": provider.model_dump(mode="json"), "model": model},
    }


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
    if admission["status"] != "unknown":
        return None
    return {
        "status": "fail", "reason_code": admission["reason_code"],
        "detail": "Local verification needs effective runtime context, placement and memory-domain "
                  "evidence (ResourceEnvelope). Model listing/profile advice is not hardware safety. "
                  "Review AI settings; choose Hosted or No LLM to verify another mode. "
                  "No local inference was submitted and no model was downloaded.",
        "attempted": False, "ownership": "not_submitted", "hardware_safe": "unknown",
        "admission": admission,
    }
