"""Human-facing AI-service presets compiled to the existing provider model."""

from __future__ import annotations

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.providers import ProviderConfig

PROVIDER_PRESET_REGISTRY = {
    "openrouter": (
        "OpenRouter",
        "openai",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        True,
    ),
    "openai": ("OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY", True),
    "anthropic": ("Anthropic", "anthropic", None, "ANTHROPIC_API_KEY", True),
    "gemini": (
        "Gemini",
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        True,
    ),
    "groq": ("Groq", "openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY", True),
    "together": (
        "Together",
        "openai",
        "https://api.together.xyz/v1",
        "TOGETHER_API_KEY",
        True,
    ),
    "local": (
        "Local model",
        "openai",
        "http://localhost:11434/v1",
        "OLLAMA_API_KEY",
        False,
    ),
    "custom": ("Custom", None, None, None, False),
}


def _compile_preset(preset_id: str) -> ProviderConfig:
    row = PROVIDER_PRESET_REGISTRY.get(preset_id)
    if row is None or preset_id == "custom":
        raise APIError(
            error="unknown_provider_preset",
            message=f"Unknown compilable provider preset: {preset_id}",
            status_code=422,
        )
    _label, provider_type, base_url, api_key_env, _requires_key = row
    return ProviderConfig(
        name=preset_id, type=provider_type, base_url=base_url, api_key_env=api_key_env
    )


def list_presets() -> list[dict[str, object]]:
    from zotero_summarizer.services.llm.credentials import _credential_status

    result = []
    for preset_id, (
        label,
        _type,
        _url,
        key_name,
        requires_key,
    ) in PROVIDER_PRESET_REGISTRY.items():
        provider = (
            None
            if preset_id == "custom"
            else _compile_preset(preset_id).model_dump(mode="json")
        )
        status = (
            _credential_status(key_name)
            if key_name and requires_key
            else {
                "name": key_name,
                "present": preset_id == "local",
                "source": "local" if preset_id == "local" else None,
            }
        )
        result.append(
            {
                "id": preset_id,
                "label": label,
                "recommended": preset_id == "openrouter",
                "requires_key": requires_key,
                "provider": provider,
                "credential": status,
            }
        )
    return result
