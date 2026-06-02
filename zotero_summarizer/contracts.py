from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class PendingChange:
    item_key: str
    item_title: str
    change_type: Literal[
        "tag_changes",
        "add_note",
        "add_to_collection",
        "remove_from_collection",
        "create_item_from_feed",
        "promote_from_inbox",
        "mark_feed_item_read",
    ]
    payload: dict[str, Any]


@dataclass(frozen=True)
class TriageJob:
    job_id: str
    status: str
    total: int
    completed: int = 0
    item_keys: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
