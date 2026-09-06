"""Disabled/loading search legs are not failed legs; no real model loads."""
from __future__ import annotations

import threading
import time
from unittest.mock import Mock

import pytest

from tests.test_library_search import _patch as patch_library
from tests.test_paper_goal_summaries import SECTIONS, _FakeEmbedder
from zotero_summarizer.services.library import _paper_goal_summaries as goals
from zotero_summarizer.services.library import _search as library
from zotero_summarizer.services.model import reranker as model
from zotero_summarizer.services.search import rank
from zotero_summarizer.services.search._models import Candidate


def _wait_loaded(reranker):
    deadline = time.monotonic() + 2
    while reranker.is_loading() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not reranker.is_loading(), "stub model load did not finish"


@pytest.mark.parametrize("consumer", ["library", "federated", "goals"])
@pytest.mark.parametrize("stage", ["load", "predict"])
def test_reranker_failures_reach_every_consumer(monkeypatch, consumer, stage):
    failure = RuntimeError(f"reranker {stage} failed")
    encoder = Mock(predict=Mock(side_effect=failure))
    constructor = Mock(side_effect=failure) if stage == "load" else Mock(return_value=encoder)
    monkeypatch.setattr(model, "CrossEncoder", constructor)
    reranker = model.Reranker("stub")
    reranker.ensure_loaded_async()
    _wait_loaded(reranker)
    patch_library(monkeypatch, cache_sims={"A": 0.9}, bm_scores={}, reranker=reranker)
    monkeypatch.setattr(rank, "get_reranker", lambda name: reranker)
    monkeypatch.setattr(goals, "get_reranker", lambda name: reranker)
    monkeypatch.setattr(goals, "_get_embedder", lambda name: _FakeEmbedder())
    llm = Mock()
    candidate = Candidate(title="Clinical multimodal models")

    for _ in range(2):
        with pytest.raises(RuntimeError) as caught:
            if consumer == "library":
                library.hybrid_search("clinical", ["A"])
            elif consumer == "federated":
                rank.score_query_relevance("clinical", [candidate], reranker_model="stub")
            else:
                goals.summarize_for_goals(
                    goals=["Clinical multimodal models"], sections=SECTIONS, full_text="", llm=llm,
                )
        assert caught.value is failure
    constructor.assert_called_once_with("stub", max_length=512)
    assert candidate.query_score is None
    llm.pydantic_prompt.assert_not_called()


def test_pending_load_serves_fusion_then_reranks_without_reloading(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    encoder = Mock(predict=Mock(return_value=[0.1, 0.9]))

    def load(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return encoder

    constructor = Mock(side_effect=load)
    monkeypatch.setattr(model, "CrossEncoder", constructor)
    reranker = model.Reranker("stub")
    patch_library(monkeypatch, cache_sims={"A": 0.9, "B": 0.1}, bm_scores={}, reranker=reranker)
    try:
        ordered, _, status = library.hybrid_search("clinical", ["A", "B"])
        assert entered.wait(2)
        assert ordered == ["A", "B"]
        assert status["reranker_loading"] and not status["reranked"]
        reranker.ensure_loaded_async()
        assert not reranker.is_ready()
        assert reranker.rerank("clinical", [("A", "text")], 1) == []
    finally:
        release.set()
        _wait_loaded(reranker)

    ordered, scores, status = library.hybrid_search("clinical", ["A", "B"])
    assert ordered == ["B", "A"] and scores == {"B": 0.9, "A": 0.1}
    assert status["reranked"] and not status["reranker_loading"]
    constructor.assert_called_once_with("stub", max_length=512)


@pytest.mark.parametrize("enabled", [False, True])
def test_bm25_disabled_is_dense_only_but_failure_propagates(monkeypatch, enabled):
    patch_library(monkeypatch, bm25=enabled, rerank_enabled=False,
                  cache_sims={"A": 0.9}, bm_scores={}, reranker=None)
    failure = RuntimeError("BM25 index failed")
    bm25 = Mock(search=Mock(side_effect=failure))
    monkeypatch.setattr(library, "get_corpus_bm25", lambda path: bm25)

    if enabled:
        with pytest.raises(RuntimeError) as caught:
            library.hybrid_search("clinical", ["A"])
        assert caught.value is failure
        bm25.search.assert_called_once()
    else:
        ordered, scores, status = library.hybrid_search("clinical", ["A"])
        assert ordered == ["A"] and scores["A"] > 0
        assert status["mode"] == "hybrid_fusion" and not status["reranked"]
        bm25.search.assert_not_called()


@pytest.mark.parametrize("failure", [ImportError("dependency broken"), OSError("weights unreadable")])
def test_synchronous_setup_load_propagates_original_error(monkeypatch, failure):
    monkeypatch.setattr(model, "CrossEncoder", Mock(side_effect=failure))
    with pytest.raises(type(failure)) as caught:
        model.Reranker("stub")._load()
    assert caught.value is failure
