"""Phase 1.8: OpenAlex client basics — cache, DOI normalization, error swallow."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from zotero_summarizer.integrations.openalex import OpenAlexClient
from zotero_summarizer.integrations.openalex_cache import OpenAlexCache


def _mock_response(status: int, payload: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = payload or {}
    return resp


def test_doi_normalization_strips_url_prefix(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    client = OpenAlexClient(cache, mailto="user@example.com", http_client=MagicMock())
    client._http.get.return_value = _mock_response(404)
    client.fetch_work_by_doi("https://doi.org/10.1234/ABC")
    called_url = client._http.get.call_args.args[0]
    assert called_url.endswith("/works/doi:10.1234/abc"), called_url


def test_cache_hit_skips_network(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    cached_work = {
        "id": "https://openalex.org/W1",
        "cited_by_count": 12,
        "primary_location": {"source": {"works_count": 800, "display_name": "Some Venue"}},
        "open_access": {"is_oa": True, "oa_url": "https://example.com/x.pdf"},
        "__max_author_h_index": 30,
    }
    cache.set("doi:10.1/x", cached_work)
    http = MagicMock()
    client = OpenAlexClient(cache, mailto="user@example.com", http_client=http)
    work = client.fetch_work_by_doi("10.1/x")
    http.get.assert_not_called()
    assert work is not None
    assert work.cited_by_count == 12
    assert work.venue_works_count == 800
    assert work.max_author_h_index == 30


def test_http_failure_returns_none(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.get.side_effect = httpx.ConnectError("boom")
    client = OpenAlexClient(cache, mailto="x@y.z", http_client=http)
    assert client.fetch_work_by_doi("10.1/x") is None


def test_404_returns_none(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.get.return_value = _mock_response(404)
    client = OpenAlexClient(cache, mailto="x@y.z", http_client=http)
    assert client.fetch_work_by_doi("10.1/missing") is None


def test_empty_doi_returns_none(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    client = OpenAlexClient(cache, mailto="x@y.z", http_client=http)
    assert client.fetch_work_by_doi("") is None
    assert client.fetch_work_by_doi("   ") is None
    http.get.assert_not_called()
