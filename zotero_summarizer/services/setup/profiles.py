"""Deployment routing and supported local-model profiles."""
from __future__ import annotations

import shutil
from typing import Any

import psutil

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.providers import LLMRoutingConfig, ProviderConfig

# ponytail: Ollama-only curated defaults; add another runtime after a real supported
# setup needs it, not merely because its OpenAI-compatible endpoint could work.
LOCAL_PROFILE_VERSION = 1
LOCAL_PROFILES: dict[str, dict[str, Any]] = {
    "light": {
        "label": "Light",
        "model": "qwen3:8b",
        "runtime": "Ollama",
        "source": "https://ollama.com/library/qwen3:8b",
        "size_gb": 5.2,
        "min_memory_gb": 12,
        "min_disk_gb": 8,
        "features": ["triage", "summaries", "Q&A", "lean deep review"],
        "tradeoff": "Fastest supported path; less capable on long, difficult reviews.",
    },
    "balanced": {
        "label": "Balanced",
        "model": "qwen3:30b",
        "runtime": "Ollama",
        "source": "https://ollama.com/library/qwen3:30b",
        "size_gb": 19,
        "min_memory_gb": 32,
        "min_disk_gb": 22,
        "features": ["triage", "summaries", "Q&A", "stronger deep review"],
        "tradeoff": "Better reasoning, with slower inference and much higher memory use.",
    },
    "existing": {
        "label": "Use existing endpoint",
        "model": None,
        "runtime": "OpenAI-compatible",
        "source": None,
        "size_gb": None,
        "min_memory_gb": 0,
        "min_disk_gb": 0,
        "features": ["uses the models already served by your endpoint"],
        "tradeoff": "You own runtime compatibility and capacity.",
    },
}

_STAGES = ("feed", "backlog", "deep_review")
_LOCAL_URL = "http://localhost:11434/v1"


def hardware_snapshot(settings: Any) -> dict[str, float]:
    """Portable RAM/free-disk facts used by both profile front-ends."""
    data_parent = settings.data_dir if settings.data_dir.exists() else settings.data_dir.parent
    return {
        "memory_gb": round(psutil.virtual_memory().total / 1024**3, 1),
        "disk_free_gb": round(shutil.disk_usage(data_parent).free / 1024**3, 1),
    }


def local_profile_catalog(settings: Any) -> dict[str, Any]:
    """Versioned recommendations enriched with this machine's compatibility."""
    hardware = hardware_snapshot(settings)
    rows = []
    for name, profile in LOCAL_PROFILES.items():
        memory_ok = hardware["memory_gb"] >= profile["min_memory_gb"]
        disk_ok = hardware["disk_free_gb"] >= profile["min_disk_gb"]
        reasons = []
        if not memory_ok:
            reasons.append(f"needs {profile['min_memory_gb']} GB memory")
        if not disk_ok:
            reasons.append(f"needs {profile['min_disk_gb']} GB free disk")
        row = {"id": name, **profile, "compatible": memory_ok and disk_ok}
        row["provider"] = _local_provider(_LOCAL_URL).model_dump(mode="json")
        row["compatibility_detail"] = "; ".join(reasons) or "Compatible with detected hardware"
        row["pull_command"] = f"ollama pull {profile['model']}" if profile["model"] else None
        rows.append(row)
    return {"version": LOCAL_PROFILE_VERSION, "hardware": hardware, "profiles": rows}


def _local_provider(endpoint: str) -> ProviderConfig:
    return ProviderConfig(
        name="local",
        base_url=endpoint,
        api_key_env="OLLAMA_API_KEY",
        max_tokens=8192,
        thinking_effort="medium",
        lean_deep_review=True,
        structured_output=True,
    )


def apply_local_profile(
    config: GoalsConfig,
    profile_name: str,
    *,
    endpoint: str = _LOCAL_URL,
    model: str | None = None,
) -> GoalsConfig:
    """Resolve one local profile into the existing provider/stage configuration."""
    profile = LOCAL_PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(f"unknown local profile {profile_name!r}")
    chosen_model = str(model or profile["model"] or "").strip()
    if not chosen_model:
        raise ValueError("the existing-endpoint profile needs a model")
    provider = _local_provider(endpoint)
    raw = config.llm_routing.model_dump(mode="python")
    raw["providers"] = [
        p for p in raw["providers"] if p["name"] != provider.name
    ] + [provider.model_dump(mode="python")]
    raw["default"] = {"provider": provider.name, "model": chosen_model}
    for stage in _STAGES:
        raw[stage] = {"provider": provider.name, "model": chosen_model}
    routing = LLMRoutingConfig.model_validate(raw)
    return config.model_copy(update={"llm_routing": routing})


def set_local_profile(
    settings: Any,
    profile_name: str,
    *,
    endpoint: str = _LOCAL_URL,
    model: str | None = None,
) -> dict[str, Any]:
    """Persist the shared local-profile resolution used by the CLI."""
    from zotero_summarizer.services._common import read_config, write_user_config

    if profile_name not in LOCAL_PROFILES:
        raise ValueError(f"unknown local profile {profile_name!r}")
    catalog = local_profile_catalog(settings)
    row = next(p for p in catalog["profiles"] if p["id"] == profile_name)
    if not row["compatible"]:
        raise ValueError(row["compatibility_detail"])
    updated = apply_local_profile(
        read_config(settings.config_path, settings.calibration_path),
        profile_name,
        endpoint=endpoint,
        model=model,
    )
    write_user_config(settings.config_path, updated)
    resolved_model = updated.llm_routing.default.model
    return {
        "status": "configured",
        "profile": profile_name,
        "provider": "local",
        "model": resolved_model,
        "endpoint": endpoint,
        "source": row["source"],
        "size_gb": row["size_gb"],
        "pull_command": f"ollama pull {resolved_model}" if profile_name != "existing" else None,
    }
