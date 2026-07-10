"""``ZS_*`` environment overrides for the **system-owned** config knobs.

Tesler's Law: the human authors only intent (``goals.yaml`` — see
``models.config.USER_OWNED_KEYS``); every other ``GoalsConfig`` value is a validated
code default. This module is the single, drift-free escape hatch that lets a power
user override one of those defaults without editing code.

The registry is AUTO-DERIVED from ``GoalsConfig``'s non-user-owned sections, so a new
scalar field automatically gets a ``ZS_<SECTION>_<FIELD>`` override and a row in
``docs/overrides.md`` (a test asserts the doc matches the registry — no manual drift).

Precedence: code default < ``goals.yaml`` < ``data/calibration.json`` (Phase 3) < ``ZS_*`` env.
"""
from __future__ import annotations

import json
import os
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.config import USER_OWNED_KEYS

# Non-scalar sections with no env override — edit goals.yaml (prompts are
# multi-line; the Phase-5 prompt-variant mechanism is the real lever).
_SKIP_SECTIONS: frozenset[str] = frozenset({"prompts"})

# Back-compat names for knobs that already shipped a documented ``ZS_*`` var; the
# existing dynamic readers (services/_common.band_primary_enabled,
# services/library/deep_review_prewarm) keep working — env wins either way.
_ENV_ALIASES: dict[str, str] = {
    "quality_review.quality_band_primary": "ZS_QUALITY_BAND_PRIMARY",
    "quality_review.rank_quality_first": "ZS_RANK_QUALITY_FIRST",
    "quality_review.rank_interleave": "ZS_RANK_INTERLEAVE",
    "quality_review.prewarm_on_startup_k": "ZS_DEEP_REVIEW_PREWARM_K",
    "quality_review.auto_quality_gate": "ZS_AUTO_QUALITY_GATE",
    "quality_review.auto_quality_llm_floor": "ZS_AUTO_QUALITY_LLM_FLOOR",
    "quality_review.auto_quality_hide_grades": "ZS_AUTO_QUALITY_HIDE_GRADES",
    "quality_review.auto_quality_hide_bands": "ZS_AUTO_QUALITY_HIDE_BANDS",
}


def _as_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _cast_for(annotation: Any) -> Callable[[str], Any] | None:
    """String->value caster for a field annotation, or None when the field is not
    env-overridable (non-scalar). ``bool`` is checked before ``int`` (bool ⊂ int).
    ``tuple[str, ...]`` is cast as a list (pydantic re-validates list→tuple)."""
    origin = typing.get_origin(annotation)
    if origin in (list, typing.List, tuple, typing.Tuple):
        return _as_list
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _cast_for(args[0]) if len(args) == 1 else None
    if annotation is bool:
        return _as_bool
    if annotation is int:
        return int
    if annotation is float:
        return float
    if annotation is str:
        return str
    return None


@dataclass(frozen=True)
class EnvOverride:
    env: str       # ZS_* variable name
    path: str      # dotted config path, e.g. "quality_review.max_text_chars"
    cast: Callable[[str], Any]
    default: Any   # the code default (for docs)


def _build_registry() -> list[EnvOverride]:
    registry: list[EnvOverride] = []
    for section, field in GoalsConfig.model_fields.items():
        if section in USER_OWNED_KEYS or section in _SKIP_SECTIONS:
            continue
        model = field.annotation
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        for fname, finfo in model.model_fields.items():
            cast = _cast_for(finfo.annotation)
            if cast is None:
                continue
            path = f"{section}.{fname}"
            env = _ENV_ALIASES.get(path, f"ZS_{section}_{fname}".upper())
            default = finfo.default_factory() if finfo.default_factory else finfo.default
            registry.append(EnvOverride(env=env, path=path, cast=cast, default=default))
    return registry


REGISTRY: list[EnvOverride] = _build_registry()


def _set_dotted(data: dict[str, Any], path: str, value: Any) -> None:
    section, key = path.split(".", 1)
    data.setdefault(section, {})[key] = value


def apply_calibration(config: GoalsConfig, calibration_path: Path) -> GoalsConfig:
    """Apply per-user calibration (``data/calibration.json``) onto ``config`` and
    RE-VALIDATE. Precedence: code default < goals.yaml < calibration < ZS_* env — so
    calibration is applied BEFORE env (env still wins). Absence of the file is the
    Tier-0 contract (code defaults are the floor), NOT an error. Envelope:
    ``{"version": 1, "updated_at": ..., "entries": {"<dotted.path>": {"value": V, ...receipt}}}``.
    Each entry carries its receipt (harness/metric/run/ts); only ``value`` is applied."""
    if not calibration_path.exists():
        return config
    payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    entries = payload.get("entries", {})
    overrides = {path: entry["value"] for path, entry in entries.items()}
    if not overrides:
        return config
    data = config.model_dump(mode="python")
    for path, value in overrides.items():
        _set_dotted(data, path, value)
    return GoalsConfig.model_validate(data)


def apply_env_overrides(config: GoalsConfig) -> GoalsConfig:
    """Return ``config`` with every set ``ZS_*`` var applied and RE-VALIDATED — a bad
    env value (e.g. ``ZS_CLASSIFIER_GATE_MODEL_NAME=bogus``) fails loud, never silently."""
    overrides = {ov.path: ov.cast(os.environ[ov.env]) for ov in REGISTRY if ov.env in os.environ}
    if not overrides:
        return config
    data = config.model_dump(mode="python")
    for path, value in overrides.items():
        _set_dotted(data, path, value)
    return GoalsConfig.model_validate(data)


def _type_label(cast: Callable[[str], Any]) -> str:
    return {
        _as_bool: "bool",
        _as_list: "list[str]",
        int: "int",
        float: "float",
        str: "str",
    }.get(cast, "str")


def _fmt_default(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "(empty)"
    if value == "":
        return "(empty)"
    return str(value)


def render_overrides_doc() -> str:
    """Markdown for ``docs/overrides.md``, generated from ``REGISTRY`` (single source
    of truth). ``tools/`` regenerates it; a test asserts the committed file matches."""
    lines = [
        "# Environment overrides (`ZS_*`)",
        "",
        "Operator escape hatch for the **system-owned** config knobs. A human edits only",
        "intent (`goals.yaml`); these override a validated code default without touching code.",
        "",
        "Precedence: **code default < `goals.yaml` < `data/calibration.json` < `ZS_*` env**.",
        "",
        "> Generated from `zotero_summarizer/services/config_overrides.py` — do not edit by hand.",
        "> A test asserts this file matches the registry.",
        "",
        "| Env var | Config path | Type | Default |",
        "|---|---|---|---|",
    ]
    for ov in REGISTRY:
        lines.append(f"| `{ov.env}` | `{ov.path}` | {_type_label(ov.cast)} | {_fmt_default(ov.default)} |")
    return "\n".join(lines) + "\n"
