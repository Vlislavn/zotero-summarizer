"""Request-local transport receipts; inert HTTP fixtures, not relevance judgments."""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from zotero_summarizer.integrations import arxiv, crossref, europepmc, openalex, semantic_scholar

PUBLIC_QUERY = "public topic BAD_QUERY_MARKER"
RAW_BODY = "RAW_BODY_MARKER credential=not-a-real-credential"
EMPTY = {
    "arxiv": '<feed xmlns="http://www.w3.org/2005/Atom"/>',
    "crossref": {"message": {"items": []}},
    "europepmc": {"resultList": {"result": []}},
    "semantic_scholar": {"data": []},
    "openalex": {"results": []},
}
NONEMPTY = {
    "arxiv": '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2501.12345v1</id>'
             '<title>Public paper</title><summary>Public abstract.</summary></entry></feed>',
    "crossref": {"message": {"items": [{"title": ["Public paper"], "DOI": "10.1234/fixture"}]}},
    "europepmc": {"resultList": {"result": [{"title": "Public paper", "pmid": "12345"}]}},
    "semantic_scholar": {"data": [{"title": "Public paper", "externalIds": {"DOI": "10.1234/fixture"}}]},
    "openalex": {"results": [{"id": "https://openalex.org/W1", "title": "A public paper"}]},
}


@pytest.fixture(autouse=True)
def no_rate_wait(monkeypatch):
    for module in (arxiv, crossref, europepmc, openalex, semantic_scholar):
        monkeypatch.setattr(module, "_RATE_LIMITER", SimpleNamespace(acquire=lambda: None))


def response(payload, status=200):
    if payload is None:
        return httpx.Response(status, text="null")
    if isinstance(payload, str):
        return httpx.Response(status, text=payload)
    return httpx.Response(status, json=payload)


@contextmanager
def leaf(provider, handler, *, allow_network=True):
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        if provider == "arxiv":
            yield lambda query, **kw: arxiv.search_arxiv(query, http_client=http, **kw)
        elif provider == "europepmc":
            yield lambda query, **kw: europepmc.search_europepmc(query, http_client=http, **kw)
        elif provider == "crossref":
            yield lambda query, **kw: crossref.search_crossref(query, http_client=http, **kw)
        elif provider == "semantic_scholar":
            yield lambda query, **kw: semantic_scholar.search_semantic_scholar(query, http_client=http, **kw)
        elif provider == "openalex":
            client = openalex.OpenAlexClient(object(), http_client=http, allow_network=allow_network)
            yield client.search_works
        else:
            pytest.fail(f"Unknown provider fixture: {provider}")


def assert_receipt(receipt, status, count=None, code=None):
    assert receipt == {"status": status, "count": count, "error_code": code}
    encoded = json.dumps(receipt)
    assert RAW_BODY not in encoded
    assert "BAD_QUERY_MARKER" not in encoded
    assert "https://" not in encoded


@pytest.mark.parametrize("provider", EMPTY)
@pytest.mark.parametrize("nonempty", [False, True])
def test_valid_200_empty_and_nonempty(provider, nonempty):
    payload = NONEMPTY[provider] if nonempty else EMPTY[provider]
    outcome = {}
    with leaf(provider, lambda request: response(payload)) as search:
        hits = search(PUBLIC_QUERY, outcome=outcome)
    assert len(hits) == int(nonempty)
    assert_receipt(outcome, "success_nonempty" if nonempty else "success_empty", int(nonempty))


@pytest.mark.parametrize("provider", EMPTY)
@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (404, "http_error"), (503, "http_error")])
def test_http_failure_is_not_empty(provider, status, code):
    outcome = {}
    with leaf(provider, lambda request: response(RAW_BODY, status)) as search:
        assert search(PUBLIC_QUERY, outcome=outcome) == []
    assert_receipt(outcome, "failed", code=code)


@pytest.mark.parametrize("provider", EMPTY)
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, OSError])
def test_transport_failure_is_not_empty(provider, error):
    def fail(request):
        raise error(RAW_BODY + " https://invalid.example/" + PUBLIC_QUERY)

    outcome = {}
    with leaf(provider, fail) as search:
        assert search(PUBLIC_QUERY, outcome=outcome) == []
    assert_receipt(outcome, "failed", code="transport_error")


@pytest.mark.parametrize("provider", EMPTY)
def test_undecodable_200_is_parse_failure(provider):
    outcome = {}
    with leaf(provider, lambda request: response(RAW_BODY)) as search:
        assert search(PUBLIC_QUERY, outcome=outcome) == []
    assert_receipt(outcome, "failed", code="parse_error")


@pytest.mark.parametrize("provider,payload", [
    ("arxiv", "<html/>"),
    ("arxiv", '<feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>'),
    ("crossref", {}),
    ("crossref", {"message": {"items": None}}),
    ("crossref", {"message": {"items": [{}]}}),
    ("crossref", {"message": {"items": [None]}}),
    ("europepmc", {}),
    ("europepmc", {"resultList": {"result": None}}),
    ("europepmc", {"resultList": {"result": [{}]}}),
    ("semantic_scholar", {}),
    ("semantic_scholar", {"data": None}),
    ("semantic_scholar", {"data": [{}]}),
    ("openalex", {}),
    ("openalex", []),
    ("openalex", None),
    ("openalex", {"results": None}),
    ("openalex", {"results": {}}),
    ("openalex", {"results": [None]}),
    ("openalex", {"results": [{"id": "W1", "title": ["malformed"]}]}),
    ("openalex", {"results": [{"id": "W1", "title": "ok", "authorships": [1]}]}),
    ("openalex", {"results": [{}]}),
])
def test_malformed_200_container_or_row_is_not_empty(provider, payload):
    outcome = {}
    with leaf(provider, lambda request: response(payload)) as search:
        assert search(PUBLIC_QUERY, outcome=outcome) == []
    assert_receipt(outcome, "failed", code="parse_error")


@pytest.mark.parametrize("literal", ["1e9999", "-1e9999", "12"])
def test_openalex_numeric_conversion_stays_inside_receipt_boundary(literal):
    payload = '{"results":[{"id":"W1","title":"Public work","cited_by_count":' + literal + '}]}'
    outcome = {}
    with leaf("openalex", lambda request: response(payload)) as search:
        hits = search(PUBLIC_QUERY, outcome=outcome)
        if literal == "12":
            assert len(hits) == 1 and hits[0].cited_by_count == 12
            assert_receipt(outcome, "success_nonempty", 1)
        else:
            assert hits == []
            assert_receipt(outcome, "failed", code="parse_error")
            with pytest.raises(OverflowError):
                search(PUBLIC_QUERY)


@pytest.mark.parametrize("literal", ["1e9999", "-1e9999", "2025", "null"])
def test_openalex_publication_year_cannot_publish_nonfinite_wire_values(literal):
    payload = '{"results":[{"id":"W1","title":"Public work","publication_year":' + literal + '}]}'
    outcome = {}
    with leaf("openalex", lambda request: response(payload)) as search:
        hits = search(PUBLIC_QUERY, outcome=outcome)
        if literal not in {"2025", "null"}:
            assert search(PUBLIC_QUERY)[0].year == float(literal)
    if literal in {"2025", "null"}:
        assert len(hits) == 1 and hits[0].year == json.loads(literal)
        assert_receipt(outcome, "success_nonempty", 1)
    else:
        assert hits == []
        assert_receipt(outcome, "failed", code="parse_error")


@pytest.mark.parametrize("observe", [False, True])
def test_openalex_retains_location_aliases_without_following_urls(observe):
    payload = {"results": [{"id": "W900", "title": "Different bibliography", "doi": "10.9/current",
        "locations": [
            {"doi": "https://doi.org/10.1/ALIAS/"},
            {"landing_page_url": "https://arxiv.org/abs/2501.12345v2"},
            {"pdf_url": "http://arxiv.org/pdf/2501.12345v3.pdf"},
            {"landing_page_url": "https://doi.org/10.2/PUBLISHED"},
            {"landing_page_url": "https://example.test/arxiv.org/abs/2501.99999"},
        ]}]}
    calls = []
    def provider(request):
        calls.append(request)
        return response(payload)
    outcome = {}
    with leaf("openalex", provider) as search:
        hits = search(PUBLIC_QUERY, **({"outcome": outcome} if observe else {}))
    assert len(calls) == len(hits) == 1
    assert "locations" in calls[0].url.params["select"].split(",")
    assert hits[0].family_identifier_keys == ["arxiv:2501.12345", "doi:10.1/alias", "doi:10.2/published"]
    if observe:
        assert_receipt(outcome, "success_nonempty", 1)


def test_openalex_disabled_never_attempts_http():
    def unexpected(request):
        pytest.fail("network-disabled client attempted HTTP")

    outcome = {}
    with leaf("openalex", unexpected, allow_network=False) as search:
        assert search(PUBLIC_QUERY, outcome=outcome) == []
    assert_receipt(outcome, "not_attempted", code="network_disabled")


@pytest.mark.parametrize("provider", EMPTY)
def test_receipt_can_be_reused_sequentially_without_stale_count(provider):
    responses = iter([response(NONEMPTY[provider]), response(RAW_BODY, 429)])
    outcome = {}
    with leaf(provider, lambda request: next(responses)) as search:
        assert len(search(PUBLIC_QUERY, outcome=outcome)) == 1
        assert_receipt(outcome, "success_nonempty", 1)
        assert search(PUBLIC_QUERY, outcome=outcome) == []
    assert_receipt(outcome, "failed", code="rate_limited")


@pytest.mark.parametrize("provider", EMPTY)
def test_blank_leaf_query_stays_unknown_without_http(provider):
    def unexpected(request):
        pytest.fail("blank query attempted HTTP")

    outcome = {}
    with leaf(provider, unexpected) as search:
        assert search(" ", outcome=outcome) == []
    assert_receipt(outcome, "unknown")


@pytest.mark.parametrize("provider", EMPTY)
def test_unexpected_programming_error_propagates_without_success_receipt(provider):
    def fail(request):
        raise RuntimeError("Owned programming failure")

    outcome = {}
    with leaf(provider, fail) as search:
        with pytest.raises(RuntimeError, match="Owned programming failure"):
            search(PUBLIC_QUERY, outcome=outcome)
    assert_receipt(outcome, "unknown")


def test_openalex_ordinary_search_preserves_narrow_get_override(monkeypatch):
    client = openalex.OpenAlexClient(object(), http_client=object())
    calls = []

    def old_get(path, params=None):
        calls.append((path, params))
        return NONEMPTY["openalex"]

    monkeypatch.setattr(client, "_get", old_get)
    assert len(client.search_works(PUBLIC_QUERY, semantic=True)) == 1
    assert calls[0][0] == "/works"
    assert calls[0][1]["search.semantic"] == PUBLIC_QUERY


def test_nonsearch_get_retains_legacy_status_and_payload_policy():
    payloads = iter([response({"legacy": True}, 302), response([], 200), response(RAW_BODY, 404)])
    with httpx.Client(transport=httpx.MockTransport(lambda request: next(payloads)), trust_env=False) as http:
        client = openalex.OpenAlexClient(object(), http_client=http)
        assert client._get("/works") == {"legacy": True}
        assert client._get("/works") == []
        assert client._get("/works") is None


@pytest.mark.parametrize("provider", EMPTY)
def test_default_leaf_still_best_effort_for_http_and_decode_errors(provider):
    payloads = iter([response(RAW_BODY, 429), response(RAW_BODY)])
    with leaf(provider, lambda request: next(payloads)) as search:
        assert search(PUBLIC_QUERY) == []
        assert search(PUBLIC_QUERY) == []


@pytest.mark.parametrize("provider", ["europepmc", "openalex"])
def test_default_leaf_preserves_existing_malformed_row_exception(provider):
    malformed = {"title": ["not a string"], "id": "W1"}
    payload = {"results": [malformed]} if provider == "openalex" else {"resultList": {"result": [malformed]}}
    with leaf(provider, lambda request: response(payload)) as search:
        with pytest.raises(AttributeError):
            search(PUBLIC_QUERY)
