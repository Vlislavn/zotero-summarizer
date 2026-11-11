"""The owner's on/off switch for the weekly analysis; on by default.

A tiny file (not goals.yaml) so the separately running feed daemon sees a change on
its next tick without a restart. ``ZS_FEEDBACK_SCHEDULE_ENABLED=false`` still wins.
"""
from __future__ import annotations

import json

from zotero_summarizer.services._common import atomic_write

__all__ = ["is_enabled", "set_enabled"]


def _path(settings):
    return settings.feedback_analysis_dir / "weekly.json"


def is_enabled(settings) -> bool:
    try:
        value = json.loads(_path(settings).read_text("utf-8"))["enabled"]
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return value if type(value) is bool else True


def set_enabled(settings, enabled: bool) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, lambda tmp: tmp.write_text(json.dumps({"enabled": enabled}), "utf-8"))
