"""Owned Search assembly; actual parser, grounding, policy and store stay active."""
import json
from html import unescape
from types import SimpleNamespace

import pytest

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.config import LLMConfig
from zotero_summarizer.services import _common
from zotero_summarizer.services.search import _question_evidence as evidence
from zotero_summarizer.services.search import pipeline, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent

QUESTION = "Did the treatment improve the outcome?"
QUOTE = "The registered treatment did not improve the outcome in this controlled study."


class Generator:
    def __init__(self):
        self.calls = []
        self.on_call = None
        self.fail = False
        self.output = {"answer": "The treatment did not improve the outcome", "quote": QUOTE}

    def prompt(self, prompt):
        self.calls.append(prompt)
        if self.on_call:
            self.on_call()
        if self.fail:
            raise RuntimeError("private synthetic provider details")
        return json.dumps(self.output)


class Verifier:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.state = None

    def pydantic_prompt(self, *, prompt, pydantic_model):
        payload = json.loads(unescape(prompt.rsplit("<untrusted_input>", 1)[1].split("</untrusted_input>")[0]))
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError("private synthetic verifier details")
        state = self.state or ("sufficient" if payload["original_passages"][0]["text"].startswith("P11") else "unknown")
        return pydantic_model(state=state, evidence_ids=[payload["evidence"][0]["passage_id"]]
                              if state in {"sufficient", "partial"} else [],
                              uncovered=["Synthetic missing aspect"] if state == "partial" else [])


@pytest.fixture
def journey(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "settings", lambda: SimpleNamespace(search_dir=tmp_path / "sessions"))
    monkeypatch.delenv("ZS_OFFLINE", raising=False)
    for key in ("CANDIDATES", "CALLS", "TOPIC_FLOOR"):
        monkeypatch.delenv(f"ZS_SEARCH_EVIDENCE_{key}", raising=False)
    config = GoalsConfig(relevance_scale={n: f"Synthetic {n}" for n in range(1, 6)},
                         llm=LLMConfig(draft_model="synthetic-generator", refine_model="synthetic-verifier",
                                       api_base="http://127.0.0.1:9999/v1", api_key_env="SYNTHETIC_KEY"))
    config.llm_routing.feed.model = "synthetic-generator"
    config.llm_routing.deep_review.model = "synthetic-verifier"
    monkeypatch.setattr(_common.state(), "app_state", SimpleNamespace(config=config))
    generator, verifier = Generator(), Verifier()
    deps = pipeline.SearchDeps(llm=verifier, llm_light=generator, config=config, reranker_model="synthetic",
                               extractor=object(), max_chars=60000)
    acquired, light, deep = [], [], []

    def acquire(candidate, **kwargs):
        acquired.append(candidate.candidate_id)
        return candidate.candidate_id + ": original text\n" + QUOTE

    def retrieve(text, questions, **kwargs):
        return [[{"start": 0, "end": len(text), "text": text}] for _ in questions], {
            "dense": True, "bm25": True, "reranker": False, "embedder_model": "synthetic", "reranker_model": "synthetic"}

    monkeypatch.setattr(evidence, "acquire_full_text", acquire)
    monkeypatch.setattr(evidence, "retrieve_original_passages", retrieve)
    monkeypatch.setattr(pipeline, "acquire_full_text", acquire)
    monkeypatch.setattr(pipeline, "light_review", lambda c, **kw: light.append(c.candidate_id))
    monkeypatch.setattr(pipeline, "targeted_review", lambda c, **kw: deep.append(c.candidate_id))
    return SimpleNamespace(deps=deps, generator=generator, verifier=verifier, acquired=acquired, light=light, deep=deep)


def stored_session(policy="question_evidence", questions=None, name="question-evidence"):
    return ResearchSession(id=name, created_at="2026-10-18", raw_query="treatment studies",
                           intent=SearchIntent(raw_query="treatment studies"), plan=QueryPlan(),
                           questions=[QUESTION] if questions is None else questions, status="reviewing",
                           evidence_policy=policy,
                           candidates=[Candidate(title=f"Paper {n}", candidate_id=f"P{n:02}",
                                                 query_score=0.99 - n * 0.04) for n in range(1, 14)])


def run(journey, **kwargs):
    research = stored_session(**kwargs)
    session.save(research)
    return pipeline.run_review(research.id, deps=journey.deps)
