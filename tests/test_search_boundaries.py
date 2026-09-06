"""Search HTTP/work boundaries and durable candidate addressing."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zotero_summarizer.api.errors import APIError, install_error_handlers
from zotero_summarizer.api.routes import search
from zotero_summarizer.services.search import _fulltext, pipeline, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent
from tests.test_search_materialize import _store, _session


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(search.router)
    install_error_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as http:
        yield http


@pytest.mark.parametrize("payload", [
    {"query": "   "}, {"query": "x" * 4001},
    {"query": "q", "questions": ["Why?"] * 11},
    {"query": "q", "questions": ["x" * 1001]},
    {"query": "q", "questions": ["   "]},
    {"query": "q", "questions": ["Why?"] + [""] * 10_000},
])
def test_screen_rejects_unbounded_or_blank_work_before_dependencies(client, monkeypatch, payload):
    deps = Mock(side_effect=AssertionError("invalid work reached provider construction"))
    monkeypatch.setattr(search, "default_deps", deps)

    response = client.post("/api/search/screen", json=payload)

    assert response.status_code == 422, response.text
    deps.assert_not_called()


@pytest.mark.parametrize("entry", ["http_review", "direct_review", "pmc", "doi"])
def test_offline_rechecks_persisted_review_and_fulltext_boundaries(client, monkeypatch, tmp_path, entry):
    _store(monkeypatch, tmp_path)
    research = _session("offline", Candidate(title="Paper", pmcid="PMC123", doi="10.1/test"))
    session.save(research)
    before = (tmp_path / "offline.json").read_bytes()
    monkeypatch.setenv("ZS_OFFLINE", "1")
    thread = Mock()
    monkeypatch.setattr(search, "threading", SimpleNamespace(Thread=thread))
    pmc = Mock(return_value="forbidden network text")
    oa = Mock(return_value="https://example.org/paper.pdf")
    fetch = Mock(return_value=None)
    monkeypatch.setattr(_fulltext.europepmc, "fetch_fulltext_xml", pmc)
    monkeypatch.setattr(_fulltext.pdf_fetch, "resolve_pdf_url", oa)
    monkeypatch.setattr(_fulltext.pdf_fetch, "fetch_pdf", fetch)
    review = Mock()
    monkeypatch.setattr(pipeline, "light_review", review)
    deep_review = Mock()
    monkeypatch.setattr(pipeline, "targeted_review", deep_review)
    deps = SimpleNamespace(extractor=Mock(), unpaywall_client=Mock(), llm_light=Mock(), llm=Mock(), max_chars=100,
                           config=SimpleNamespace(quality_review=SimpleNamespace(max_text_chars=100)))

    if entry == "http_review":
        response = client.post("/api/search/offline/review")
        assert response.status_code == 409 and response.json()["error"] == "strict_offline"
    else:
        with pytest.raises(APIError) as error:
            if entry == "direct_review":
                pipeline.run_review("offline", deps=deps)
            else:
                candidate = research.candidates[0] if entry == "pmc" else Candidate(title="DOI", doi="10.1/test")
                _fulltext.acquire_full_text(candidate, extractor=deps.extractor, unpaywall=deps.unpaywall_client)
        assert error.value.error == "strict_offline"
    assert (tmp_path / "offline.json").read_bytes() == before
    assert not thread.mock_calls and not pmc.mock_calls and not oa.mock_calls and not fetch.mock_calls
    review.assert_not_called()
    deep_review.assert_not_called()


def test_same_title_cards_are_addressable_over_http_and_do_not_cross_stamp(client, monkeypatch, tmp_path):
    from zotero_summarizer.services.search import materialize

    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(materialize, "settings", lambda: SimpleNamespace(zotero_data_dir=tmp_path))
    writer = Mock()
    monkeypatch.setattr(materialize, "ZoteroWriter", Mock(return_value=writer))
    research = _session("same-title", Candidate(title="Same title", authors=["Alice"], abstract="<b>First</b>"))
    research.raw_query = "<script>query</script>"
    research.candidates.append(Candidate(title="Same title", authors=["Bob"]))
    session.save(research)

    cards = client.get("/api/search/same-title").json()["candidates"]
    assert all(card.get("candidate_id") for card in cards)
    assert cards[0]["candidate_id"] != cards[1]["candidate_id"]
    results = [client.post("/api/search/same-title/materialize", json={"candidate_id": card["candidate_id"]})
               for card in cards]

    assert [result.status_code for result in results] == [200, 200]
    assert [result.json()["status"] for result in results] == ["added", "added"]
    assert [call.kwargs["feed_payload"]["authors"] for call in writer.apply_feed_materialization.call_args_list] == [
        ["Alice"], ["Bob"]]
    note = writer.apply_feed_materialization.call_args_list[0].kwargs["note_html"]
    assert "&lt;script&gt;query&lt;/script&gt;" in note and "&lt;b&gt;First&lt;/b&gt;" in note
    assert "source=targeted-search" in note and "<script>" not in note
    keys = [result.json()["zotero_key"] for result in results]
    assert keys[0] != keys[1]
    research.candidates.reverse()  # stale review snapshot must merge by ID, not title or row position
    session.save_merge(research)
    reloaded = client.get("/api/search/same-title").json()["candidates"]
    assert {card["authors"][0]: card["materialized_zotero_key"] for card in reloaded} == dict(zip(["Alice", "Bob"], keys))


def test_legacy_identifierless_session_ids_survive_repeated_load_and_save(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    research = _session("legacy", Candidate(title="Same", authors=["Alice"]))
    research.candidates.append(Candidate(title="Same", authors=["Bob"]))
    raw = research.to_dict()
    for card in raw["candidates"]:
        card.pop("candidate_id", None)
    (tmp_path / "legacy.json").write_text(json.dumps(raw))

    first = session.load("legacy")
    second = session.load("legacy")
    ids = [card.candidate_id for card in first.candidates]
    assert len(set(ids)) == 2
    assert [card.candidate_id for card in second.candidates] == ids
    first.candidates.reverse()
    session.save(first)
    assert [card.candidate_id for card in session.load("legacy").candidates] == ids[::-1]


def test_candidate_identity_does_not_change_when_identifiers_are_enriched():
    candidate = Candidate(title="Paper")
    original = candidate.candidate_id
    candidate.doi = "10.1/new"
    candidate.title = "Corrected title"
    assert candidate.candidate_id == original
    assert Candidate.from_dict(candidate.to_dict()).candidate_id == original


@pytest.mark.parametrize("damage", ["duplicate", "blank", "null", "list"])
def test_corrupt_persisted_addresses_fail_before_a_candidate_can_be_targeted(monkeypatch, tmp_path, damage):
    _store(monkeypatch, tmp_path)
    raw = _session("corrupt", Candidate(title="Paper", doi="10.1/test")).to_dict()
    if damage == "duplicate":
        raw["candidates"].append(dict(raw["candidates"][0]))
    else:
        raw["candidates"][0]["candidate_id"] = {"blank": "", "null": None, "list": []}[damage]
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    write = Mock()

    with pytest.raises(ValueError):
        session.materialize_once("corrupt", "10.1/test", write)
    write.assert_not_called()
    assert path.read_bytes() == before


def test_federation_enrichment_preserves_existing_family_address():
    from zotero_summarizer.services.search.dedup import to_version_families

    old = Candidate(title="Paper", pmid="123", materialized_zotero_key="EXISTING")
    address = old.candidate_id
    richer = Candidate(title="Paper", pmid="123", doi="10.1/new", abstract="Rich abstract")
    merged, = to_version_families([old, richer])
    assert merged.candidate_id == address
    assert merged.doi == "10.1/new" and merged.materialized_zotero_key == "EXISTING"


@pytest.mark.parametrize("entry", ["screen", "review", "claim"])
def test_direct_or_persisted_work_cannot_bypass_the_question_budget(monkeypatch, tmp_path, entry):
    _store(monkeypatch, tmp_path)
    research = _session("too-many", Candidate(title="Paper"))
    research.questions = ["Why?"] * 11
    session.save(research)
    before = (tmp_path / "too-many.json").read_bytes()
    parse = Mock()
    monkeypatch.setattr(pipeline, "parse_intent", parse)
    acquire = Mock()
    monkeypatch.setattr(pipeline, "acquire_full_text", acquire)

    with pytest.raises(ValueError):
        if entry == "screen":
            pipeline.run_screen("query", research.questions, deps=Mock())
        elif entry == "review":
            pipeline.run_review("too-many", deps=Mock())
        else:
            session.claim("too-many", expect="screened", to="reviewing")
    assert not parse.mock_calls and not acquire.mock_calls
    assert (tmp_path / "too-many.json").read_bytes() == before


def test_maximum_screen_input_runs_the_actual_pipeline_and_persists_normalized_questions(client, monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    llm = Mock()
    llm.prompt.return_value = '{"canonical_question": "Valid topic", "concepts": ["topic"]}'
    deps = pipeline.SearchDeps(llm=llm, llm_light=None, config=Mock(), reranker_model="test")
    monkeypatch.setattr(search, "default_deps", lambda: deps)
    monkeypatch.setattr(search, "_kickoff_review", lambda session_id: False)
    monkeypatch.setattr(pipeline, "federate", lambda *args, **kwargs: [Candidate(title="Paper", doi="10.1/test")])
    monkeypatch.setattr(pipeline, "score_query_relevance", lambda *args, **kwargs: "test")
    payload = {"query": "q" * 4000, "questions": ["x" * 1000 for _ in range(10)]}

    response = client.post("/api/search/screen", json=payload)

    assert response.status_code == 200, response.text
    stored = session.load(response.json()["id"])
    assert stored.raw_query == payload["query"] and stored.questions == payload["questions"]
    assert response.json()["candidates"][0]["candidate_id"] == "10.1/test"
    llm.prompt.assert_called_once()
    assert len(llm.prompt.call_args.args[0]) < 16_000


def test_screen_input_trims_without_silently_dropping_questions():
    request = search.ScreenRequest(query="  topic  ", questions=["  Why?  "])
    assert request.query == "topic" and request.questions == ["Why?"]
    with pytest.raises(ValueError):
        search.ScreenRequest(query="topic", questions=["Why?", "   "])


def test_http_plan_exposes_all_executed_variants_in_server_order(client, monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    research = _session("plan", Candidate(title="Paper", doi="10.1/test"))
    research.plan = QueryPlan(arxiv="broad topic", arxiv_variants=['"precise topic"', "broad topic"],
                              openreview="peer-reviewed topic")
    session.save(research)

    response = client.get("/api/search/plan")

    assert response.status_code == 200
    assert response.json()["plan"]["display"] == [
        {"source": "arxiv", "query": '"precise topic"'}, {"source": "arxiv", "query": "broad topic"},
        {"source": "openreview", "query": "peer-reviewed topic"},
    ]
