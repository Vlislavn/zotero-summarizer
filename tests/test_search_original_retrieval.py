"""Original occurrence plumbing through the shared Library hybrid owner."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.services.faithbench._corpus import chunk_spans, chunk_text
from zotero_summarizer.services.library import _paper_goal_summaries as goals
from zotero_summarizer.services.search import _question_evidence as evidence


def test_chunk_text_preserves_independent_legacy_window_oracle():
    text = "  alpha beta gamma delta "
    assert chunk_text(text, chunk_chars=10, overlap=0) == ["alpha", "beta", "gamma", "delta"]
    assert chunk_spans(text, chunk_chars=10, overlap=0) == [(2, 7), (8, 12), (13, 18), (19, 24)]


def test_chunk_spans_reconstruct_legacy_windows_and_keep_repeated_occurrences():
    text = "  repeated words repeated words repeated words repeated words  "
    spans = chunk_spans(text, chunk_chars=18, overlap=0)
    assert [text[start:end] for start, end in spans] == chunk_text(text, chunk_chars=18, overlap=0)
    assert len({start for start, _ in spans}) == len(spans)
    assert spans[0][0] == 2
    assert spans[-1][1] == len(text.rstrip())
    with pytest.raises(ValueError):
        chunk_spans(text, chunk_chars=0)


def test_question_retrieval_reuses_fusion_and_rerank_without_cosine_answer_gate(monkeypatch):
    text = "first unrelated passage " * 60 + "second original passage " * 60
    chunks = goals._section_chunks([], text)
    target = len(chunks) - 1
    bm25 = SimpleNamespace(available=True, ranked=lambda query: [target, 0])
    reranker = Mock()
    reranker.is_ready.return_value = True
    reranker.rerank.side_effect = lambda q, pairs, k: [(str(target), 0.1)]
    ctx = goals._GoalCtx([c["text"] for c in chunks], chunks, bm25, None, None,
                         False, reranker, 0.35, None, "")
    builder = Mock(return_value=ctx)
    monkeypatch.setattr(goals, "_retrieval_context", builder)

    result, capabilities = goals.retrieve_original_passages(text, ["question A", "question B"],
                                                           embedder_model="synthetic-dense", reranker_model="synthetic-rank")

    assert ctx.floor == -1.0
    assert capabilities == {"dense": False, "bm25": True, "reranker": True,
                            "embedder_model": "synthetic-dense", "reranker_model": "synthetic-rank"}
    assert reranker.rerank.call_count == 2
    for group in result:
        assert group == [chunks[target]]
        assert text[group[0]["start"]:group[0]["end"]] == group[0]["text"]


def test_no_retrieval_capability_is_empty_not_negative(monkeypatch):
    ctx = goals._GoalCtx([], [], SimpleNamespace(available=False), None, None,
                         False, SimpleNamespace(is_ready=lambda: False), 0.35, None, "")
    monkeypatch.setattr(goals, "_retrieval_context", lambda *args: ctx)
    result, capabilities = goals.retrieve_original_passages("original", ["question"],
                                                           embedder_model="synthetic", reranker_model="synthetic")
    assert result == [[]]
    assert capabilities["dense"] is False and capabilities["bm25"] is False


@pytest.mark.parametrize("payload", [
    {"state": "sufficient", "evidence_ids": ["unknown-id"], "uncovered": []},
    {"state": "sufficient", "evidence_ids": [], "uncovered": []},
    {"state": "partial", "evidence_ids": ["chars:0:79"], "uncovered": []},
    {"state": "other", "evidence_ids": [], "uncovered": []},
])
def test_invalid_verifier_machine_outputs_remain_unknown(monkeypatch, payload):
    quote = "The intervention did not improve outcomes in the registered controlled study."
    generator = SimpleNamespace(prompt=lambda prompt: json.dumps({
        "answer": "The intervention did not improve outcomes", "quote": quote}))
    deps = SimpleNamespace(llm_light=generator, llm=SimpleNamespace(pydantic_prompt=lambda **kw: payload))
    row = {"question": "Did the intervention help?", "state": "unknown"}
    calls = evidence._assess(row, [{"start": 0, "end": len(quote), "text": quote}], deps, session_id="invalid-verdict")
    assert calls == 2 and row["state"] == "unknown" and row["reason"] == "verifier_error"
