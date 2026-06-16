"""Semantic ("Meaning") search branch of build_reading_queue: hybrid re-order +
cap, substring bypass, fallback, gate-off, and the response flags. hybrid_search
is mocked, so no embeddings/BM25/reranker are loaded."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import zotero_summarizer.services.library._search as search_mod
from zotero_summarizer.services.library import reading_queue


class _FakeReader:
    def __init__(self, items):
        self._items = items
        self.captured = {}

    def get_all_items(self, *, collection_key=None, search=None, tag=None, page_size=500, include_abstract=True):
        self.captured.update(collection_key=collection_key, search=search, tag=tag)
        return {"items": self._items, "total": len(self._items)}


class _FakeGate:
    def __init__(self, sha):
        self.golden_csv_sha256 = sha


def _item(key, date="2026-05-01"):
    return {"item_key": key, "title": f"T{key}", "abstract": "abs", "authors": "A",
            "reading_priority": "", "has_pdf": True, "date_added": date, "tags": []}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from zotero_summarizer.storage import repositories
    reading_queue.finish(error=None)
    monkeypatch.setattr(reading_queue, "_cache_path", lambda: tmp_path / "rq.json")
    monkeypatch.setattr(reading_queue, "run_in_background", lambda target: None)
    monkeypatch.setattr(reading_queue, "get_settings", lambda: SimpleNamespace(
        corpus_db_path=tmp_path / "c.db", triage_db_path=tmp_path / "t.db"))
    monkeypatch.setattr(repositories, "list_label_verdict_priorities", lambda db: {})
    yield
    reading_queue.finish(error=None)


def _patch_state(monkeypatch, reader, gate):
    monkeypatch.setattr(reading_queue, "get_state", lambda: SimpleNamespace(
        zotero_reader=reader, classifier_gate=gate, app_state=SimpleNamespace(config=object())))


def _seed(sha, **scores):
    reading_queue._write_cache(sha, {
        k: {"relevance_score": v, "why_reason": "Topic match", "scoring": {"composite_score": v, "shap_top": []}}
        for k, v in scores.items()
    })


def _fake_hybrid(monkeypatch, ordered, scores, status):
    monkeypatch.setattr(search_mod, "hybrid_search", lambda q, keys, **kw: (ordered, scores, status))


def test_semantic_orders_caps_and_keeps_gate_score(monkeypatch):
    reader = _FakeReader([_item("A"), _item("B"), _item("C"), _item("D")])
    _patch_state(monkeypatch, reader, _FakeGate("sha"))
    _seed("sha", A=2.0, B=4.0, C=3.0, D=1.0)
    _fake_hybrid(monkeypatch, ["C", "A"], {"C": 0.9, "A": 0.5},
                 {"reranked": True, "reranker_loading": False, "semantic_unavailable": False})
    res = reading_queue.build_reading_queue(search="brain", semantic=True)
    assert [i["item_key"] for i in res["items"]] == ["C", "A"]   # ranked subset; B, D dropped
    assert res["items"][0]["search_score"] == 0.9
    assert res["items"][0]["relevance_score"] == 3.0            # gate score untouched
    assert res["semantic"] is True and res["reranked"] is True
    assert reader.captured["search"] is None                    # substring bypassed


def test_semantic_keeps_collection_and_tag(monkeypatch):
    reader = _FakeReader([_item("A")])
    _patch_state(monkeypatch, reader, _FakeGate("sha"))
    _seed("sha", A=3.0)
    _fake_hybrid(monkeypatch, ["A"], {"A": 0.7},
                 {"reranked": True, "reranker_loading": False, "semantic_unavailable": False})
    reading_queue.build_reading_queue(search="x", semantic=True, collection="COLL", tag="🧪")
    assert reader.captured == {"collection_key": "COLL", "search": None, "tag": "🧪"}


def test_semantic_unavailable_falls_back_to_gate_order(monkeypatch):
    reader = _FakeReader([_item("A"), _item("B")])
    _patch_state(monkeypatch, reader, _FakeGate("sha"))
    _seed("sha", A=2.0, B=4.0)
    _fake_hybrid(monkeypatch, [], {},
                 {"reranked": False, "reranker_loading": False, "semantic_unavailable": True})
    res = reading_queue.build_reading_queue(search="x", semantic=True)
    assert res["semantic"] is False and res["semantic_unavailable"] is True
    assert [i["item_key"] for i in res["items"]] == ["B", "A"]   # normal gate order


def test_semantic_empty_query_is_normal_queue(monkeypatch):
    reader = _FakeReader([_item("A"), _item("B")])
    _patch_state(monkeypatch, reader, _FakeGate("sha"))
    _seed("sha", A=2.0, B=4.0)
    calls = {"n": 0}

    def _counting(*a, **k):
        calls["n"] += 1
        return [], {}, {}
    monkeypatch.setattr(search_mod, "hybrid_search", _counting)
    res = reading_queue.build_reading_queue(search="", semantic=True)
    assert calls["n"] == 0                                       # never invoked for an empty query
    assert res["semantic"] is False
    assert [i["item_key"] for i in res["items"]] == ["B", "A"]


def test_semantic_works_when_gate_off(monkeypatch):
    reader = _FakeReader([_item("A"), _item("B")])
    _patch_state(monkeypatch, reader, None)                      # gate off → model_ready False
    _fake_hybrid(monkeypatch, ["A", "B"], {"A": 0.9, "B": 0.8},
                 {"reranked": True, "reranker_loading": False, "semantic_unavailable": False})
    res = reading_queue.build_reading_queue(search="x", semantic=True)
    assert res["model_ready"] is False
    assert [i["item_key"] for i in res["items"]] == ["A", "B"]
    assert res["items"][0]["relevance_score"] is None
    assert res["semantic"] is True


def test_semantic_reranker_loading_flag(monkeypatch):
    reader = _FakeReader([_item("A")])
    _patch_state(monkeypatch, reader, _FakeGate("sha"))
    _seed("sha", A=3.0)
    _fake_hybrid(monkeypatch, ["A"], {"A": 0.5},
                 {"reranked": False, "reranker_loading": True, "semantic_unavailable": False})
    res = reading_queue.build_reading_queue(search="x", semantic=True)
    assert res["reranker_loading"] is True and res["reranked"] is False and res["semantic"] is True
