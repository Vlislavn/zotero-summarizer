"""Phase 1.8: PDF URL resolution priority + Unpaywall client basics."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
from zotero_summarizer.integrations.pdf_fetch import resolve_pdf_url
from zotero_summarizer.integrations.unpaywall import UnpaywallClient


def _mock_response(status: int, payload: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = payload or {}
    return resp


def test_arxiv_id_takes_priority_over_unpaywall(tmp_path):
    """arXiv URLs are direct PDF and free; never consult Unpaywall when present."""
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    unpaywall = UnpaywallClient(cache, email="x@y.z", http_client=http)
    url = resolve_pdf_url(
        doi="10.1234/abc",
        arxiv_id="2401.12345",
        url=None,
        unpaywall=unpaywall,
    )
    assert url == "https://arxiv.org/pdf/2401.12345.pdf"
    http.get.assert_not_called()


def test_arxiv_id_from_url_when_no_dedicated_field(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    url = resolve_pdf_url(
        doi=None,
        arxiv_id=None,
        url="https://arxiv.org/abs/2403.04567",
        unpaywall=None,
    )
    assert url == "https://arxiv.org/pdf/2403.04567.pdf"


def test_unpaywall_used_when_no_arxiv(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.get.return_value = _mock_response(
        200, {"best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"}}
    )
    unpaywall = UnpaywallClient(cache, email="x@y.z", http_client=http)
    url = resolve_pdf_url(doi="10.1/x", arxiv_id=None, url=None, unpaywall=unpaywall)
    assert url == "https://example.com/paper.pdf"


def test_no_source_returns_none(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    unpaywall = UnpaywallClient(cache, email="x@y.z", http_client=MagicMock())
    assert resolve_pdf_url(doi=None, arxiv_id=None, url=None, unpaywall=unpaywall) is None


def test_unpaywall_without_email_returns_none(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    unpaywall = UnpaywallClient(cache, email="", http_client=http)
    assert unpaywall.find_oa_pdf_url("10.1/x") is None
    http.get.assert_not_called()


def test_unpaywall_404_caches_negative(tmp_path):
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.get.return_value = _mock_response(404)
    unpaywall = UnpaywallClient(cache, email="x@y.z", http_client=http)
    assert unpaywall.find_oa_pdf_url("10.1/x") is None
    # Second call should be served from cache, not the network.
    http.get.reset_mock()
    assert unpaywall.find_oa_pdf_url("10.1/x") is None
    http.get.assert_not_called()
