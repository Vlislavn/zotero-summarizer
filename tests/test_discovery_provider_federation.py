"""Federation keeps real leaf receipts on the existing accounting request rows."""
from __future__ import annotations

import json
from functools import partial
from threading import Barrier, Lock
from types import SimpleNamespace

import httpx
import pytest

from zotero_summarizer.integrations import arxiv, europepmc, openalex
from zotero_summarizer.services.search import federate as fed
from zotero_summarizer.services.search._models import Candidate, QueryPlan

EMPTY_ATOM = '<feed xmlns="http://www.w3.org/2005/Atom" />'
RAW_BODY = "RAW_BODY_MARKER credential=not-a-real-credential"
PUBLIC_QUERY = "public title BAD_QUERY_MARKER"


@pytest.fixture(autouse=True)
def no_rate_wait(monkeypatch):
    for module in (arxiv, europepmc, openalex):
        monkeypatch.setattr(module, "_RATE_LIMITER", SimpleNamespace(acquire=lambda: None))


def variants(plan):
    return [row for source in plan.retrieval_accounting.values() for row in source.get("variants", [])]


def test_bad_numeric_variant_does_not_abort_valid_neighbor(monkeypatch):
    def handler(request):
        if request.url.params["search"] == "malformed-count":
            return httpx.Response(200, text='{"results":[{"id":"W1","title":"Bad count","cited_by_count":1e9999}]}')
        assert request.url.params["search"] == "valid-neighbor"
        return httpx.Response(200, json={"results": [{"id": "W2", "title": "Valid neighbor", "cited_by_count": 12}]})

    plan = QueryPlan(openalex_lexical_variants=["malformed-count", "valid-neighbor"])
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        client = openalex.OpenAlexClient(object(), http_client=http)
        candidates = fed.federate(plan, openalex_client=client, quota=4, observe_outcomes=True)
    assert [candidate.title for candidate in candidates] == ["Valid neighbor"]
    rows = {row["query"]: row["outcome"] for row in variants(plan)}
    assert rows["malformed-count"] == {"status": "failed", "count": None, "error_code": "parse_error"}
    assert rows["valid-neighbor"] == {"status": "success_nonempty", "count": 1, "error_code": None}


def test_real_leaves_concurrent_variants_keep_independent_receipts(monkeypatch):
    barrier, lock = Barrier(7), Lock()
    received, receipts = [], []

    def handler(request):
        query = request.url.params.get("search_query") or request.url.params.get("query")
        query = query or request.url.params.get("search") or request.url.params.get("search.semantic")
        with lock:
            received.append(query)
        barrier.wait(timeout=5)
        if query == "all:arxiv-empty":
            return httpx.Response(200, text=EMPTY_ATOM)
        if query == "all:arxiv-rate":
            return httpx.Response(429, text=RAW_BODY)
        if query == "epmc-hit":
            return httpx.Response(200, json={"resultList": {"result": [
                {"title": "Public work", "doi": "10.1234/shared", "pmid": "123"},
            ]}})
        if query == "epmc-malformed":
            return httpx.Response(200, text=RAW_BODY)
        if query == PUBLIC_QUERY:
            return httpx.Response(200, json={"results": [
                {"id": "W1", "title": "Public work", "doi": "10.1234/shared"},
                {"id": "W2", "title": "Public work", "doi": "10.1234/shared"},
            ]})
        if query == "lexical-empty":
            return httpx.Response(200, json={"results": []})
        assert query == "semantic-transport"
        raise httpx.ReadTimeout(RAW_BODY + " https://invalid.example/" + PUBLIC_QUERY)

    def track(search):
        def run(query, **kwargs):
            with lock:
                receipts.append(kwargs["outcome"])
            return search(query, **kwargs)
        return run

    plan = QueryPlan(
        arxiv_variants=["arxiv-empty", "arxiv-rate"],
        europepmc_variants=["epmc-hit", "epmc-malformed"],
        openalex_lexical_variants=[PUBLIC_QUERY, "lexical-empty", "unscheduled-third"],
        openalex_semantic="semantic-transport",
    )
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        monkeypatch.setattr(fed, "search_arxiv", partial(arxiv.search_arxiv, http_client=http))
        monkeypatch.setattr(fed, "search_europepmc", partial(europepmc.search_europepmc, http_client=http))
        client = openalex.OpenAlexClient(object(), http_client=http)
        monkeypatch.setattr(client, "search_works", track(client.search_works))
        candidates = fed.federate(plan, openalex_client=client, quota=5, observe_outcomes=True)

    assert len(received) == 7
    assert len(receipts) == len({id(receipt) for receipt in receipts}) == 3
    rows = {row["query"]: row for row in variants(plan)}
    expected = {
        PUBLIC_QUERY: ("openalex_lexical", "success_nonempty", 2, None),
        "lexical-empty": ("openalex_lexical", "success_empty", 0, None),
        "semantic-transport": ("openalex_semantic", "failed", None, "transport_error"),
    }
    ordinary = {"arxiv-empty": 0, "arxiv-rate": 0, "epmc-hit": 1, "epmc-malformed": 0}
    assert set(rows) == set(expected) | set(ordinary)
    for query, count in ordinary.items():
        assert "outcome" not in rows[query] and "channel" not in rows[query]
        assert rows[query]["observations"] == count
    for query, (channel, status, count, code) in expected.items():
        row = rows[query]
        assert row["channel"] == channel
        assert row["outcome"] == {"status": status, "count": count, "error_code": code}
        assert any(row["outcome"] is receipt for receipt in receipts)
        assert row["allocation"] == (5 if channel == "openalex_semantic" else 3 if query in (
            "arxiv-empty", "epmc-hit", PUBLIC_QUERY) else 2)
    assert len(candidates) == 1  # three observations, one identifier family; receipt counts remain 1 and 2
    assert plan.retrieval_accounting["openalex"]["families"] == 1
    assert rows[PUBLIC_QUERY]["outcome"]["count"] == 2
    assert QueryPlan.from_dict(json.loads(json.dumps(plan.to_dict()))).retrieval_accounting == plan.retrieval_accounting
    encoded_receipts = json.dumps([row["outcome"] for row in rows.values() if "outcome" in row])
    assert "RAW_BODY_MARKER" not in encoded_receipts
    assert "BAD_QUERY_MARKER" not in encoded_receipts
    assert "credential" not in encoded_receipts
    assert "https://" not in encoded_receipts
    assert "BAD_QUERY_MARKER" in json.dumps(rows[PUBLIC_QUERY]["query"])  # existing PUBLIC query remains


@pytest.mark.parametrize("missing", [False, True])
def test_scheduled_openalex_disabled_or_missing_is_not_attempted(missing):
    def unexpected(request):
        pytest.fail("unavailable client attempted HTTP")

    plan = QueryPlan(openalex_lexical_variants=["tight", "bag"], openalex_semantic="semantic")
    with httpx.Client(transport=httpx.MockTransport(unexpected), trust_env=False) as http:
        client = None if missing else openalex.OpenAlexClient(object(), http_client=http, allow_network=False)
        assert fed.federate(plan, openalex_client=client, observe_outcomes=True) == []
    rows = variants(plan)
    assert len(rows) == len({id(row["outcome"]) for row in rows}) == 3
    for row in rows:
        assert row["outcome"] == {
            "status": "not_attempted", "count": None,
            "error_code": "client_unavailable" if missing else "network_disabled",
        }


@pytest.mark.parametrize("nonempty", [False, True])
def test_uninstrumented_leaf_keeps_no_receipt_even_with_observations(monkeypatch, nonempty):
    def silent_leaf(query, *, max_results):
        return [arxiv.ArxivHit(arxiv_id="2501.00001", title="Public", abstract="")] if nonempty else []

    monkeypatch.setattr(fed, "search_arxiv", silent_leaf)
    plan = QueryPlan(arxiv="public")
    assert len(fed.federate(plan, observe_outcomes=True)) == int(nonempty)
    assert "outcome" not in variants(plan)[0] and "channel" not in variants(plan)[0]
    assert variants(plan)[0]["observations"] == int(nonempty)


@pytest.mark.parametrize("options", [{}, {"observe_outcomes": False}])
def test_default_false_preserves_narrow_clients_and_all_other_channels(monkeypatch, options):
    called = []

    def arxiv_search(query, *, max_results):
        called.append(("arxiv", max_results))
        return []

    def epmc_search(query, *, page_size):
        called.append(("europepmc", page_size))
        return []

    class NarrowOpenAlex:
        def search_works(self, query, *, per_page, semantic):
            called.append(("semantic" if semantic else "lexical", per_page))
            return []

    def crossref_search(query, *, rows, mailto):
        assert mailto == "public@example.invalid"
        called.append(("crossref", rows))
        return []

    def scholar_search(query, *, limit):
        called.append(("semantic_scholar", limit))
        return []

    def openreview_search(client, query, *, limit):
        called.append(("openreview", limit))
        return []

    monkeypatch.setattr(fed, "search_arxiv", arxiv_search)
    monkeypatch.setattr(fed, "search_europepmc", epmc_search)
    monkeypatch.setattr(fed, "search_crossref", crossref_search)
    monkeypatch.setattr(fed, "search_semantic_scholar", scholar_search)
    monkeypatch.setattr(fed, "search_openreview", openreview_search)
    plan = QueryPlan(arxiv="a", europepmc="e", openalex_lexical="l", openalex_semantic="s",
                     crossref="c", semantic_scholar="ss", openreview="or", library_raw="lib")
    candidates = fed.federate(
        plan, openalex_client=NarrowOpenAlex(), openreview_client=object(), quota=5,
        crossref_mailto="public@example.invalid",
        library_finder=lambda query: [Candidate(title="Library result")], **options,
    )
    assert len(candidates) == 1
    assert sorted(called) == sorted((name, 5) for name in (
        "arxiv", "europepmc", "lexical", "semantic", "crossref", "semantic_scholar", "openreview"))
    assert len(variants(plan)) == 8
    assert all("channel" not in row and "outcome" not in row for row in variants(plan))
    assert candidates[0].provenance[0].source == "library"


def test_opt_in_does_not_instrument_other_ordinary_channels(monkeypatch):
    monkeypatch.setattr(fed, "search_crossref", lambda query, *, rows, mailto: [])
    monkeypatch.setattr(fed, "search_semantic_scholar", lambda query, *, limit: [])
    monkeypatch.setattr(fed, "search_openreview", lambda client, query, *, limit: [])
    plan = QueryPlan(crossref="c", semantic_scholar="s", openreview="or", library_raw="lib")
    assert fed.federate(plan, openreview_client=object(), library_finder=lambda query: [], observe_outcomes=True) == []
    assert {row["source"] for row in variants(plan)} == {"crossref", "semantic_scholar", "openreview", "library"}
    assert all("channel" not in row and "outcome" not in row for row in variants(plan))


def test_only_nonzero_allocated_variants_receive_receipts(monkeypatch):
    def search(query, *, per_page, semantic, outcome):
        assert (query, per_page, semantic) == ("first", 1, False)
        outcome.update(status="success_empty", count=0, error_code=None)
        return []

    plan = QueryPlan(openalex_lexical_variants=["first", "not-scheduled", "also-not-scheduled"])
    assert fed.federate(plan, openalex_client=SimpleNamespace(search_works=search), quota=1, observe_outcomes=True) == []
    assert [row["query"] for row in variants(plan)] == ["first"]
    assert variants(plan)[0]["outcome"]["status"] == "success_empty"
    assert "arxiv" not in plan.retrieval_accounting


@pytest.mark.parametrize("observe", [False, True])
def test_unexpected_channel_exception_still_propagates(monkeypatch, observe):
    def broken(query, *, max_results, **kwargs):
        raise RuntimeError("programming fault, not a provider empty result")

    monkeypatch.setattr(fed, "search_arxiv", broken)
    with pytest.raises(RuntimeError, match="programming fault"):
        fed.federate(QueryPlan(arxiv="public"), observe_outcomes=observe)
