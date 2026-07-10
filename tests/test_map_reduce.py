"""Map-reduce deep review: chunk splitter + map(local)→reduce(API) assembly."""
from __future__ import annotations

import pytest

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library._map_reduce import (
    ChunkBudget,
    digest_for_strategy,
    map_reduce_digest,
    split_chunks,
)
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def test_split_chunks_basic_and_overlap():
    text = "x" * 20000
    chunks = split_chunks(text, 8000, overlap=200)
    assert len(chunks) == 3  # step 7800 → 0, 7800, 15600
    assert all(len(c) <= 8000 for c in chunks)


def test_split_chunks_validates():
    with pytest.raises(ValueError):
        split_chunks("abc", 0)
    with pytest.raises(ValueError):
        split_chunks("abc", 100, overlap=100)


def test_split_chunks_drops_empty():
    assert split_chunks("   ", 10, overlap=0) == []


class _MapLLM:
    def __init__(self):
        self.calls = 0

    def prompt(self, _prompt):
        self.calls += 1
        return f"note {self.calls}"


class _ReduceLLM:
    def pydantic_prompt(self, *, prompt, pydantic_model):
        # The reduce model sees the chunk notes as its source text.
        assert "note 1" in prompt and "chunk 1" in prompt
        return PaperDigest(tldr="synthesized from notes")


def test_map_reduce_maps_each_chunk_then_reduces():
    text = "Sample paper body. " * 1200  # ~22k chars → multiple chunks
    map_llm = _MapLLM()
    digest = map_reduce_digest(
        "T", text, _default_goals_config(),
        map_llm=map_llm, reduce_llm=_ReduceLLM(), chunk_chars=8000, sub_concurrency=1,
    )
    assert isinstance(digest, PaperDigest)
    assert digest.basis == "map_reduce"
    assert digest.tldr == "synthesized from notes"
    assert map_llm.calls == len(split_chunks(text, 8000))  # one map call per chunk


def test_map_reduce_empty_text_raises():
    with pytest.raises(ValueError):
        map_reduce_digest("T", "   ", _default_goals_config(),
                          map_llm=_MapLLM(), reduce_llm=_ReduceLLM(), chunk_chars=8000)


class _DigestLLM:
    """Single LLM that serves both the map (prompt) and reduce (pydantic_prompt) roles,
    recording the text it was handed so the dispatch test can assert what reached the model."""
    def __init__(self):
        self.seen_text = ""
        self.pydantic_calls = 0

    def prompt(self, prompt):
        return "map note"

    def pydantic_prompt(self, *, prompt, pydantic_model):
        self.seen_text = prompt
        self.pydantic_calls += 1
        return PaperDigest(tldr="ok")


def test_digest_for_strategy_dispatches_by_chunk_strategy():
    """Regression: chunk_strategy was DEAD config (deep_review called assess_digest
    unconditionally). digest_for_strategy must now actually branch — rank/prefix take the
    single-call assess_digest path; map_reduce takes the multi-chunk path (map_llm.prompt
    called once per chunk). Proves the knob is wired, not a no-op."""
    cfg = _default_goals_config()
    text = "Sample paper body. " * 1200  # ~22k chars → multiple chunks at 8000
    budget = ChunkBudget(max_chars=cfg.quality_review.max_text_chars, chunk_chars=8000, sub_concurrency=1)

    # rank (default) → single assess_digest call, basis full_text
    cfg.quality_review.chunk_strategy = "rank"
    llm = _DigestLLM()
    d = digest_for_strategy("T", text, cfg, map_llm=_MapLLM(), reduce_llm=llm, budget=budget)
    assert d.basis == "full_text" and llm.pydantic_calls == 1

    # prefix → still single assess_digest call (naive truncate path), basis full_text
    cfg.quality_review.chunk_strategy = "prefix"
    llm_p = _DigestLLM()
    d_p = digest_for_strategy("T", text, cfg, map_llm=_MapLLM(), reduce_llm=llm_p, budget=budget)
    assert d_p.basis == "full_text" and llm_p.pydantic_calls == 1

    # map_reduce → map_llm.prompt called once per chunk, basis map_reduce
    cfg.quality_review.chunk_strategy = "map_reduce"
    map_llm = _MapLLM()
    d_m = digest_for_strategy("T", text, cfg, map_llm=map_llm, reduce_llm=_DigestLLM(), budget=budget)
    assert d_m.basis == "map_reduce"
    assert map_llm.calls == len(split_chunks(text, 8000))  # one map call per chunk — NOT a no-op
