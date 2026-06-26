"""Unit tests for ``integrations.pubmed`` — PMID/DOI → PMC full-text PDF URL.

No network: the ID-Converter HTTP call is stubbed. Covers PMID extraction, the
happy-path PMCID→URL, caching (no second network call), the cache-only mode, and
the not-in-PMC / no-PMCID cases.
"""
from __future__ import annotations

from zotero_summarizer.integrations import pubmed


class _FakeCache:
    def __init__(self) -> None:
        self.d: dict[str, dict] = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp = resp
        self.calls: list = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return self._resp


def test_pmid_from_url():
    assert pubmed._pmid_from_url("https://pubmed.ncbi.nlm.nih.gov/42317861/") == "42317861"
    assert pubmed._pmid_from_url("https://arxiv.org/abs/2401.00001") == ""
    assert pubmed._pmid_from_url("") == ""


def test_resolve_pmc_url_happy_and_cached():
    cache = _FakeCache()
    client = _Client(_Resp(200, {"records": [{"pmid": "42317861", "pmcid": "PMC13274294"}]}))
    url = pubmed.resolve_pmc_pdf_url(cache=cache, pmid="42317861", http_client=client)
    assert url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC13274294/pdf/"
    assert len(client.calls) == 1
    # Second call hits the cache → no new network request.
    again = pubmed.resolve_pmc_pdf_url(cache=cache, pmid="42317861", http_client=client)
    assert again == url and len(client.calls) == 1


def test_resolve_pmc_url_falls_back_to_doi():
    cache = _FakeCache()
    client = _Client(_Resp(200, {"records": [{"doi": "10.1/x", "pmcid": "PMC42"}]}))
    url = pubmed.resolve_pmc_pdf_url(cache=cache, doi="10.1/X", http_client=client)
    assert url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC42/pdf/"
    assert client.calls[0][1]["ids"] == "10.1/x"  # normalized DOI passed as the id


def test_not_in_pmc_returns_none():
    cache = _FakeCache()
    client = _Client(_Resp(200, {"records": [{"pmid": "1", "pmcid": ""}]}))
    assert pubmed.resolve_pmc_pdf_url(cache=cache, pmid="1", http_client=client) is None
    # The negative is cached so we don't re-query the not-in-PMC paper.
    assert cache.get("pmc:1") == {"pmcid": ""}


def test_cache_only_mode_no_network():
    client = _Client(_Resp(200, {"records": [{"pmid": "1", "pmcid": "PMC9"}]}))
    out = pubmed.resolve_pmc_pdf_url(cache=_FakeCache(), pmid="1", http_client=client, allow_network=False)
    assert out is None and client.calls == []


def test_http_error_degrades_to_none():
    cache = _FakeCache()
    client = _Client(_Resp(503, None))
    assert pubmed.resolve_pmc_pdf_url(cache=cache, pmid="1", http_client=client) is None
