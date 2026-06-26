from __future__ import annotations

from dataclasses import dataclass
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
