"""Text caps include separators and apply on successful retrieval, not just misses."""
from __future__ import annotations

import json
from html import unescape
from itertools import product
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.faithbench import _corpus, _judge, _runner
from zotero_summarizer.services.library import _review_text as review, qa


@pytest.mark.parametrize("budget", [1, 5, 10, 21, 50, 101])
def test_section_selection_never_exceeds_the_exact_cap(budget):
    sections = [{"title": "Methods", "text": "M" * 100}, {"title": "Limitations", "text": "L" * 100}]
    result = review.select_review_text(sections, "M" * 100 + "L" * 100, budget=budget)
    assert len(result) <= budget
    if budget >= 50:
        assert "M" in result and "L" in result


def _index(monkeypatch, chunks, ranked=None):
    class Index:
        def __init__(self, _text):
            self.chunks = chunks

        def top_chunks(self, _query, k):
            return list(chunks if ranked is None else ranked)[:k]

    monkeypatch.setattr(_corpus, "PaperChunkIndex", Index)
    return Index


@pytest.mark.parametrize("budget", [11, 12, 15, 17])
def test_ranked_fill_takes_a_safe_prefix_of_the_next_chunk(monkeypatch, budget):
    chunks = ["A" * 8, "B" * 8]
    _index(monkeypatch, chunks)
    result = review._fill_from_chunks("A" * 8 + "B" * 8, budget, ["query"])
    assert len(result) == budget
    assert result.startswith("A" * 8 + "\n\nB")
    assert result.endswith("B")


def test_oversized_relevant_chunk_wins_over_irrelevant_document_prefix(monkeypatch):
    chunks = ["UNRELATED " * 20, "RELEVANT " * 20]
    _index(monkeypatch, chunks, ranked=chunks[::-1])
    result = review._fill_from_chunks("".join(chunks), 40, ["RELEVANT"])
    assert result == chunks[1][:40]


@pytest.mark.parametrize("budget", [-1, 0])
def test_chunk_fill_with_no_budget_is_empty(monkeypatch, budget):
    _index(monkeypatch, ["data"])
    assert review._fill_from_chunks("data", budget, ["query"]) == ""


def test_equal_share_allocation_uses_integer_remainder():
    allocations = review._water_fill(["A" * 100, "B" * 100, "C" * 100], 11)
    assert sum(allocations) == 11
    assert max(allocations) - min(allocations) <= 1


def test_chunk_clipping_matches_independent_join_slice_oracle():
    for lengths in product(range(1, 5), repeat=3):
        chunks = [letter * size for letter, size in zip("αβγ", lengths)]
        for separator in ("", "\n\n", _corpus._CONTEXT_SEPARATOR):
            for budget in range(-1, 33):
                kept = _corpus._clip_chunks(chunks, budget, separator=separator)
                expected = separator.join(chunks)[:max(0, budget)]
                if separator:
                    expected = expected.rstrip(separator)
                assert separator.join(kept) == expected
                assert all(kept)
                assert kept[:-1] == chunks[:max(0, len(kept) - 1)]
    assert _corpus._clip_chunks([], 100) == []


def test_water_fill_exhaustive_small_allocations():
    for lengths in product(range(5), repeat=3):
        chunks = ["x" * size for size in lengths]
        for budget in range(-1, 17):
            caps = review._water_fill(chunks, budget)
            assert sum(caps) == min(max(0, budget), sum(lengths))
            assert all(0 <= cap <= size for cap, size in zip(caps, lengths))
            partial = [cap for cap, size in zip(caps, lengths) if cap < size]
            assert not partial or max(partial) - min(partial) <= 1


@pytest.mark.parametrize("chunks,ranked,expected", [
    (["A" * 8, "B" * 8], ["B" * 8, "A" * 8], "AAA\n\nBBBBBBBB"),
    (["A" * 8, "A" * 8], ["A" * 8, "A" * 8], "AAAAAAAA\n\nAAA"),
])
def test_clipped_selection_preserves_document_order_and_occurrences(monkeypatch, chunks, ranked, expected):
    _index(monkeypatch, chunks, ranked)
    assert review._fill_from_chunks("".join(chunks), 13, ["query"]) == expected


def _captured_context(prompt):
    wrapped = prompt.split("Paper text:\n", 1)[1].split("\n\nQuestion:", 1)[0]
    assert wrapped.startswith("<untrusted_input>") and wrapped.endswith("</untrusted_input>")
    return unescape(wrapped.removeprefix("<untrusted_input>").removesuffix("</untrusted_input>"))


@pytest.mark.parametrize("budget", [1, 50, 89, 90, 150])
def test_production_and_benchmark_retrieval_respect_the_same_cap(monkeypatch, budget):
    chunks = ["A" * 80, "B" * 80]
    text = "".join(chunks)
    Index = _index(monkeypatch, chunks)
    monkeypatch.setattr(qa, "PaperChunkIndex", Index)
    monkeypatch.setattr(qa.paper_render, "build_paper_read", lambda _: {"full_text": text})
    prompts = []

    class Model:
        def prompt(self, prompt):
            prompts.append(prompt)
            return json.dumps({"answer": None, "quote": None})

    monkeypatch.setattr(qa, "state", lambda: SimpleNamespace(
        app_state=SimpleNamespace(config=SimpleNamespace(
            quality_review=SimpleNamespace(max_text_chars=budget), llm_routing=None,
        )), resolve_stage_client=lambda _: Model(),
    ))
    monkeypatch.setattr(qa, "resolve_stage", lambda *_: SimpleNamespace(model="fake"))
    response = qa.ask_paper("KEY", "What is described?", mode="retrieval")
    actual = _captured_context(prompts[-1])
    expected = _runner._qa_context(
        SimpleNamespace(question="What is described?"), condition="retrieval",
        text=text, index=Index(text), max_chars=budget,
    )
    assert len(actual) <= budget
    assert len(expected) <= budget
    assert actual == expected
    assert actual.startswith("A")
    assert response["chunks_used"] == (1 if budget <= 89 else 2)


@pytest.mark.parametrize("field", ["", "read_why"])
def test_claim_judge_caps_retrieval_and_fulltext_second_pass(monkeypatch, field):
    chunks = ["A" * 80, "B" * 80]
    Index = _index(monkeypatch, chunks)
    text = "".join(chunks)
    substrate = SimpleNamespace(text=text, norm=text.lower(), index=Index(text))
    prompts = []

    class Model:
        def prompt(self, prompt):
            prompts.append(prompt)
            return json.dumps({"verdict": "not_enough_info"})

    _judge.judge_claim(Model(), claim="Different factual claim", substrate=substrate,
                       max_chars=50, judge_model="fake", field=field, research_goals="A goal")
    assert len(prompts) == 2
    for prompt in prompts:
        context = _captured_context(prompt.replace("\n\nReturn ONE JSON", "\n\nQuestion:"))
        assert len(context) <= 50
