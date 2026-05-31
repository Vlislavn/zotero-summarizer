"""FIX 2: exclude non-paper feeds at the triage source (feeds.exclude_feeds).

The exclusion is by feed NAME (a stable, config-driven signal), applied only
when feeds are auto-resolved — never on an explicit CLI ``--feed`` selection.
Names here intentionally differ from the original audit trace (GitHub releases)
to prove the fix keys off the config list, not those specific titles."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.triage.feeds._tick import _pick_unread_batch_round_robin


class _FakeReader:
    def __init__(self, groups: list[dict[str, Any]], items: dict[int, list[dict[str, Any]]]):
        self._groups = groups
        self._items = items

    def get_feed_groups(self) -> list[dict[str, Any]]:
        return self._groups

    def get_feed_items(self, *, feed_library_id: int, unread_only: bool = True,
                       order: str = "oldest_first", limit: int | None = None):
        return list(self._items.get(int(feed_library_id), []))


_GROUPS = [
    {"library_id": 2, "name": "bioRxiv Neuroscience"},
    {"library_id": 3, "name": "Some Project Releases"},   # non-paper feed (≠ trace title)
    {"library_id": 4, "name": "arXiv cs.AI"},
]
_ITEMS = {
    2: [{"item_id": 1, "feed_library_id": 2, "title": "A paper"}],
    3: [{"item_id": 2, "feed_library_id": 3, "title": "v9.9.9 — Speedy Snake"}],
    4: [{"item_id": 3, "feed_library_id": 4, "title": "B paper"}],
}


def test_excludes_named_feed_on_auto_resolve():
    out = _pick_unread_batch_round_robin(
        _FakeReader(_GROUPS, _ITEMS),
        batch_size=None, feed_library_ids=None,
        exclude_feed_names={"some project releases"},  # casefolded match
    )
    assert {it["item_id"] for it in out} == {1, 3}   # the releases feed (id 3) is dropped


def test_no_exclusion_keeps_all_feeds():
    out = _pick_unread_batch_round_robin(
        _FakeReader(_GROUPS, _ITEMS), batch_size=None, feed_library_ids=None,
    )
    assert {it["item_id"] for it in out} == {1, 2, 3}


def test_explicit_feed_ids_bypass_exclusion():
    # CLI --feed is the user's explicit choice; the exclude list only governs
    # the auto-resolve (daemon/drain "score everything") path.
    out = _pick_unread_batch_round_robin(
        _FakeReader(_GROUPS, _ITEMS),
        batch_size=None, feed_library_ids=[3],
        exclude_feed_names={"some project releases"},
    )
    assert {it["item_id"] for it in out} == {2}
