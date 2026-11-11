"""Refinement must preserve identifier-based saved membership through explicit Add."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.search import dedup, materialize, pipeline, refine, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent
from zotero_summarizer.services.search.dedup import to_version_families


@pytest.fixture(autouse=True)
def _loaded_source_receipt(record_property):
    for module in (dedup, refine, pipeline, materialize, session):
        path = Path(module.__file__).resolve()
        record_property(module.__name__, f"{path}:{hashlib.sha256(path.read_bytes()).hexdigest()}")


class _Reader:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def find_by_external_id(self, *, doi, arxiv_id):
        self.calls.append((doi, arxiv_id))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _round(monkeypatch, tmp_path, candidates, new_candidate, reader):
    monkeypatch.setattr(session, "settings", lambda: SimpleNamespace(search_dir=tmp_path))
    stored = ResearchSession(
        id="coverage", created_at="2026-10-08T00:00:00Z", raw_query="topic",
        intent=SearchIntent(raw_query="topic"), plan=QueryPlan(),
        candidates=candidates, status="reviewing",
    )
    session.save(stored)
    monkeypatch.setattr(refine, "offline_requested", lambda: False)
    monkeypatch.setattr(refine, "refine_once", lambda *a, **kw: {"add_concepts": ["facet"], "drop_terms": []})
    monkeypatch.setattr(refine, "federate", lambda *a, **kw: [new_candidate])
    monkeypatch.setattr(refine, "score_query_relevance", lambda *a, **kw: None)
    deps = pipeline.SearchDeps(llm=None, llm_light=None, config=None, reranker_model="unused", reader=reader)
    return refine.run_agentic_rounds(stored.id, deps=deps, max_rounds=1)


def test_refinement_saved_hit_survives_persistence_and_explicit_add(monkeypatch, tmp_path):
    seed = Candidate(title="Seed", doi="10.1000/seed", query_score=0.9, in_library=False)
    new = Candidate(title="Saved", doi="10.1000/saved", query_score=0.8)
    reader = _Reader("SAVED001")
    writes = []

    def forbidden_writer(*a, **kw):
        writes.append((a, kw))
        raise AssertionError("A saved hit must not construct a Zotero writer")

    monkeypatch.setattr(materialize, "ZoteroWriter", forbidden_writer)
    out = _round(monkeypatch, tmp_path, [seed], new, reader)
    saved = session.load(out.id)
    result = next(c for c in saved.candidates if c.candidate_id == new.candidate_id)
    assert (result.in_library, result.existing_zotero_key) == (True, "SAVED001")
    assert reader.calls == [("10.1000/saved", None)]
    unchanged = saved.candidates[0]
    assert (unchanged.candidate_id, unchanged.query_score, unchanged.in_library) == (
        seed.candidate_id, seed.query_score, seed.in_library,
    )
    assert unchanged.quality == seed.quality and unchanged.review == seed.review
    assert writes == []  # Refinement itself is read-only with respect to Zotero.
    assert materialize.materialize_candidate(out.id, result.candidate_id, None) == {
        "status": "already_added", "zotero_key": "SAVED001",
    }
    assert session.load(out.id).candidates[1].materialized_zotero_key == "SAVED001"
    assert writes == []


@pytest.mark.parametrize("reader_result,expected", [(None, False), (OSError("locked"), None), ("KEY00001", True)])
@pytest.mark.parametrize("identifiers", [{"doi": "10.1000/item"}, {"arxiv_id": "2503.16416"}])
def test_refinement_coverage_uses_existing_tristate_contract(monkeypatch, tmp_path, reader_result, expected, identifiers):
    new = Candidate(title="Delta", query_score=0.8, **identifiers)
    reader = _Reader(reader_result)
    out = _round(monkeypatch, tmp_path, [], new, reader)
    restored = session.load(out.id).candidates[0]
    assert restored.in_library is expected
    assert restored.existing_zotero_key == (reader_result if expected else None)
    assert len(reader.calls) == 1
    assert restored.query_score == new.query_score
    assert restored.quality == restored.review == {}


@pytest.mark.parametrize("reader_available", [False, True])
def test_refinement_unsupported_identity_or_absent_reader_stays_unknown(monkeypatch, tmp_path, reader_available):
    reader = _Reader("DO_NOT_INFER") if reader_available else None
    new = Candidate(title="PMID only", pmid="123", query_score=0.8)
    out = _round(monkeypatch, tmp_path, [], new, reader)
    restored = session.load(out.id).candidates[0]
    assert restored.in_library is None and restored.existing_zotero_key is None
    if reader is not None:
        assert reader.calls == []


@pytest.mark.parametrize("hit_first", [False, True])
def test_family_saved_hit_beats_richer_identifier_miss(hit_first):
    hit = Candidate(title="Saved preprint", arxiv_id="2503.16416", in_library=True, existing_zotero_key="SAVED001")
    miss = Candidate(title="Journal", abstract="Richer metadata", doi="10.1000/paper", arxiv_id="2503.16416", in_library=False)
    members = [hit, miss] if hit_first else [miss, hit]
    first_address = members[0].candidate_id
    family = to_version_families(members)[0]
    assert family.title == "Journal"  # Metadata preference stays unchanged.
    assert family.candidate_id == first_address
    assert (family.in_library, family.existing_zotero_key) == (True, "SAVED001")
    assert Candidate.from_dict(family.to_dict()).in_library is True


def test_refinement_rediscovery_updates_saved_membership_without_losing_review(monkeypatch, tmp_path):
    original = Candidate(
        title="Journal", abstract="Richer original metadata", doi="10.1000/paper", arxiv_id="2503.16416",
        query_score=0.9, in_library=False, review={"state": "reviewed", "brief": {"tldr": "Retain"}},
    )
    new = Candidate(title="Preprint", arxiv_id="2503.16416", query_score=0.7)
    reader = _Reader("SAVED001")
    out = _round(monkeypatch, tmp_path, [original], new, reader)
    family = session.load(out.id).candidates[0]
    assert len(out.candidates) == 1
    assert family.candidate_id == original.candidate_id
    assert family.review == original.review and family.query_score == original.query_score
    assert (family.in_library, family.existing_zotero_key) == (True, "SAVED001")
    assert reader.calls == [(None, "2503.16416")]
    assert out.refinements[0]["new_candidates"] == 0
