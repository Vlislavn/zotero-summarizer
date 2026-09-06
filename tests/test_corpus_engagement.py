"""Corpus engagement survives both import paths and retains its intended sign."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.domain import TRIAGE_APPROVED_TAG, TRIAGE_REJECTED_TAG
from zotero_summarizer.models import CorpusItem
from zotero_summarizer.services import corpus as service
from zotero_summarizer.storage.corpus import EmbeddingCache


@pytest.mark.parametrize("tags, annotations, notes, age, weight", [
    ([], 0, 0, 29, 0.0),
    ([], 0, 0, 30, -0.3),
    ([], 0, 0, 90, -0.3),
    ([], 0, 0, None, 0.0),
    (["🧠"], 0, 0, 90, 3.0),
    (["👀"], 0, 0, 90, 2.0),
    ([], 1, 0, 90, 2.0),
    ([], 0, 1, 90, 1.5),
    ([TRIAGE_APPROVED_TAG, "🧠"], 1, 1, 90, 4.0),
    (["👎", "🧠"], 1, 1, 90, -2.0),
    ([TRIAGE_REJECTED_TAG, TRIAGE_APPROVED_TAG], 1, 1, 90, -3.0),
])
def test_weight_metadata_and_both_affinity_paths(tmp_path, tags, annotations, notes, age, weight):
    cache = EmbeddingCache(tmp_path / "corpus.db", "test-model")
    created = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat() if age is not None else None
    cache.upsert_items([CorpusItem(
        item_id="A", title="Paper", tags=tags, collections=["Research"],
        annotation_count=annotations, manual_note_count=notes, created_at=created,
    )])
    metadata = cache.get_item_metadata("A")
    assert metadata["engagement_weight"] == weight
    assert metadata["signals"]["stale_weak_negative"] is (weight == -0.3)
    full = cache.match_candidate("Candidate", "Abstract")
    fast, _ = cache.affinity_and_goals("Candidate", "Abstract")
    expected = 1.0 if weight > 0 else -1.0 if weight < 0 else 0.0
    assert full.affinity_score == fast == expected
    assert full.suggested_collections == (["Research"] if weight > 0 else [])


@pytest.mark.parametrize("route", ["refresh", "auto"])
def test_both_import_paths_preserve_and_remove_engagement(tmp_path, monkeypatch, route):
    asyncio.run(_check_import_paths(tmp_path, monkeypatch, route))


async def _check_import_paths(tmp_path, monkeypatch, route):
    cache = EmbeddingCache(tmp_path / "corpus.db", "test-model")
    embed = Mock(return_value=[1.0, 0.0, 0.0])
    monkeypatch.setattr(cache, "_embed", embed)
    details = {
        key: {"item_key": key, "title": key, "abstract": "Abstract", "tags": [],
              "collections": [{"path": "Research > Agents"}], "date_added": "2020-01-01",
              "annotations": [{"text": "Highlight"}] if key == "A" else [],
              "notes": [{"note": "Note"}] if key == "B" else []}
        for key in ("A", "B")
    }
    rows = [{"item_key": key, "title": key, "abstract": "Abstract"} for key in details]
    reader = SimpleNamespace(
        get_item_detail=Mock(side_effect=details.get),
        get_all_items=Mock(side_effect=lambda **kwargs: {"items": rows, "total": len(rows)}),
    )
    runtime = SimpleNamespace(
        embedding_cache=cache, zotero_reader=reader,
        app_state=SimpleNamespace(config=SimpleNamespace(corpus=SimpleNamespace(stale_days_for_weak_negative=30))),
    )
    monkeypatch.setattr(service, "state", lambda: runtime)
    monkeypatch.setattr(service.triage_db, "get_latest_results_for_items", lambda keys: {})
    events = []
    monkeypatch.setattr(service.triage_db, "insert_feedback_events", lambda batch: events.extend(batch) or len(batch))
    if route == "auto":
        await service.auto_import_corpus_from_zotero(page_size=1)
    else:
        assert await service.refresh_corpus_items_by_keys(["A", "B", "A"]) == (2, 0, 2)
    assert reader.get_item_detail.call_count == 2
    assert cache.get_item_metadata("A")["annotation_count"] == 1
    assert cache.get_item_metadata("B")["manual_note_count"] == 1
    assert cache.get_item_metadata("A")["collections"] == ["Research > Agents"]
    assert {(e["item_id"], e["signal"]) for e in events} == {("A", "has_annotations"), ("B", "manual_note")}

    events.clear()
    # Startup resync must neither erase existing counts nor invent stale negatives.
    await service.auto_import_corpus_from_zotero(page_size=1)
    assert cache.get_item_metadata("A")["annotation_count"] == 1
    assert cache.get_item_metadata("B")["manual_note_count"] == 1
    assert all(e["feedback_type"] == "implicit_engagement" for e in events)
    events.clear()
    details["A"]["annotations"] = []
    details["B"]["notes"] = []
    await service.auto_import_corpus_from_zotero(page_size=1)
    for key in details:
        metadata = cache.get_item_metadata(key)
        assert metadata["annotation_count"] == metadata["manual_note_count"] == 0
        assert metadata["engagement_weight"] == -0.3
    assert all(e["feedback_type"] == "implicit_weak_negative" for e in events)
    assert len(events) == 2
    assert embed.call_count == 2  # all later writes only update metadata
