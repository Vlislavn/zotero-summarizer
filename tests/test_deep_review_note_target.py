"""Deep-review Zotero-note mirroring respects logical item ownership."""
from __future__ import annotations

import pytest

from tests.test_deep_review import _StubExtractor, _StubReader, _detail, _run, _wire
from zotero_summarizer.services._common import read_config, settings as _settings
from zotero_summarizer.services.library import _pdf_acquire, _review_cache, deep_review


@pytest.fixture
def config():
    return read_config(_settings().config_path)


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    with deep_review._LOCK:
        deep_review._JOBS.clear()
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "reviews.json")


@pytest.mark.parametrize("item_key", ["feed:73", "feed:g:another-source"])
def test_in_place_feed_review_never_attempts_zotero_note(config, monkeypatch, item_key):
    calls = []
    _wire(
        monkeypatch, config,
        reader=_StubReader({item_key: _detail()}), extractor=_StubExtractor(),
        note_fn=lambda *args: calls.append(args),
    )
    _run([{"item_key": item_key, "title": "Feed story"}])
    entry = deep_review.get_cached_review(item_key)
    assert entry["digest"] is not None and entry["zotero_note_written"] is False
    assert entry["zotero_note_error"] is None and calls == []


def test_library_review_still_mirrors_digest(config, monkeypatch):
    calls = []
    _wire(
        monkeypatch, config,
        reader=_StubReader({"LIBR0001": _detail()}), extractor=_StubExtractor(),
        note_fn=lambda *args: calls.append(args),
    )
    _run([{"item_key": "LIBR0001", "title": "Library paper"}])
    assert calls and calls[0][0] == "LIBR0001"
    assert deep_review.get_cached_review("LIBR0001")["zotero_note_written"] is True


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
