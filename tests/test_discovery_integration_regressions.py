"""Real ordinary HTTP wire identity and seed-alias dependency controls."""
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from types import SimpleNamespace

from tests._zotero_fixtures import add_library_item
from tests.test_discovery_server import STABLE, journey
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations import arxiv, europepmc, openalex
from zotero_summarizer.services.search import federate as federation
from zotero_summarizer.services.search import discovery, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent


@pytest.mark.parametrize("alias", [
    {"doi": "https://doi.org/10.1/SEED"},
    {"landing_page_url": "http://arxiv.org/abs/2501.12345v2"},
    {"pdf_url": "https://arxiv.org/pdf/2501.12345v3.pdf"},
])
def test_real_openalex_location_alias_excludes_seed_without_title_matching(journey, monkeypatch, alias):
    with sqlite3.connect(journey.native_db) as conn:
        conn.execute("INSERT INTO itemDataValues(value) VALUES ('https://arxiv.org/abs/2501.12345')")
        conn.execute("INSERT OR REPLACE INTO itemData(itemID,fieldID,valueID) "
                     "SELECT itemID,3,last_insert_rowid() FROM items WHERE key='SEED0001'")
    requested = []

    def provider(request):
        requested.append(request)
        return httpx.Response(200, json={"results": [
            {"id": "W900", "doi": "10.9/new-primary", "title": "Renamed seed", "locations": [alias]},
            {"id": "W901", "doi": "10.9/distinct", "title": "Renamed seed", "locations": []},
        ]})

    with httpx.Client(transport=httpx.MockTransport(provider), trust_env=False) as http:
        client = openalex.OpenAlexClient(object(), http_client=http)
        monkeypatch.setattr(discovery, "federate", federation.federate)
        monkeypatch.setattr(discovery, "_search_openalex_client", lambda *args: client)
        metadata = discovery.discovery_metadata("SEED0001")
        response = journey.client.post("/api/library/related/SEED0001/discover", json={
            "context_fingerprint": metadata["context_fingerprint"],
        })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "discovery_ready"
    assert [row["doi"] for row in result["candidates"]] == ["10.9/distinct"]
    assert result["discovery"]["recommended_ids"] == ["10.9/distinct"]
    assert requested and all("locations" in row.url.params["select"].split(",") for row in requested)
    assert session.load(result["id"]).candidates[0].doi == "10.9/distinct"


def test_ordinary_http_wire_fixture(journey, tmp_path):
    ordinary = ResearchSession(
        id="ordinary-wire", created_at="2026-10-19T00:00:00Z", raw_query="Public ordinary query",
        intent=SearchIntent(raw_query="Public ordinary query"), plan=QueryPlan(), status="reviewing",
        candidates=[Candidate(candidate_id="ordinary-candidate", title="Public ordinary candidate")],
    )
    session.save(ordinary)
    response = journey.client.get("/api/search/ordinary-wire")
    assert response.status_code == 200
    wire = response.json()
    assert "discovery" not in wire
    assert wire["candidates"][0]["title"] == "Public ordinary candidate"
    comparison = deepcopy(wire)
    assert comparison.pop("evidence_policy", "baseline") == "baseline"
    assert comparison.pop("evidence_status", {}) == {}
    assert comparison.pop("evidence_baseline_order", []) == []
    for candidate in comparison["candidates"]:
        assert candidate.pop("question_evidence", []) == []
        assert candidate.pop("planned_zotero_key", "") == ""
    assert comparison == json.loads((Path(__file__).parent / "fixtures/discovery-ordinary-wire.json").read_text())
    (tmp_path / "ordinary-http-wire.json").write_text(json.dumps(wire, indent=2) + "\n")


def test_candidate_binding_uses_public_contract_not_later_derived_enrichment(journey):
    @dataclass
    class EnrichedCandidate(Candidate):
        planned_zotero_key: str = ""
        question_evidence: list = field(default_factory=list)

    metadata = discovery.discovery_metadata(STABLE)
    result = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
        "context_fingerprint": metadata["context_fingerprint"],
    }).json()
    saved = session.load(result["id"])
    identity = discovery._candidate_fingerprint(saved)
    original = saved.candidates[0]
    saved.candidates[0] = EnrichedCandidate(**{**original.to_dict(), "planned_zotero_key": "PLAN0001",
                                              "question_evidence": [{"status": "derived-only"}]})
    assert discovery._candidate_fingerprint(saved) == identity
    saved.candidates[0].title = "Changed public metadata"
    assert discovery._candidate_fingerprint(saved) != identity


@pytest.mark.parametrize("graph", ["direct", "transitive"])
@pytest.mark.parametrize("change", ["edit", "clear"])
def test_identifier_linked_unmaterialized_app_alias_context_is_complete(journey, graph, change):
    from zotero_summarizer.storage import repositories

    alias = "feed:g:" + "c" * 64
    target = "feed:g:" + "d" * 64 if graph == "transitive" else alias
    with sqlite3.connect(journey.paths.triage_db_path) as conn:
        conn.execute("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                     "title, doi, arxiv_id, guid, decision, run_id) "
                     "VALUES (2, 91, ?, 'App alias', '10.1/seed', '2501.12345', 'app-alias', 'user_approved', 'test')",
                     (alias,))
        if graph == "transitive":
            conn.execute("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                         "title, arxiv_id, guid, decision, run_id) "
                         "VALUES (2, 92, ?, 'Transitive app alias', '2501.12345', 'app-alias-2', 'selected', 'test')",
                         (target,))
    sentinel = "Не повторять этот метод. APP_IDENTIFIER_CONTEXT"
    repositories.upsert_review_note(journey.paths.triage_db_path, target, sentinel)
    metadata = discovery.discovery_metadata("SEED0001")
    response = journey.client.post("/api/library/related/SEED0001/discover", json={
        "context_fingerprint": metadata["context_fingerprint"], "consent_token": metadata["boundary"]["token"],
    })
    assert response.status_code == 200
    result = response.json()
    assert sentinel in {row["text"] for row in journey.payloads[0]["seed"]["comments"]["records"]}
    assert sentinel not in json.dumps(journey.requests, ensure_ascii=False)
    repositories.upsert_review_note(journey.paths.triage_db_path, target, "Edited context" if change == "edit" else "")
    stale = journey.client.get(f"/api/search/{result['id']}").json()["discovery"]
    assert stale["freshness"] == "stale"
    assert stale["seed_comments"] is None
    assert stale["assessment"]["items"] == []


def test_disabled_metadata_features_prepare_cache_before_discovery_identity(journey, monkeypatch):
    from zotero_summarizer.models import AppState
    from zotero_summarizer.runtime import RuntimeState
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services.search import pipeline
    from zotero_summarizer.storage.corpus_read import _corpus_fingerprint

    for name in ("prestige", "full_text_refine", "openreview"):
        getattr(journey.config, name).enabled = False
    runtime = RuntimeState(app_state=AppState(config=journey.config))
    lifecycle._init_metadata_clients(runtime, journey.config, journey.paths)
    assert runtime.openalex_cache is not None
    assert runtime.openalex_client is runtime.unpaywall_client is runtime.openreview_client is None
    identity = _corpus_fingerprint(str(journey.paths.corpus_db_path))
    monkeypatch.setattr(pipeline, "settings", lambda: journey.paths)
    monkeypatch.setattr(pipeline, "_SEARCH_OPENALEX", None)
    client = pipeline._search_openalex_client(runtime, journey.config)
    try:
        assert _corpus_fingerprint(str(journey.paths.corpus_db_path)) == identity
    finally:
        client._http.close()


@pytest.mark.parametrize("cached", [False, True])
def test_discovery_search_client_uses_runtime_cache_without_prestige(journey, monkeypatch, cached):
    from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
    from zotero_summarizer.models import AppState
    from zotero_summarizer.runtime import RuntimeState
    from zotero_summarizer.services.search import pipeline

    cache = OpenAlexCache(journey.paths.corpus_db_path) if cached else None
    runtime = RuntimeState(app_state=AppState(config=journey.config), openalex_cache=cache)
    monkeypatch.setattr(pipeline, "settings", lambda: journey.paths)
    monkeypatch.setattr(pipeline, "_SEARCH_OPENALEX", None)
    client = pipeline._search_openalex_client(runtime, journey.config)
    try:
        assert isinstance(client.cache, OpenAlexCache)
        if cached:
            assert client.cache is cache
        assert pipeline._search_openalex_client(runtime, journey.config) is client
    finally:
        client._http.close()


def test_discovery_without_native_reader_keeps_membership_unknown(journey):
    journey.runtime.zotero_reader = None
    metadata = discovery.discovery_metadata(STABLE)
    assert metadata["admission"] == {"state": "saved", "basis": "user_approved"}
    response = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
        "context_fingerprint": metadata["context_fingerprint"],
    })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "discovery_ready"
    assert result["discovery"]["recommended_ids"] == []
    assert all(row["in_library"] is None for row in result["candidates"])
    assert set(result["discovery"]["unresolved_membership"]) == {
        row["candidate_id"] for row in result["candidates"]}
    assert result["discovery"]["retrieval"]["empty_reason"] is None


def test_discovery_respects_disabled_reranker_without_loading_models(journey, monkeypatch):
    from zotero_summarizer.services.search import rank

    journey.config.corpus.reranker_enabled = False
    monkeypatch.setattr(discovery, "score_query_relevance", rank.score_query_relevance)

    def forbidden(*args):
        pytest.fail("Disabled Discovery reranker attempted model construction")

    monkeypatch.setattr(rank, "get_reranker", forbidden)
    metadata = discovery.discovery_metadata(STABLE)
    response = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
        "context_fingerprint": metadata["context_fingerprint"],
    })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "discovery_ready"
    candidate = next(row for row in result["candidates"] if row["doi"] == "10.1/new")
    assert candidate["query_score"] == rank._lexical_score(result["raw_query"],
                                                          Candidate.from_dict(candidate))
    assert result["discovery"]["freshness"] == "current"
    assert journey.payloads == []


def test_discovery_context_read_failure_refuses_without_clients_or_sessions(journey, monkeypatch):
    def unavailable(*args):
        raise OSError("Controlled context read failure")

    monkeypatch.setattr(discovery, "read_context", unavailable)
    response = journey.client.get(f"/api/library/related/{STABLE}/discover")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["admission"]["state"] == "unknown"
    assert metadata["context_fingerprint"] is None
    assert metadata["reason"] == "discovery_unverified"
    assert journey.requests == journey.payloads == []
    assert not journey.paths.search_dir.exists()


@pytest.mark.parametrize("semantic_error", ["rate_limit", "nonfinite"])
def test_api_real_provider_receipts_keep_partial_results_and_private_context_separate(journey, monkeypatch, semantic_error):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>')
        if request.url.host == "www.ebi.ac.uk":
            return httpx.Response(200, json={"resultList": {"result": []}})
        assert request.url.host == "api.openalex.org"
        if "search.semantic" in request.url.params:
            if semantic_error == "nonfinite":
                return httpx.Response(200, text='{"results":[{"id":"W2","doi":"10.1/bad","title":"Bad year","publication_year":1e9999}]}')
            return httpx.Response(429, text="Controlled rate limit")
        return httpx.Response(200, json={"results": [{"id": "W1", "doi": "10.1/new",
            "title": "Public controlled evaluation", "abstract_inverted_index": {"Controlled": [0], "evaluation": [1]}}]})

    for module in (arxiv, europepmc, openalex):
        monkeypatch.setattr(module, "_RATE_LIMITER", SimpleNamespace(acquire=lambda: None))
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        client = openalex.OpenAlexClient(object(), http_client=http)
        monkeypatch.setattr(discovery, "federate", federation.federate)
        monkeypatch.setattr(discovery, "_search_openalex_client", lambda *args: client)
        monkeypatch.setattr(federation, "search_arxiv", partial(arxiv.search_arxiv, http_client=http))
        monkeypatch.setattr(federation, "search_europepmc", partial(europepmc.search_europepmc, http_client=http))
        metadata = discovery.discovery_metadata(STABLE)
        response = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
            "context_fingerprint": metadata["context_fingerprint"], "consent_token": metadata["boundary"]["token"],
        })
    assert response.status_code == 200
    result = response.json()
    reopened = journey.client.get(f"/api/search/{result['id']}").json()
    assert reopened["discovery"]["freshness"] == "current", {
        "post": result["discovery"]["freshness"], "reopened": reopened["discovery"]["freshness"],
        "candidates": result["candidates"],
    }
    assert result["discovery"]["retrieval"]["status"] == "partial"
    assert result["discovery"]["retrieval"]["empty_reason"] is None
    assert result["discovery"]["assessment"]["status"] == "assessed"
    assert len(result["candidates"]) == len(result["discovery"]["recommended_ids"]) == 1
    outcomes = {row["channel"]: row["outcome"] for row in result["discovery"]["retrieval"]["variants"]}
    assert outcomes["openalex_semantic"] == {"status": "failed", "count": None,
                                             "error_code": "parse_error" if semantic_error == "nonfinite" else "rate_limited"}
    assert outcomes["openalex_lexical"]["status"] == "success_nonempty"
    assert set(outcomes) == {"openalex_lexical", "openalex_semantic"}
    assert all(httpx.URL(url).host == "api.openalex.org" for url in requests)
    assert requests and all("PRIVATE_SENTINEL" not in url and "%D0%9D%D0%B5" not in url for url in requests)
    assert any("PRIVATE_SENTINEL" in row["text"] for row in journey.payloads[0]["seed"]["comments"]["records"])
    assert reopened["discovery"]["freshness"] == "current"


@pytest.mark.parametrize("change", ["edit", "delete"])
@pytest.mark.parametrize("graph", ["direct", "transitive", "mixed"])
def test_app_linked_native_identifier_family_owns_complete_context(journey, change, graph):
    with sqlite3.connect(journey.paths.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET doi=NULL, arxiv_id=NULL")
    bridge = "https://arxiv.org/abs/2501.12345"
    alias = add_library_item(journey.native_db, item_key="SEED0002", title="Native alias", doi="10.1/seed",
                             url=bridge if graph == "transitive" else None)
    if graph == "transitive":
        alias = add_library_item(journey.native_db, item_key="SEED0003", title="Transitive alias", url=bridge)
    elif graph == "mixed":
        alias = add_library_item(journey.native_db, item_key="SEED0003", title="Explicit app alias", doi="10.1/mixed")
        with sqlite3.connect(journey.paths.triage_db_path) as conn:
            conn.executemany("INSERT INTO processed_feed_items (feed_library_id, feed_item_id, stable_feed_key, "
                             "materialized_zotero_key, title, guid, decision, run_id) "
                             "VALUES (2, ?, ?, ?, 'Alias history', ?, 'selected', 'test')", [
                (73, "feed:g:" + "b" * 64, "SEED0002", "old-alias"),
                (74, "feed:g:" + "b" * 64, "SEED0003", "new-alias"),
            ])
    comment = add_library_item(journey.native_db, item_key="ALIASNOT", title="Alias note", item_type="note")
    other = add_library_item(journey.native_db, item_key="OTHER001", title="Unrelated", doi="10.1/other")
    unrelated = add_library_item(journey.native_db, item_key="OTHERNOT", title="Unrelated note", item_type="note")
    sentinel = "Не рекомендовать этот вариант. ALIAS_PRIVATE_CONTEXT"
    with sqlite3.connect(journey.native_db) as conn:
        conn.executemany("INSERT INTO itemNotes(itemID,parentItemID,note) VALUES (?,?,?)", [
            (comment, alias, "<p>" + sentinel + "</p>"), (unrelated, other, "Unrelated context"),
        ])
    metadata = discovery.discovery_metadata(STABLE)
    assert metadata["admission"]["state"] == "saved"
    response = journey.client.post(f"/api/library/related/{STABLE}/discover", json={
        "context_fingerprint": metadata["context_fingerprint"], "consent_token": metadata["boundary"]["token"],
    })
    assert response.status_code == 200
    result = response.json()
    assert sentinel in {row["text"] for row in journey.payloads[0]["seed"]["comments"]["records"]}
    with sqlite3.connect(journey.native_db) as conn:
        conn.execute("UPDATE itemNotes SET note='Unrelated changed' WHERE itemID=?", (unrelated,))
    assert discovery.discovery_view(session.load(result["id"]))["discovery"]["freshness"] == "current"
    with sqlite3.connect(journey.native_db) as conn:
        if change == "edit":
            conn.execute("UPDATE itemNotes SET note='Edited alias context' WHERE itemID=?", (comment,))
        else:
            conn.execute("INSERT INTO deletedItems(itemID) VALUES (?)", (comment,))
    stale = journey.client.get(f"/api/search/{result['id']}").json()
    assert stale["discovery"]["freshness"] == "stale"
    assert stale["discovery"]["seed_comments"] is None
    assert stale["discovery"]["assessment"]["items"] == []
    assert sentinel not in json.dumps(stale, ensure_ascii=False)
    assert journey.client.post(f"/api/search/{result['id']}/review", json={}).status_code == 409
    with pytest.raises(APIError) as error:
        discovery.require_current_discovery(session.load(result["id"]))
    assert error.value.status_code == 409
