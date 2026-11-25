"""goals.yaml load/save: validate, then layer calibration and ``ZS_*`` env (see ``config_overrides``).

Import-light on purpose (no runtime context): low-level services call ``read_config`` directly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.config import USER_KNOBS, USER_OWNED_KEYS
from zotero_summarizer.services.config_overrides import apply_calibration, apply_env_overrides


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env_placeholders(item) for key, item in value.items()}
    return value


# chars-per-token for typical English+paper prose (the standard ~4 assumption; scientific
# text runs slightly denser but 4 is the conservative planning factor).
_CHARS_PER_TOKEN = 4
# Slack on (prompt_tokens + max_tokens) so the KV cache has headroom for the response + any
# thinking phase without a hard truncation at the edge. 1.25 = 25% headroom.
_NUM_CTX_HEADROOM = 1.25


def derive_num_ctx(*, max_text_chars: int, max_tokens: int, headroom: float = _NUM_CTX_HEADROOM) -> int:
    """Context window (tokens) a LOCAL provider needs so a deep-review prompt FITS.

    ``num_ctx = ceil((max_text_chars/chars_per_token + max_tokens) * headroom)`` — large
    enough for the biggest prompt the stage sends (text budget + structured output + thinking
    headroom), small enough not to over-allocate KV-cache RAM on a memory-constrained Mac
    (ollama allocates KV for the FULL window). ollama's own default (~2–4k) silently truncates
    a 60k-char prompt; this sizes it to fit. Pure (testable); re-exported for calibration."""
    if max_text_chars <= 0 or max_tokens <= 0:
        raise ValueError("max_text_chars and max_tokens must be positive")
    prompt_tokens = max_text_chars / _CHARS_PER_TOKEN
    return int((prompt_tokens + max_tokens) * headroom)


def read_config(config_path: Path, calibration_path: Path | None = None) -> GoalsConfig:
    """Load goals.yaml → validate → apply per-user calibration → apply ``ZS_*`` env.
    Precedence: code default < goals.yaml < ``calibration.json`` < ZS_* env. Pass
    ``calibration_path`` (``settings.calibration_path``) to enable the calibration layer;
    omit it (eval/CLI tools that want the raw config) to skip it. See
    ``services/config_overrides.py``."""
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    config = GoalsConfig.model_validate(_expand_env_placeholders(raw))
    if calibration_path is not None:
        config = apply_calibration(config, calibration_path)
    config = apply_env_overrides(config)
    config = _disable_section_when_offline(config, "prestige")
    config = _disable_section_when_offline(config, "openreview")
    config = _disable_section_when_offline(config, "full_text_refine")
    return _derive_local_num_ctx(config)


def _is_offline() -> bool:
    from zotero_summarizer.settings import offline_requested

    return offline_requested()


def _disable_section_when_offline(config: GoalsConfig, section: str) -> GoalsConfig:
    """Air-gap contract: ``ZS_OFFLINE`` forces a network-dependent enrichment section
    off so triage and scoring never reach the network.
    Applied AFTER the env layer so ``ZS_OFFLINE`` beats an explicit ``ZS_*_ENABLED=1``.
    No-op when the section is already off or the app is online."""
    sec = getattr(config, section)
    if not sec.enabled or not _is_offline():
        return config
    data = config.model_dump(mode="python")
    data[section]["enabled"] = False
    return GoalsConfig.model_validate(data)


def _derive_local_num_ctx(config: GoalsConfig) -> GoalsConfig:
    """Auto-size the context window for LOCAL providers that didn't set one explicitly.

    A local ollama provider's own ``num_ctx`` default (~2–4k) silently truncates a long
    deep-review prompt; derive it to fit (``max_text_chars/4 + max_tokens``, +headroom —
    see ``derive_num_ctx``). REMOTE providers keep ``num_ctx=None`` (they size
    their own window; extra_body.num_ctx is a safe no-op there). A provider that set
    ``num_ctx`` explicitly is left alone (user/calibration override). No-op when there's no
    ``llm_routing`` (the legacy flat-llm path)."""
    routing = config.llm_routing
    if routing is None:
        return config
    qr = config.quality_review
    changed = False
    new_providers = []
    for p in routing.providers:
        if p.is_local and p.num_ctx is None:
            p = p.model_copy(update={"num_ctx": derive_num_ctx(max_text_chars=qr.max_text_chars, max_tokens=p.max_tokens)})
            changed = True
        new_providers.append(p)
    if not changed:
        return config
    return config.model_copy(update={"llm_routing": routing.model_copy(update={"providers": new_providers})})


def write_config_atomic(config_path: Path, payload: dict[str, Any]) -> None:
    tmp_path = config_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as config_file:
        yaml.safe_dump(payload, config_file, sort_keys=False, allow_unicode=False)
    tmp_path.replace(config_path)


def write_user_config(config_path: Path, config: GoalsConfig) -> None:
    """Persist ONLY the user-owned keys (intent + LLM connection + university access)
    to goals.yaml, plus the few ``USER_KNOBS`` fields when non-default. System-owned sections (corpus, prestige, quality_review, gate, …)
    are NEVER written — they stay validated code defaults, refined by calibration and
    ``ZS_*`` env. ``university_access`` is written only when non-default (off → omitted)
    so an unused section never clutters the file. See ``models.config.USER_OWNED_KEYS``."""
    full = config.model_dump(mode="json")
    lean = config.model_dump(mode="json", exclude_defaults=True)
    payload = {
        key: value
        for key, value in full.items()
        if key in USER_OWNED_KEYS and (key != "university_access" or key in lean)
    }
    for section, names in USER_KNOBS.items():
        knobs = {name: lean[section][name] for name in names if name in lean.get(section, {})}
        if knobs:
            payload[section] = knobs
    write_config_atomic(config_path, payload)
