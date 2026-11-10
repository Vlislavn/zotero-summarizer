"""Saved Telegram source selection and the Telegram report directory."""
from __future__ import annotations

from zotero_summarizer.models.research_feed import TelegramTarget


def output_dir(settings):
    """Telegram report directory; a symlinked owner is rejected before any write."""
    parent = settings.data_dir / "research_feed"
    directory = parent / "telegram"
    if any(path.is_symlink() for path in (parent, directory, directory / "state.json")):
        raise ValueError("telegram output owner cannot be a symlink")
    return directory


def targets(profile):
    # Retain existing channel selections until explicitly saved through Sources.
    values = profile.telegram_targets or [TelegramTarget(kind="broadcast", username=name)
                                          for name in profile.telegram_channels]
    if len({target.key for target in values}) != len(values):
        raise ValueError("duplicate Telegram source selection")
    return values
