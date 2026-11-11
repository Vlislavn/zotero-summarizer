"""Targeted corpus refresh honors OFF before any reader/cache/feedback I/O."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from zotero_summarizer.services import corpus as service


@pytest.fixture
def corpus_io(monkeypatch):
    io = SimpleNamespace(
        reader=SimpleNamespace(get_item_detail=Mock(
            side_effect=AssertionError("unexpected Zotero reader I/O"),
        )),
        cache=SimpleNamespace(upsert_items=Mock(
            side_effect=AssertionError("unexpected corpus cache I/O"),
        )),
        lock=AsyncMock(),
        lookup=Mock(side_effect=AssertionError("unexpected feedback lookup")),
        infer=Mock(side_effect=AssertionError("unexpected feedback inference")),
        insert=Mock(side_effect=AssertionError("unexpected feedback insert")),
    )
    io.lock.__aexit__.return_value = False
    io.lock_factory = Mock(return_value=io.lock)
    monkeypatch.setattr(service.asyncio, "Lock", io.lock_factory)
    monkeypatch.setattr(service.triage_db, "get_latest_results_for_items", io.lookup)
    monkeypatch.setattr(service.feedback, "infer_feedback_events_from_corpus_items", io.infer)
    monkeypatch.setattr(service.triage_db, "insert_feedback_events", io.insert)
    return io


def _set_runtime(monkeypatch, *, enabled, reader, cache, lock=None):
    runtime = SimpleNamespace(
        app_state=SimpleNamespace(config=SimpleNamespace(
            corpus=SimpleNamespace(enabled=enabled, stale_days_for_weak_negative=30),
        )),
        zotero_reader=reader, embedding_cache=cache, corpus_write_lock=lock,
    )
    monkeypatch.setattr(service, "state", Mock(return_value=runtime))
    return runtime


def _assert_no_io(io):
    io.reader.get_item_detail.assert_not_called()
    io.cache.upsert_items.assert_not_called()
    io.lock_factory.assert_not_called()
    io.lock.__aenter__.assert_not_called()
    io.lock.__aexit__.assert_not_called()
    io.lookup.assert_not_called()
    io.infer.assert_not_called()
    io.insert.assert_not_called()


@pytest.mark.parametrize("existing_lock", [False, True])
@pytest.mark.parametrize("blocked_io", ["reader", "cache"])
def test_disabled_refresh_does_no_corpus_io(monkeypatch, corpus_io, existing_lock, blocked_io):
    io = corpus_io
    if blocked_io == "cache":
        io.reader.get_item_detail.side_effect = None
        io.reader.get_item_detail.return_value = {"item_key": "A", "title": "Synthetic"}
    lock = io.lock if existing_lock else None
    runtime = _set_runtime(monkeypatch, enabled=False, reader=io.reader, cache=io.cache, lock=lock)

    assert asyncio.run(service.refresh_corpus_items_by_keys([" A ", "A"])) == (0, 0, 0)

    _assert_no_io(io)
    assert runtime.corpus_write_lock is lock


def test_enabled_refresh_preserves_reconciliation_and_feedback(monkeypatch, corpus_io):
    io = corpus_io
    detail = {
        "item_key": "A", "title": "Paper", "abstract": "Owned synthetic text",
        "doi": "10.0000/test", "tags": ["🧠"], "collections": [{"path": "Research"}],
        "annotations": [{"text": "Highlight"}], "notes": [],
    }
    io.reader.get_item_detail.side_effect = {"A": detail, "B": None}.get
    io.cache.upsert_items.side_effect, io.cache.upsert_items.return_value = None, (1, 0)
    latest, events = {"A": {"relevance_score": 4}}, [{"item_id": "A", "signal": "has_annotations"}]
    io.lookup.side_effect, io.lookup.return_value = None, latest
    io.infer.side_effect, io.infer.return_value = None, events
    io.insert.side_effect, io.insert.return_value = None, 1
    _set_runtime(monkeypatch, enabled=True, reader=io.reader, cache=io.cache, lock=io.lock)

    assert asyncio.run(service.refresh_corpus_items_by_keys([" A ", "B", "A"])) == (1, 0, 1)

    assert io.reader.get_item_detail.call_args_list == [call("A"), call("B")]
    io.cache.upsert_items.assert_called_once()
    items, = io.cache.upsert_items.call_args.args
    item, = items
    assert (item.item_id, item.title, item.doi) == ("A", "Paper", "10.0000/test")
    assert (item.collections, item.annotation_count, item.manual_note_count) == (["Research"], 1, 0)
    assert io.cache.upsert_items.call_args.kwargs == {"missing_item_ids": ["B"]}
    io.lock_factory.assert_not_called()
    io.lock.__aenter__.assert_awaited_once()
    io.lock.__aexit__.assert_awaited_once()
    io.lookup.assert_called_once_with(["A"])
    io.infer.assert_called_once_with(items, 30, latest)
    io.insert.assert_called_once_with(events)


@pytest.mark.parametrize("item_keys", [[], ["", " "]])
def test_empty_refresh_does_not_read_runtime(monkeypatch, corpus_io, item_keys):
    get_state = Mock(side_effect=AssertionError("empty refresh read runtime"))
    monkeypatch.setattr(service, "state", get_state)

    assert asyncio.run(service.refresh_corpus_items_by_keys(item_keys)) == (0, 0, 0)

    get_state.assert_not_called()
    _assert_no_io(corpus_io)


@pytest.mark.parametrize("unavailable", ["reader", "cache"])
def test_enabled_refresh_without_reader_or_cache_is_noop(monkeypatch, corpus_io, unavailable):
    io = corpus_io
    runtime = _set_runtime(
        monkeypatch, enabled=True, reader=None if unavailable == "reader" else io.reader,
        cache=None if unavailable == "cache" else io.cache,
    )

    assert asyncio.run(service.refresh_corpus_items_by_keys(["A"])) == (0, 0, 0)

    _assert_no_io(io)
    assert runtime.corpus_write_lock is None


@pytest.mark.parametrize("unavailable", ["reader", "cache"])
def test_unavailable_refresh_without_config_is_noop(monkeypatch, corpus_io, unavailable):
    io = corpus_io
    runtime = _set_runtime(
        monkeypatch, enabled=True, reader=None if unavailable == "reader" else io.reader,
        cache=None if unavailable == "cache" else io.cache,
    )
    runtime.app_state = None

    assert asyncio.run(service.refresh_corpus_items_by_keys(["A"])) == (0, 0, 0)

    _assert_no_io(io)
    assert runtime.app_state is None
    assert runtime.corpus_write_lock is None
