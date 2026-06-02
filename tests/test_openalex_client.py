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


def test_parses_field_normalized_percentile_and_fwci(tmp_path):
    """citation_normalized_percentile.value and fwci flow into the dataclass."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    cache.set("doi:10.1/x", {
        "id": "https://openalex.org/W1",
        "cited_by_count": 12,
        "citation_normalized_percentile": {"value": 0.97, "is_in_top_1_percent": False},
        "fwci": 3.4,
    })
    client = OpenAlexClient(cache, http_client=MagicMock())
    work = client.fetch_work_by_doi("10.1/x")
    assert work.citation_percentile == 0.97
    assert work.fwci == 3.4


def test_cache_only_client_never_hits_network(tmp_path):
    """allow_network=False (interactive paths): a cache MISS returns None without
    any HTTP call, but a cache HIT still resolves — so opening a paper's detail
    never blocks on a multi-second OpenAlex search."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    client = OpenAlexClient(cache, http_client=http, allow_network=False)

    # Miss → None, and NO network call.
    assert client.fetch_work_by_doi("10.1/uncached") is None
    http.get.assert_not_called()

    # Hit → resolves from cache, still no network.
    cache.set("doi:10.1/x", {"id": "https://openalex.org/W1", "cited_by_count": 7})
    work = client.fetch_work_by_doi("10.1/x")
    assert work is not None and work.cited_by_count == 7
    http.get.assert_not_called()


def test_cold_start_percentile_is_none(tmp_path):
    """A too-new / uncited work: OpenAlex returns no percentile → None (cold-start),
    so prestige stays neutral rather than flooring to 1.0."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    cache.set("doi:10.1/new", {
        "id": "https://openalex.org/W2",
        "cited_by_count": 0,
        "citation_normalized_percentile": {"value": None},
    })
    client = OpenAlexClient(cache, http_client=MagicMock())
    work = client.fetch_work_by_doi("10.1/new")
    assert work.citation_percentile is None
    assert work.fwci is None
    assert work.max_author_field_percentile is None


# ------------------------- cold-start author field-percentile enrichment


def test_cold_start_work_fetches_author_field_percentile(tmp_path):
    """A cold-start work (no percentile of its own) fetches each author's
    FIELD-normalized standing = median of the author's works' percentiles, and
    takes the max across authors."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    work_payload = {
        "id": "https://openalex.org/W9",
        "cited_by_count": 0,
        "citation_normalized_percentile": {"value": None},  # cold-start
        "authorships": [{"author": {"id": "https://openalex.org/A1"}}],
    }
    author_profile = {"summary_stats": {"h_index": 40}}
    author_works = {"results": [
        {"citation_normalized_percentile": {"value": 0.8}},
        {"citation_normalized_percentile": {"value": 0.9}},
        {"citation_normalized_percentile": {"value": None}},  # too-new work ignored
    ]}
    http.get.side_effect = [
        _mock_response(200, work_payload),
        _mock_response(200, author_profile),
        _mock_response(200, author_works),
    ]
    client = OpenAlexClient(cache, http_client=http)
    work = client.fetch_work_by_doi("10.1/cs")
    assert work.citation_percentile is None
    assert work.max_author_h_index == 40
    assert work.max_author_field_percentile == pytest.approx(0.85)  # median([0.8, 0.9])


def test_established_work_skips_author_percentile_fetch(tmp_path):
    """Cost guard: a work WITH its own percentile must NOT trigger the extra
    per-author /works calls (only the h-index lookup runs)."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    work_payload = {
        "id": "https://openalex.org/W10",
        "cited_by_count": 100,
        "citation_normalized_percentile": {"value": 0.7},  # established
        "authorships": [{"author": {"id": "https://openalex.org/A1"}}],
    }
    http.get.side_effect = [
        _mock_response(200, work_payload),
        _mock_response(200, {"summary_stats": {"h_index": 40}}),
    ]
    client = OpenAlexClient(cache, http_client=http)
    work = client.fetch_work_by_doi("10.1/established")
    assert work.citation_percentile == 0.7
    assert work.max_author_field_percentile is None
    assert http.get.call_count == 2  # work + h-index only, NO author /works call


def test_author_field_percentile_none_when_no_normalized_works(tmp_path):
    """An author whose works all lack a percentile (too new) → None, never lifted."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.get.side_effect = [
        _mock_response(200, {
            "id": "https://openalex.org/W11",
            "citation_normalized_percentile": {"value": None},
            "authorships": [{"author": {"id": "https://openalex.org/A1"}}],
        }),
        _mock_response(200, {"summary_stats": {"h_index": 5}}),
        _mock_response(200, {"results": [{"citation_normalized_percentile": {"value": None}}]}),
    ]
    client = OpenAlexClient(cache, http_client=http)
    work = client.fetch_work_by_doi("10.1/cs2")
    assert work.max_author_field_percentile is None


def test_transient_author_fetch_failure_does_not_poison_work_cache(tmp_path):
    """If the author /works call fails transiently (vs. genuinely empty), the work
    is NOT cached — so the next pass retries instead of freezing the paper at
    'no author signal' for the whole work-cache TTL."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.get.side_effect = [
        _mock_response(200, {
            "id": "https://openalex.org/W13",
            "citation_normalized_percentile": {"value": None},  # cold-start
            "authorships": [{"author": {"id": "https://openalex.org/A1"}}],
        }),
        _mock_response(200, {"summary_stats": {"h_index": 50}}),
        _mock_response(500),  # author /works transient failure
    ]
    client = OpenAlexClient(cache, http_client=http)
    work = client.fetch_work_by_doi("10.1/transient")
    assert work.max_author_field_percentile is None       # degrades gracefully now
    assert cache.get("doi:10.1/transient") is None         # but NOT cached → will retry


def test_genuine_empty_author_works_is_cached(tmp_path):
    """Contrast with the failure case: a genuinely-empty author (200 + no
    percentiled works) IS cached, so we don't re-query a known-empty author."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.get.side_effect = [
        _mock_response(200, {
            "id": "https://openalex.org/W14",
            "citation_normalized_percentile": {"value": None},
            "authorships": [{"author": {"id": "https://openalex.org/A1"}}],
        }),
        _mock_response(200, {"summary_stats": {"h_index": 3}}),
        _mock_response(200, {"results": []}),  # genuinely empty
    ]
    client = OpenAlexClient(cache, http_client=http)
    work = client.fetch_work_by_doi("10.1/empty-author")
    assert work.max_author_field_percentile is None
    assert cache.get("doi:10.1/empty-author") is not None   # cached (no retry churn)


def test_author_field_percentile_cached_payload(tmp_path):
    """A cached full payload carrying __max_author_field_percentile resolves with
    no network (the why-panel / interactive read path)."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    cache.set("doi:10.1/cs3", {
        "id": "https://openalex.org/W12",
        "citation_normalized_percentile": {"value": None},
        "__max_author_h_index": 30,
        "__max_author_field_percentile": 0.66,
    })
    http = MagicMock()
    client = OpenAlexClient(cache, http_client=http)
    work = client.fetch_work_by_doi("10.1/cs3")
    http.get.assert_not_called()
    assert work.max_author_field_percentile == 0.66
