"""hybrid_search: RRF fusion + cross-encoder rerank + the degradation ladder.
All retrievers/reranker are mocked (the reranker model is NEVER downloaded)."""
from __future__ import annotations

from types import SimpleNamespace

import zotero_summarizer.services.library._search as s


class _FakeCache:
    def __init__(self, sims):
        self._sims = sims

    def query_affinity_for_items(self, q, keys):
        return {k: v for k, v in self._sims.items() if k in keys}


class _FakeBM:
    def __init__(self, scores):
        self._scores = scores

    def search(self, q, keys, top_k=100):
        return {k: v for k, v in self._scores.items() if k in keys}

    def texts_for(self, keys):
        return {k: f"text {k}" for k in keys}


class _FakeReranker:
    def __init__(self, ready, ranked=None):
        self._ready = ready
        self._ranked = ranked or []
        self.loaded_async = False

    def is_ready(self):
        return self._ready

    def is_loading(self):
        return False

    def ensure_loaded_async(self):
        self.loaded_async = True

    def rerank(self, q, pairs, top_n):
        return self._ranked[:top_n]


def _patch(monkeypatch, *, enabled=True, bm25=True, rerank_enabled=True,
           cache_sims, bm_scores, reranker):
    cfg = SimpleNamespace(enabled=enabled, bm25_enabled=bm25,
                          reranker_enabled=rerank_enabled, reranker_model="m")
    state = SimpleNamespace(
        app_state=SimpleNamespace(config=SimpleNamespace(corpus=cfg)),
        embedding_cache=_FakeCache(cache_sims),
    )
    monkeypatch.setattr(s, "get_state", lambda: state)
    monkeypatch.setattr(s, "get_settings", lambda: SimpleNamespace(corpus_db_path="x"))
    monkeypatch.setattr(s, "get_corpus_bm25", lambda db: _FakeBM(bm_scores))
    monkeypatch.setattr(s, "get_reranker", lambda m: reranker)


def test_reranked_order(monkeypatch):
    rr = _FakeReranker(ready=True, ranked=[("C", 9.0), ("A", 8.0), ("B", 1.0)])
    _patch(monkeypatch, cache_sims={"A": 0.9, "B": 0.1}, bm_scores={"B": 5.0, "C": 1.0}, reranker=rr)
    ordered, scores, status = s.hybrid_search("q", ["A", "B", "C"])
    assert ordered == ["C", "A", "B"]
    assert status["reranked"] is True
    assert scores["C"] == 9.0


def test_fusion_when_reranker_not_ready(monkeypatch):
    rr = _FakeReranker(ready=False)
    _patch(monkeypatch, cache_sims={"A": 0.9}, bm_scores={"B": 5.0}, reranker=rr)
    ordered, _scores, status = s.hybrid_search("q", ["A", "B"])
    assert status["reranked"] is False
    assert rr.loaded_async is True          # background warmup kicked off
    assert set(ordered) == {"A", "B"}       # RRF of dense + bm25


def test_corpus_off_is_unavailable(monkeypatch):
    _patch(monkeypatch, enabled=False, cache_sims={}, bm_scores={}, reranker=_FakeReranker(False))
    ordered, _scores, status = s.hybrid_search("q", ["A"])
    assert ordered == [] and status["semantic_unavailable"] is True


def test_no_candidates_is_unavailable(monkeypatch):
    _patch(monkeypatch, cache_sims={}, bm_scores={}, reranker=_FakeReranker(False))
    ordered, _scores, status = s.hybrid_search("q", ["A", "B"])
    assert ordered == [] and status["semantic_unavailable"] is True


def test_reranker_disabled_uses_fusion(monkeypatch):
    rr = _FakeReranker(ready=True, ranked=[("A", 1.0)])
    _patch(monkeypatch, rerank_enabled=False, cache_sims={"A": 0.9}, bm_scores={"B": 5.0}, reranker=rr)
    _ordered, _scores, status = s.hybrid_search("q", ["A", "B"])
    assert status["reranked"] is False
    assert rr.loaded_async is False         # reranker never consulted


def test_empty_query(monkeypatch):
    _patch(monkeypatch, cache_sims={"A": 0.5}, bm_scores={}, reranker=_FakeReranker(True))
    ordered, _scores, _status = s.hybrid_search("", ["A"])
    assert ordered == []


def test_rrf_prefers_items_in_both_lists():
    fused = s._rrf([["A", "B"], ["B", "C"]])
    assert max(fused, key=fused.get) == "B"   # appears in both ranked lists
