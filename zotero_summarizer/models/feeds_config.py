"""Feed-daemon configuration section (``feeds.*``), split from ``config.py``.

System-owned (validated code defaults + ``ZS_FEEDS_*`` env overrides). Before
this section existed, ``feeds.*`` was raw-dict passthrough from goals.yaml —
unvalidated, undocumented, and (because ``GoalsConfig`` ignored the unknown
key) impossible to actually set. Every knob below is read by
``services/triage/feeds`` (``_tick.py`` / ``_loop.py`` / ``_daily.py``).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

__all__ = ["FeedsConfig"]


class FeedsConfig(BaseModel):
    """Feed-daemon knobs: tick cadence, RSS refresh rotation, read-marking, outcomes."""

    daemon_tick_seconds: int = Field(default=300, ge=10)
    daemon_batch_size: int = Field(default=5, ge=1)
    # RSS refresh pass size. Feeds are refreshed least-recently-fetched-first
    # (rotation), so a bounded pass still covers EVERY enabled feed across
    # successive ticks — the old alphabetical slice starved feeds 11..N forever.
    max_feeds_per_pass: int = Field(default=10, ge=1)
    max_new_items_per_feed: int = Field(default=25, ge=1)
    per_feed_timeout_secs: float = Field(default=10.0, ge=1.0, le=300.0)
    mark_processed_as_read: bool = Field(default=True)
    # Reconcile app-side read state back into Zotero's unread badge (guid match
    # against unread feedItems). Zotero is an optional adapter: when its data
    # dir is absent the sync is skipped for that tick.
    zotero_read_sync: bool = Field(default=True)
    dedup_against_library: bool = Field(default=True)
    # None = follow dedup_against_library (legacy dependent default).
    dedup_against_processed: Optional[bool] = None
    outcome_check_per_tick: int = Field(default=3, ge=0)
    outcome_window_days: int = Field(default=7, ge=0)
    exclude_feeds: List[str] = Field(default_factory=list)
    # "HH:MM" local time-of-day trigger for daily selection; empty = interval mode.
    daily_selection_at: str = Field(default="")
    daily_selection_interval_hours: int = Field(default=24, ge=0)
    daily_target_min: int = Field(default=1, ge=0)
    daily_target_max: int = Field(default=2, ge=0)
    daily_window_hours: int = Field(default=24, ge=1)
    inbox_collection_name: str = Field(default="Inbox", min_length=1)
    daily_force_black_swan_every_run: bool = Field(default=False)
