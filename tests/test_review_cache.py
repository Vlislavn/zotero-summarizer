"""Deep-review cache primitives (split out of deep_review for the LOC cap).

Pins ``copy_review`` — the persistence step that carries an IN-PLACE Today review
(cached under ``stable_feed_key``) onto the new library ``item_key`` when the paper
is materialized into Zotero, so the review shows in the library too.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.services.library import _review_cache


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "deep_reviews.json")
    yield


def test_copy_review_persists_under_new_key():
    _review_cache._write_one("feed:d:abc", {"quality": {"grade": "A"}})
    assert _review_cache.copy_review("feed:d:abc", "ZNEW") is True
    assert _review_cache.get_cached_review("ZNEW") == {"quality": {"grade": "A"}}
    # The source entry is untouched (copy, not move).
    assert _review_cache.get_cached_review("feed:d:abc") == {"quality": {"grade": "A"}}


def test_copy_review_is_noop_on_absent_or_same_key():
    assert _review_cache.copy_review("missing", "Z") is False       # nothing cached
    _review_cache._write_one("K1", {"quality": {}})
    assert _review_cache.copy_review("K1", "K1") is False           # same key
    assert _review_cache.copy_review("", "Z") is False              # empty src
    assert _review_cache.copy_review("K1", "") is False             # empty dst


def test_cached_review_keys_reports_all():
    _review_cache._write_one("A", {"x": 1})
    _review_cache._write_one("B", {"x": 2})
    assert _review_cache.cached_review_keys() == {"A", "B"}


def test_current_review_filters_legacy_contracts():
    current = {"review_contract_version": _review_cache.REVIEW_CONTRACT_VERSION, "digest": {}}
    _review_cache._write_one("CURRENT", current)
    _review_cache._write_one("LEGACY", {"digest": {}})

    assert _review_cache.get_current_review("CURRENT") == current
    assert _review_cache.get_current_review("LEGACY") is None
    assert _review_cache.current_review_keys() == {"CURRENT"}
