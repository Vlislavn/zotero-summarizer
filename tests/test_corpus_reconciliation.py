"""A corpus mirror removes only items authoritatively absent from Zotero."""
import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests._zotero_fixtures import add_library_item, add_tag_to_item, build_zotero_db, mark_trashed
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.models import CorpusItem
from zotero_summarizer.services import corpus as service
from zotero_summarizer.storage.corpus import EmbeddingCache
from zotero_summarizer.storage.corpus_bm25 import CorpusBM25


def _runtime(tmp_path, monkeypatch, reader):
    cache = EmbeddingCache(tmp_path / "corpus.db", "test-model")
    runtime = SimpleNamespace(
        embedding_cache=cache, zotero_reader=reader,
        app_state=SimpleNamespace(config=SimpleNamespace(corpus=SimpleNamespace(stale_days_for_weak_negative=30))),
    )
    monkeypatch.setattr(service, "state", lambda: runtime)
    monkeypatch.setattr(service.triage_db, "get_latest_results_for_items", lambda keys: {})
    monkeypatch.setattr(service.triage_db, "insert_feedback_events", lambda events: len(events))
    return cache


@pytest.mark.parametrize("route", ["refresh", "auto"])
def test_deleted_and_trashed_items_leave_all_warm_readers(tmp_path, monkeypatch, route):
    db = build_zotero_db(tmp_path / "zotero")
    ids = {key: add_library_item(db, item_key=key, title=title) for key, title in (
        ("A", "quasar"), ("B", "pulsar"), ("C", "genomics"), ("D", "chemistry"),
    )}
    for key in ("A", "B"):
        add_tag_to_item(db, item_id=ids[key], tag_name="🧠")
    cache = _runtime(tmp_path, monkeypatch, ZoteroReader(db.parent))
    asyncio.run(service.auto_import_corpus_from_zotero(page_size=2))
    cache.upsert_goals(["Goal"])
    lexical = CorpusBM25(cache.db_path)
    keys = list(ids)
    assert set(cache.query_affinity_for_items("Query", keys)) == set(keys)
    assert set(cache.goal_affinity_for_items(keys)) == set(keys)
    assert cache.affinity_and_goals("Query", "")[0] == 1.0
    assert "A" in lexical.search("quasar", keys)

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM items WHERE itemID = ?", (ids["A"],))
    mark_trashed(db, item_id=ids["B"])

    async def refresh():
        if route == "auto":
            await service.auto_import_corpus_from_zotero(page_size=2)
        else:
            await service.refresh_corpus_items_by_keys(["A", "B", "A"])

    asyncio.run(refresh())
    assert cache.get_item_metadata("A") is cache.get_item_metadata("B") is None
    assert cache.list_items_metadata()["total"] == 2
    assert set(cache.query_affinity_for_items("Query", keys)) == {"C", "D"}
    assert set(cache.goal_affinity_for_items(keys)) == {"C", "D"}
    assert cache.affinity_and_goals("Query", "")[0] == 0.0
    assert cache.match_candidate("Query", "").affinity_score == 0.0
    assert lexical.search("quasar", keys) == {}
    assert set(lexical.texts_for(keys)) == {"C", "D"}
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM deletedItems WHERE itemID = ?", (ids["B"],))
    asyncio.run(refresh())
    assert cache.get_item_metadata("B") is not None
    assert cache.get_item_metadata("A") is None


@pytest.mark.parametrize("live", [False, True])
def test_empty_listing_checks_every_cached_key_without_metadata_page_cap(tmp_path, monkeypatch, live):
    reader = SimpleNamespace(
        get_items=Mock(return_value={"items": [], "total": 0}),
        get_all_items=Mock(return_value={"items": [], "total": 0}),
        get_item_detail=Mock(side_effect=lambda key: {"item_key": key, "title": "Still live"} if live and key == "LIVE" else None),
    )
    cache = _runtime(tmp_path, monkeypatch, reader)
    cache.upsert_items([CorpusItem(item_id=str(i), title="Gone") for i in range(1001)] + [
        CorpusItem(item_id="LIVE", title="Old title"),
    ])
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute("UPDATE corpus_embeddings SET encoder_id = NULL")
    asyncio.run(service.auto_import_corpus_from_zotero())
    assert cache.list_items_metadata()["total"] == int(live)
    if live:
        assert cache.get_item_metadata("LIVE")["title"] == "Still live"
    else:
        assert cache.get_item_metadata("LIVE") is None
    assert reader.get_item_detail.call_count == 1002
    reader.get_items.assert_not_called()


@pytest.mark.parametrize("failure", ["read", "encode", "delete", "malformed", "wrong_key"])
def test_failed_batch_preserves_prior_rows(tmp_path, monkeypatch, failure):
    def detail(key):
        if key == "A":
            return None
        if failure == "read":
            raise RuntimeError("injected reader failure")
        if failure == "wrong_key":
            return {"item_key": "OTHER", "title": "Changed"}
        return {} if failure == "malformed" else {"item_key": key, "title": "Changed"}

    cache = _runtime(tmp_path, monkeypatch, SimpleNamespace(get_item_detail=detail))
    cache.upsert_items([CorpusItem(item_id=key, title="Old") for key in ("A", "B")])
    if failure == "encode":
        monkeypatch.setattr(cache, "_embed", Mock(side_effect=RuntimeError("injected encoder failure")))
    if failure == "delete":
        with sqlite3.connect(cache.db_path) as conn:
            conn.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON corpus_embeddings BEGIN SELECT RAISE(ABORT, 'injected deletion failure'); END")
    with pytest.raises((RuntimeError, sqlite3.IntegrityError, KeyError, ValueError)):
        asyncio.run(service.refresh_corpus_items_by_keys(["A", "B"]))
    assert cache.get_item_metadata("A")["title"] == cache.get_item_metadata("B")["title"] == "Old"


def test_listing_failure_does_not_clear_corpus(tmp_path, monkeypatch):
    reader = SimpleNamespace(
        get_all_items=Mock(side_effect=RuntimeError("read failed")),
        get_items=Mock(side_effect=RuntimeError("read failed")),
    )
    cache = _runtime(tmp_path, monkeypatch, reader)
    cache.upsert_items([CorpusItem(item_id="A", title="Keep")])
    with pytest.raises(RuntimeError, match="read failed"):
        asyncio.run(service.auto_import_corpus_from_zotero())
    assert cache.get_item_metadata("A")["title"] == "Keep"
