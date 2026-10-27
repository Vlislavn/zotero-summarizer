"""Versioned user-editable research profile and controlled taxonomy."""
from __future__ import annotations

import json
from pathlib import Path

from zotero_summarizer.models import ResearchProfile
from zotero_summarizer.services._common import write_json_atomic


DEFAULT_THEMES = [
    "agent evaluation and benchmarking", "trustworthiness calibration and uncertainty",
    "trace and trajectory analysis", "reward hacking and specification gaming",
    "tool use and environment interaction", "activation probing and mechanistic interpretability",
    "biomedical research agents and clinical agents", "long-horizon planning",
    "memory and retrieval", "verification evidence attribution and hallucination detection",
]
DEFAULT_PROJECTS = [
    "agent harness", "activation oracle", "biomedical agents",
    "trustworthiness evaluation pipeline",
]


def profile_path(data_dir: Path) -> Path:
    return data_dir / "research_feed" / "profile.json"


def load_profile(data_dir: Path, *, persist_default: bool = True) -> ResearchProfile:
    path = profile_path(data_dir)
    if path.exists():
        return ResearchProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    profile = ResearchProfile(themes=DEFAULT_THEMES, projects=DEFAULT_PROJECTS)
    if persist_default:
        write_json_atomic(path, profile.model_dump(mode="json"))
    return profile


def save_telegram(data_dir: Path, selection) -> ResearchProfile:
    from zotero_summarizer.models.research_feed import TelegramSourceSettings

    selection = TelegramSourceSettings.model_validate(selection)
    path = profile_path(data_dir)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("research profile cannot be a symlink")
    raw = (json.loads(path.read_text(encoding="utf-8")) if path.exists()
           else load_profile(data_dir, persist_default=False).model_dump(mode="json"))
    # Patch only this source's fields, retaining other intent and unknown extensions.
    raw.update(telegram_targets=[target.model_dump() for target in selection.sources],
               telegram_channels=[target.username for target in selection.sources if target.kind == "broadcast"],
               telegram_model=selection.model.model_dump() if selection.model else None,
               telegram_limits=selection.limits.model_dump())
    profile = ResearchProfile.model_validate(raw)
    write_json_atomic(path, raw)
    return profile


__all__ = ["DEFAULT_PROJECTS", "DEFAULT_THEMES", "load_profile", "profile_path", "save_telegram"]
