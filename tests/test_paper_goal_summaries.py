"""Goal-conditioned retrieval summaries: HIT/MISS/NOT_RETRIEVED states,
grounding, and the always-length-N board."""
from __future__ import annotations

import numpy as np

from zotero_summarizer.services.library import _paper_goal_summaries as gs

SECTIONS = [
    {"title": "Methods", "page": 3,
     "text": "We use multimodal patient imaging data and clinical notes for diagnosis in the hospital setting here."},
    {"title": "Intro", "page": 1,
     "text": "This paper studies clinical multimodal models for patient care across multiple sites and modalities."},
]


class _FakeEmbedder:
    """2-axis toy embedder: clinical-multimodal text → axis 0, quantum → axis 1."""

    def encode(self, texts, normalize_embeddings=True):
        vecs = []
        for t in texts:
            tl = t.lower()
            v = np.zeros(2, dtype="float32")
            if any(w in tl for w in ("multimodal", "clinic", "patient")):
                v[0] = 1.0
            if "quantum" in tl:
                v[1] = 1.0
            if not v.any():
                v[0] = 0.01  # tiny, stays below the relevance floor
            n = float(np.linalg.norm(v))
            vecs.append(v / n if n else v)
        return np.array(vecs)


class _NotReadyReranker:
    def ensure_loaded_async(self):
        pass

    def is_ready(self):
        return False


class _FacetLLM:
    def __init__(self, quote, relevant=True):
        self._quote = quote
        self._relevant = relevant

    def pydantic_prompt(self, prompt, pydantic_model):
        return pydantic_model(relevant=self._relevant, summary="It does multimodal clinical diagnosis.",
                              supporting_quotes=[self._quote])


def _patch(monkeypatch, embedder, reranker=None):
    monkeypatch.setattr(gs, "_get_embedder", lambda m: embedder)
    monkeypatch.setattr(gs, "get_reranker", lambda m: reranker or _NotReadyReranker())


def test_board_hit_miss_grounded(monkeypatch):
    _patch(monkeypatch, _FakeEmbedder())
    llm = _FacetLLM("We use multimodal patient imaging data and clinical notes for diagnosis")
    out = gs.summarize_for_goals(
        goals=["Multimodal AI for clinics", "Quantum error correction"],
        sections=SECTIONS, full_text="", llm=llm,
    )
    by = {o.goal: o for o in out}
    assert len(out) == 2  # board always renders all goals
    hit = by["Multimodal AI for clinics"]
    assert hit.retrieval_state == "hit" and hit.abstained is False
    assert hit.summary and hit.supporting_quotes and hit.key_sections == ["Methods"]
    miss = by["Quantum error correction"]
    assert miss.retrieval_state == "miss" and miss.summary is None and miss.relevant is False


def test_ungrounded_quote_abstains(monkeypatch):
    _patch(monkeypatch, _FakeEmbedder())
    llm = _FacetLLM("a quote that is not present anywhere in the passages at all")
    out = gs.summarize_for_goals(goals=["Multimodal AI for clinics"], sections=SECTIONS, full_text="", llm=llm)
    g = out[0]
    assert g.retrieval_state == "hit" and g.abstained is True and g.summary is None


def test_model_says_not_relevant_overrides_gate_to_miss(monkeypatch):
    # Dense gate fires (hit), but the facet model reads the chunks and says the
    # paper does not address the goal → MISS with the model's verdict preserved.
    _patch(monkeypatch, _FakeEmbedder())
    llm = _FacetLLM("We use multimodal patient imaging data and clinical notes for diagnosis", relevant=False)
    out = gs.summarize_for_goals(goals=["Multimodal AI for clinics"], sections=SECTIONS, full_text="", llm=llm)
    assert out[0].retrieval_state == "miss" and out[0].relevant is False and out[0].summary is None


def test_no_dense_no_lexical_match_is_not_retrieved(monkeypatch):
    # No embedder (dense off); a goal with no lexical overlap → degraded, never a MISS.
    _patch(monkeypatch, None)
    out = gs.summarize_for_goals(
        goals=["Zzqx nonexistent topic phrase"], sections=SECTIONS, full_text="",
        llm=_FacetLLM("x"),
    )
    assert out[0].retrieval_state == "not_retrieved"


def test_empty_paper_renders_all_cells_not_retrieved(monkeypatch):
    _patch(monkeypatch, _FakeEmbedder())
    out = gs.summarize_for_goals(goals=["A", "B", "C"], sections=[], full_text="", llm=_FacetLLM("x"))
    assert len(out) == 3 and all(o.retrieval_state == "not_retrieved" for o in out)


def test_parallel_goals_match_serial_board_and_order(monkeypatch):
    # Remote tier (sub_concurrency>1): per-goal calls fan out concurrently, but each
    # goal still gets its own full-attention call and the board order is preserved —
    # identical result to the serial (local) path, just faster.
    _patch(monkeypatch, _FakeEmbedder())
    quote = "We use multimodal patient imaging data and clinical notes for diagnosis"
    goals = ["Multimodal AI for clinics", "Clinical patient imaging models"]
    serial = gs.summarize_for_goals(goals=goals, sections=SECTIONS, full_text="",
                                    llm=_FacetLLM(quote), sub_concurrency=1)
    parallel = gs.summarize_for_goals(goals=goals, sections=SECTIONS, full_text="",
                                      llm=_FacetLLM(quote), sub_concurrency=4)
    assert [o.goal for o in serial] == [o.goal for o in parallel] == goals  # order preserved
    assert [o.retrieval_state for o in parallel] == [o.retrieval_state for o in serial]
    assert all(o.retrieval_state == "hit" and o.summary for o in parallel)


class _BatchLLM:
    """Returns a BatchedGoalResponse for the gate-passing goals in ONE call; counts
    calls so a test can prove batching collapses N goal calls into one."""

    def __init__(self, by_index, quote):
        self.calls = 0
        self._by_index = by_index  # {goal_index: (relevant, summary)}
        self._quote = quote

    def pydantic_prompt(self, prompt, pydantic_model):
        self.calls += 1
        return pydantic_model(summaries=[
            {"goal_index": i, "relevant": rel, "summary": summ, "supporting_quotes": [self._quote]}
            for i, (rel, summ) in self._by_index.items()
        ])


def test_batched_goals_single_call_hit_and_miss(monkeypatch):
    _patch(monkeypatch, _FakeEmbedder())
    quote = "We use multimodal patient imaging data and clinical notes for diagnosis"
    # Only the dense-hit goal reaches the batch (index 0); the quantum goal is gated out (miss).
    llm = _BatchLLM({0: (True, "It does multimodal clinical diagnosis.")}, quote)
    out = gs.summarize_for_goals(
        goals=["Multimodal AI for clinics", "Quantum error correction"],
        sections=SECTIONS, full_text="", llm=llm, batch=True,
    )
    by = {o.goal: o for o in out}
    assert len(out) == 2                          # board still renders all goals
    assert llm.calls == 1                         # ONE batched call, not one per hit-goal
    hit = by["Multimodal AI for clinics"]
    assert hit.retrieval_state == "hit" and hit.abstained is False
    assert hit.summary and hit.supporting_quotes and hit.key_sections == ["Methods"]
    assert by["Quantum error correction"].retrieval_state == "miss"  # gated out, no LLM


def test_batched_missing_index_still_renders_abstained(monkeypatch):
    # The batch returns NO entry for the hit goal (index 0 omitted) → the board still
    # shows it (hit, no grounded summary) rather than dropping it.
    _patch(monkeypatch, _FakeEmbedder())
    llm = _BatchLLM({}, "x")
    out = gs.summarize_for_goals(
        goals=["Multimodal AI for clinics"], sections=SECTIONS, full_text="", llm=llm, batch=True,
    )
    assert llm.calls == 1
    assert out[0].retrieval_state == "hit" and out[0].abstained is True and out[0].summary is None
