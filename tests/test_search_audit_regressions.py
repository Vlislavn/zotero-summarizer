"""Focused regressions for Search federation, review and session boundaries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent
from zotero_summarizer.services.search._targeted_review import targeted_review
from zotero_summarizer.services.search.rank import constrained_key


def _candidate(title: str) -> Candidate:
    return Candidate(title=title)


def _temporary_store(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import session

    class Settings:
        search_dir = tmp_path

    monkeypatch.setattr(session, "settings", lambda: Settings())
    return session


@pytest.mark.parametrize("epsilon", [0, -0.1, float("inf"), float("nan")])
def test_rank_rejects_invalid_internal_epsilon(epsilon):
    with pytest.raises(ValueError, match="finite and greater than zero"):
        constrained_key(_candidate("p"), epsilon=epsilon)


def test_rank_invalid_environment_epsilon_falls_back(monkeypatch):
    from zotero_summarizer.services.search import rank

    monkeypatch.setenv("ZS_SEARCH_RANK_EPSILON", "0")
    assert rank._epsilon() == rank.DEFAULT_EPSILON


class _Digest:
    tldr = executive_summary = read_why = methods = limitations = key_strength = key_weakness = ""
    key_findings = []
    read_decision = grade = ""


class _Summary:
    def __init__(self, text: str):
        self.summary = text
        self.relevant = True
        self.retrieval_state = "hit"
        self.abstained = False
        self.supporting_quotes = []
        self.key_sections = []


def test_targeted_review_keeps_answer_aligned_after_blank_question(monkeypatch):
    from zotero_summarizer.services.search import _targeted_review as target

    monkeypatch.setattr(target, "assess_digest", lambda **kw: _Digest())
    monkeypatch.setattr(
        target, "summarize_for_goals",
        lambda **kw: [_Summary("lens"), _Summary("answer")],
    )
    cand = _candidate("P")
    targeted_review(cand, full_text="body", query="query", questions=["", "real question"], config=object(), llm=object())
    assert cand.review["question_answers"][0]["question"] == "real question"
    assert cand.review["question_answers"][0]["text"] == "answer"


def test_list_sessions_skips_corrupt_file(monkeypatch, tmp_path):
    store = _temporary_store(monkeypatch, tmp_path)
    sess = ResearchSession(
        id="good", created_at="2026", raw_query="q", intent=SearchIntent(raw_query="q"), plan=QueryPlan(),
    )
    store.save(sess)
    (tmp_path / "bad.json").write_text("{truncated", encoding="utf-8")
    assert [row["id"] for row in store.list_sessions()] == ["good"]


def test_delete_prevents_late_merge_from_resurrecting_session(monkeypatch, tmp_path):
    store = _temporary_store(monkeypatch, tmp_path)
    sess = ResearchSession(
        id="gone", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"), plan=QueryPlan(),
    )
    store.save(sess)
    stale = store.load("gone")
    store.delete("gone")
    store.save_merge(stale)
    assert not (tmp_path / "gone.json").exists()
    assert store.is_deleted("gone")


def test_run_review_isolates_candidate_failure(monkeypatch, tmp_path):
    from zotero_summarizer.services.search import pipeline

    store = _temporary_store(monkeypatch, tmp_path)
    sess = ResearchSession(
        id="batch", created_at="t", raw_query="q", intent=SearchIntent(raw_query="q"),
        plan=QueryPlan(), candidates=[_candidate("A"), _candidate("B")], status="reviewing",
    )
    store.save(sess)
    calls = []

    def acquire(candidate, **kwargs):
        if candidate.title == "A":
            raise RuntimeError("bad PDF")
        return "full text"

    monkeypatch.setattr(pipeline, "acquire_full_text", acquire)
    monkeypatch.setattr(pipeline, "light_review", lambda cand, **kw: calls.append(("light", cand.title)))
    monkeypatch.setattr(pipeline, "rank_candidates", lambda cands: cands)
    monkeypatch.setattr(pipeline, "attach_relevance", lambda cands: None)
    monkeypatch.setattr(pipeline, "select_deep_set", lambda cands, *, k: cands)
    monkeypatch.setattr(pipeline, "targeted_review", lambda cand, **kw: calls.append(("deep", cand.title)))
    deps = SimpleNamespace(
        extractor=object(), unpaywall_client=None, llm_light=object(), llm=object(), config=object(), max_chars=100,
    )
    result = pipeline.run_review("batch", deps=deps)
    assert result.candidates[0].review == {"state": "error", "error": "bad PDF"}
    assert ("light", "B") in calls and ("deep", "B") in calls
    assert not any(title == "A" for _, title in calls)
