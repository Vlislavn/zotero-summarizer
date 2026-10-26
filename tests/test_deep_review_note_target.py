"""Generated-note staging respects logical ownership and explicit native approval."""
from __future__ import annotations

import pytest
from unittest.mock import Mock

from tests._generated_write_fixtures import config, paper_io, published_review
from tests.test_deep_review import _StubExtractor, _StubReader, _detail, _run, _wire
from zotero_summarizer.services.library import _pdf_acquire, _review_cache, deep_review
from zotero_summarizer.services.zotero import zotero


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    with deep_review._LOCK:
        deep_review._JOBS.clear()
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "reviews.json")


@pytest.mark.parametrize("item_key", ["feed:73", "feed:g:another-source"])
def test_in_place_feed_review_never_attempts_zotero_note(config, monkeypatch, item_key):
    writer = Mock(side_effect=AssertionError("Feed review cannot resolve a native writer"))
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", writer)
    _wire(
        monkeypatch, config,
        reader=_StubReader({item_key: _detail()}), extractor=_StubExtractor(),
    )
    _run([{"item_key": item_key, "title": "Feed story"}])
    entry = deep_review.get_cached_review(item_key)
    assert entry["digest"] is not None and entry["zotero_note_written"] is False
    assert entry["zotero_note_error"] is None
    writer.assert_not_called()


def test_library_review_stages_current_digest_without_native_write(published_review):
    from zotero_summarizer.storage import repositories
    from zotero_summarizer.integrations.zotero_read import ZoteroReader

    value = published_review
    assert value.job["status"] == "ready"
    assert deep_review.get_cached_review(value.key)["zotero_note_written"] is False
    proposal, = repositories.get_pending_changes(item_key=value.key)
    assert proposal["generated_preview"]["item_key"] == value.key
    assert proposal["status"] == "pending"
    assert ZoteroReader(value.db.parent).get_item_notes(value.key) == []
    value.writer.apply_changes.assert_not_called()


def test_browser_extra_outcome_reaches_cached_review(config, monkeypatch):
    item_key = "MISS0001"
    _wire(
        monkeypatch, config,
        reader=_StubReader({item_key: _detail(pdf_path="")}), extractor=_StubExtractor(),
    )
    monkeypatch.setattr(
        _pdf_acquire, "acquire_for_item",
        lambda *_a, **_k: _pdf_acquire.AcquireResult(
            path=None, outcome="browser_extra_unavailable",
        ),
    )
    ctx = deep_review._build_ctx()
    ctx["_acquire_missing"] = True
    deep_review._review_worker({"item_key": item_key, "title": "Missing", "pdf_path": ""}, ctx, "")
    entry = deep_review.get_cached_review(item_key)
    assert entry["needs_pdf"] is True
    assert entry["acquire_outcome"] == "browser_extra_unavailable"
    assert entry["needs_login"] is False
