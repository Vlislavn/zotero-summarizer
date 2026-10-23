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
    assert wire["discovery"] == {}
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
